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


def token_recall(gold: str, prediction: str) -> float:
    """What fraction of gold answer tokens appear in prediction?

    Better than F1 for retrieval eval: doesn't penalize for retrieving
    extra context (low precision). Measures "can the answer be found?"
    """
    g = _normalize(gold)
    p = _normalize(prediction)
    if not g:
        return 1.0
    if not p:
        return 0.0
    common = Counter(g) & Counter(p)
    num_same = sum(common.values())
    return num_same / len(g)


# ── Runner ─────────────────────────────────────────────────────────────────


def _node_text(node: Node) -> str:
    parts = [node.id, node.type]
    for v in node.props.values():
        parts.append(str(v))
    if node.valid_from:
        parts.append(node.valid_from.isoformat())
    if node.valid_to:
        parts.append(node.valid_to.isoformat())
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
    if edge.valid_from:
        parts.append(edge.valid_from.isoformat())
    if edge.valid_to:
        parts.append(edge.valid_to.isoformat())
    return " ".join(parts)


def _load_src_text(src_refs: tuple[str, ...], raw_dir: Path | None) -> str:
    """Read full source markdown files referenced by src (path:line format)."""
    if not raw_dir or not src_refs:
        return ""
    seen_files: set[str] = set()
    parts: list[str] = []
    for ref in src_refs:
        path_str = ref.rpartition(":")[0]
        if path_str in seen_files:
            continue
        path = raw_dir / path_str
        if not path.exists():
            continue
        seen_files.add(path_str)
        try:
            parts.append(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if len(seen_files) >= 5:
            break
    return " ".join(parts)


def _search_raw_text(keywords: list[str], raw_dir: Path | None, limit: int = 3) -> str:
    """Direct keyword search over raw markdown files (graph-guided fallback)."""
    if not raw_dir or not keywords:
        return ""
    parts: list[str] = []
    count = 0
    for md_file in sorted(raw_dir.rglob("*.md")):
        try:
            text = md_file.read_text(encoding="utf-8")
        except Exception:
            continue
        lower = text.lower()
        if any(kw.lower() in lower for kw in keywords):
            parts.append(text)
            count += 1
            if count >= limit:
                break
    return " ".join(parts)


def _extract_keywords(question: str) -> list[str]:
    """Extract proper nouns + key content words (one search per keyword)."""
    tokens = question.replace("?", "").replace(".", "").replace("!", "").split()
    common_caps = {
        "What", "When", "Where", "Who", "Why", "How", "Did", "Do", "Does",
        "Was", "Were", "Is", "Are", "The", "A", "An", "I", "My", "Me",
        "Would", "Could", "Will", "Have", "Has", "Had", "They", "Them",
        "Their", "Her", "His", "Its", "This", "That", "These", "Those",
        "Which",
    }
    keywords = [t for t in tokens if t and t[0].isupper() and t not in common_caps]
    stop = {"what", "when", "where", "who", "why", "how", "did", "do", "does",
            "was", "were", "is", "are", "the", "a", "an", "to", "of", "in",
            "on", "at", "for", "from", "about", "after", "before", "during",
            "and", "or", "not", "no", "yes", "can", "could", "would", "will",
            "have", "has", "had", "been", "being", "that", "this", "these",
            "those", "it", "they", "them", "their", "her", "his", "its",
            "ago", "long", "much", "many", "old", "new", "first", "last"}
    content_words = [t.lower() for t in tokens
                     if t.lower() not in stop and len(t) > 2
                     and t not in common_caps and not t[0].isupper()]
    keywords.extend(content_words[:3])
    return keywords[:8]


def answer_question(
    scoped: ScopedGraph,
    store: GraphStore,
    question: dict,
    raw_dir: Path | None = None,
) -> dict:
    """Retrieve facts for a question and score against gold answer.

    Uses graph-guided retrieval: search → get_node → neighbors(depth=1-2).
    Enriches fact text with source markdown from ``src`` references.
    """
    cat = question["category"]
    keywords = _extract_keywords(question["question"])
    all_node_ids: set[str] = set()
    for kw in keywords:
        ids = scoped.search(kw, limit=5)
        all_node_ids.update(ids)

    depth = 2 if cat == 3 else 1
    fact_text_parts: list[str] = []
    seen_src: set[str] = set()

    for nid in sorted(all_node_ids):
        node = scoped.get_node(nid)
        if not node:
            continue
        fact_text_parts.append(_node_text(node))
        if not question["adversarial"]:
            for ref in node.src:
                seen_src.add(ref)

        res = scoped.neighbors(nid, depth=depth)
        for n in res["nodes"]:
            fact_text_parts.append(_node_text(n))
            if not question["adversarial"]:
                for ref in n.src:
                    seen_src.add(ref)
        for e in res["edges"]:
            fact_text_parts.append(_edge_text(e, store))
            if not question["adversarial"]:
                for ref in e.src:
                    seen_src.add(ref)

    if not question["adversarial"]:
        src_text = _load_src_text(tuple(seen_src), raw_dir)
        if src_text:
            fact_text_parts.append(src_text)
        raw_hits = _search_raw_text(keywords, raw_dir)
        if raw_hits:
            fact_text_parts.append(raw_hits)

    fact_text = " ".join(fact_text_parts)

    if question["adversarial"]:
        score = 1.0 - token_recall(question["gold"], fact_text)
    else:
        score = token_recall(question["gold"], fact_text)

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


def locomo_report(
    graph_dir: Path, json_path: Path, allowed_ns: list[str],
    raw_dir: Path | None = None,
) -> dict:
    """Run LoCoMo QA eval against a compiled graph.

    If ``raw_dir`` is given, source markdown lines are included in the
    fact text for each retrieved node/edge (graph-guided retrieval).

    Returns per-category F1 + overall metrics.
    """
    facts_path = Path(graph_dir) / "facts.jsonl"
    if not facts_path.exists():
        return {"error": f"facts.jsonl not found at {facts_path}"}

    store = GraphStore.from_jsonl(facts_path)
    scoped = ScopedGraph(store, allowed_ns)
    questions = extract_questions(json_path)

    results = [answer_question(scoped, store, q, raw_dir=raw_dir) for q in questions]

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
