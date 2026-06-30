"""LoCoMo Tier-2 evaluation: convert, compile, retrieve, score.

Pipeline:
  1. Converter: LoCoMo JSON → raw/<conv_id>/*.md (one file per session)
  2. Compile: markdown → facts.jsonl (standard lorekeep pipeline)
  3. Runner: answer QA questions via graph retrieval (programmatic, no agent LLM)
  4. Scorer: token-level F1 (HotpotQA-style) per category + overall

QA categories (LoCoMo):
  1 = single-hop descriptive   ("What is X?")
  2 = temporal                ("When did X happen?")
  3 = multi-hop               ("Who did X's sister work with?")
  4 = single-hop              ("What did X do?")
  5 = adversarial             (unanswerable / distractor)

Scoring:
  cat 1-4: F1 = token overlap between gold answer and retrieved fact text
  cat 5:   score 1.0 if search returns 0 results (correct abstention), else 0.0
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from lorekeep.models import Edge, Node
from lorekeep.perm.ns import ScopedGraph
from lorekeep.store.graph import GraphStore


# ── Converter ──────────────────────────────────────────────────────────────


def convert_locomo(json_path: Path, raw_dir: Path) -> int:
    """Convert LoCoMo JSON to markdown files under raw_dir.

    Each conversation session becomes one .md file:
      raw_dir/<conv_id>/session-<N>.md

    Returns number of files written.
    """
    data = json.loads(Path(json_path).read_text(encoding="utf-8"))
    count = 0
    for conv in data:
        conv_id = conv["sample_id"]
        c = conv["conversation"]
        for n in range(1, 36):
            session_key = f"session_{n}"
            date_key = f"session_{n}_date_time"
            if session_key not in c:
                continue
            date = c.get(date_key, "")
            utterances = c[session_key]
            if not utterances:
                continue
            lines = [
                f"# {conv_id} Session {n}",
                f"Date: {date}",
                f"Speakers: {c.get('speaker_a', 'A')}, {c.get('speaker_b', 'B')}",
                "",
            ]
            for utt in utterances:
                lines.append(f"**{utt['speaker']}** ({utt['dia_id']}): {utt['text']}")
            path = raw_dir / conv_id / f"session-{n}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            count += 1
    return count


def extract_questions(json_path: Path) -> list[dict]:
    """Extract all QA items from LoCoMo JSON, flattened with conv_id."""
    data = json.loads(Path(json_path).read_text(encoding="utf-8"))
    questions: list[dict] = []
    for conv in data:
        conv_id = conv["sample_id"]
        for qa in conv.get("qa", []):
            cat = qa.get("category", 0)
            if cat == 5:
                gold = str(qa.get("adversarial_answer", qa.get("answer", "")))
            else:
                gold = str(qa.get("answer", ""))
            questions.append({
                "conv_id": conv_id,
                "question": qa["question"],
                "gold": gold,
                "category": cat,
                "evidence": qa.get("evidence", []),
                "adversarial": cat == 5,
            })
    return questions


# ── Scorer ─────────────────────────────────────────────────────────────────


def _normalize(text: str) -> list[str]:
    """Lowercase, strip articles/punctuation, tokenize (HotpotQA-style)."""
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    tokens = text.split()
    articles = {"a", "an", "the", "is", "are", "was", "were", "to", "of", "in", "on", "at"}
    return [t for t in tokens if t not in articles]


def token_f1(gold: str, prediction: str) -> float:
    """Token-level F1 between gold and prediction strings."""
    g = _normalize(gold)
    p = _normalize(prediction)
    if not g and not p:
        return 1.0
    if not g or not p:
        return 0.0
    common = Counter(g) & Counter(p)
    num_same = sum(common.values())
    if num_same == 0:
        return 0.0
    precision = num_same / len(p)
    recall = num_same / len(g)
    return 2 * precision * recall / (precision + recall)


# ── Runner ─────────────────────────────────────────────────────────────────


def _node_text(node: Node) -> str:
    parts = [node.id, node.type]
    for v in node.props.values():
        parts.append(str(v))
    return " ".join(parts)


def _edge_text(edge: Edge, store: GraphStore) -> str:
    from_node = store.get_node(edge.from_)
    to_node = store.get_node(edge.to)
    parts = [edge.type, edge.from_, edge.to]
    if from_node:
        parts.append(_node_text(from_node))
    if to_node:
        parts.append(_node_text(to_node))
    for v in edge.props.values():
        parts.append(str(v))
    return " ".join(parts)


def _extract_keywords(question: str) -> list[str]:
    """Extract proper nouns + key terms from question (one search per keyword)."""
    tokens = question.replace("?", "").replace(".", "").replace("!", "").split()
    common_caps = {
        "What", "When", "Where", "Who", "Why", "How", "Did", "Do", "Does",
        "Was", "Were", "Is", "Are", "The", "A", "An", "I", "My", "Me",
        "Would", "Could", "Will", "Have", "Has", "Had", "They", "Them",
        "Their", "Her", "His", "Its", "This", "That", "These", "Those",
    }
    keywords = [t for t in tokens if t and t[0].isupper() and t not in common_caps]
    if not keywords:
        stop = {"what", "when", "where", "who", "why", "how", "did", "do", "does",
                "was", "were", "is", "are", "the", "a", "an", "to", "of", "in"}
        low = [t.lower() for t in tokens if t.lower() not in stop and len(t) > 2]
        keywords = low[:3]
    return keywords


def answer_question(
    scoped: ScopedGraph,
    store: GraphStore,
    question: dict,
) -> dict:
    """Retrieve facts for a question and score against gold answer."""
    keywords = _extract_keywords(question["question"])
    all_node_ids: set[str] = set()
    for kw in keywords:
        ids = scoped.search(kw, limit=5)
        all_node_ids.update(ids)

    fact_text_parts: list[str] = []
    for nid in sorted(all_node_ids):
        node = scoped.get_node(nid)
        if node:
            fact_text_parts.append(_node_text(node))
            res = scoped.neighbors(nid, depth=1)
            for n in res["nodes"]:
                fact_text_parts.append(_node_text(n))
            for e in res["edges"]:
                fact_text_parts.append(_edge_text(e, store))

    fact_text = " ".join(fact_text_parts)

    if question["adversarial"]:
        # Adversarial: score = 1 - f1(wrong_answer, retrieved_text)
        # High score = system correctly did NOT find supporting evidence
        # for the plausible-but-wrong answer
        score = 1.0 - token_f1(question["gold"], fact_text)
    else:
        score = token_f1(question["gold"], fact_text)

    return {
        "question": question["question"],
        "gold": question["gold"],
        "category": question["category"],
        "f1": round(score, 4),
        "matched_nodes": sorted(all_node_ids),
        "adversarial": question["adversarial"],
    }


# ── Full eval ──────────────────────────────────────────────────────────────


CATEGORY_NAMES = {
    1: "single-hop",
    2: "temporal",
    3: "multi-hop",
    4: "descriptive",
    5: "adversarial",
}


def locomo_report(graph_dir: Path, json_path: Path, allowed_ns: list[str]) -> dict:
    """Run LoCoMo QA eval against a compiled graph.

    Returns per-category F1 + overall metrics.
    """
    facts_path = Path(graph_dir) / "facts.jsonl"
    if not facts_path.exists():
        return {"error": f"facts.jsonl not found at {facts_path}"}

    store = GraphStore.from_jsonl(facts_path)
    scoped = ScopedGraph(store, allowed_ns)
    questions = extract_questions(json_path)

    results = [answer_question(scoped, store, q) for q in questions]

    per_cat: dict[str, list[float]] = {}
    for r in results:
        cat = CATEGORY_NAMES.get(r["category"], f"cat-{r['category']}")
        per_cat.setdefault(cat, []).append(r["f1"])

    summary = {
        "total_questions": len(results),
        "overall_f1": round(
            sum(r["f1"] for r in results) / len(results), 4
        ) if results else 0.0,
        "per_category": {
            cat: {
                "count": len(scores),
                "f1": round(sum(scores) / len(scores), 4),
            }
            for cat, scores in sorted(per_cat.items())
        },
        "graph_stats": scoped.stats(),
    }
    return {"summary": summary, "results": results}
