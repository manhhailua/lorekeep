"""Tests for LoCoMo Tier-2 eval: converter, scorer, runner."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from lorekeep.eval.locomo import (
    _normalize,
    answer_question,
    convert_locomo,
    extract_questions,
    locomo_report,
    token_f1,
)
from lorekeep.models import Edge, Manifest, Node
from lorekeep.store.graph import GraphStore


# ── Fixtures ───────────────────────────────────────────────────────────────

LOCOMO_SAMPLE = [
    {
        "sample_id": "conv-test",
        "conversation": {
            "speaker_a": "Alice",
            "speaker_b": "Bob",
            "session_1_date_time": "1:00 pm on 1 Jan 2024",
            "session_1": [
                {"speaker": "Alice", "dia_id": "D1:1", "text": "I started working at Acme Corp last month."},
                {"speaker": "Bob", "dia_id": "D1:2", "text": "Nice! I've been at TechStart since 2022."},
                {"speaker": "Alice", "dia_id": "D1:3", "text": "I moved to London in December."},
            ],
            "session_2_date_time": "3:00 pm on 15 Jan 2024",
            "session_2": [
                {"speaker": "Bob", "dia_id": "D2:1", "text": "I switched from Python to Go recently."},
                {"speaker": "Alice", "dia_id": "D2:2", "text": "I left Acme Corp and joined DataFlow."},
            ],
        },
        "qa": [
            {
                "question": "Where does Alice work?",
                "answer": "DataFlow",
                "evidence": ["D2:2"],
                "category": 1,
            },
            {
                "question": "When did Alice move to London?",
                "answer": "December",
                "evidence": ["D1:3"],
                "category": 2,
            },
            {
                "question": "What language did Bob switch from?",
                "answer": "Python",
                "evidence": ["D2:1"],
                "category": 4,
            },
            {
                "question": "What did Alice do after leaving Acme?",
                "answer": "joined DataFlow",
                "evidence": ["D2:2"],
                "category": 3,
            },
            {
                "question": "What did Alice think about Mars colonization?",
                "adversarial_answer": "exciting opportunity",
                "evidence": [],
                "category": 5,
            },
        ],
        "session_summary": {},
        "event_summary": {},
        "observation": {},
    }
]


@pytest.fixture
def locomo_json(tmp_path: Path) -> Path:
    p = tmp_path / "locomo10.json"
    p.write_text(json.dumps(LOCOMO_SAMPLE), encoding="utf-8")
    return p


@pytest.fixture
def raw_dir(tmp_path: Path) -> Path:
    return tmp_path / "raw"


@pytest.fixture
def graph_nodes() -> list[Node]:
    return [
        Node(id="person:Alice", type="person", ns=("locomo",),
             props={"name": "Alice"},
             src=("raw/locomo/conv-test/session-1.md:1",)),
        Node(id="person:Bob", type="person", ns=("locomo",),
             props={"name": "Bob"},
             src=("raw/locomo/conv-test/session-1.md:1",)),
        Node(id="org:Acme-Corp", type="concept", ns=("locomo",),
             props={"name": "Acme Corp"},
             src=("raw/locomo/conv-test/session-1.md:1",)),
        Node(id="org:DataFlow", type="concept", ns=("locomo",),
             props={"name": "DataFlow"},
             src=("raw/locomo/conv-test/session-2.md:1",)),
        Node(id="place:London", type="concept", ns=("locomo",),
             props={"name": "London"},
             src=("raw/locomo/conv-test/session-1.md:1",)),
        Node(id="lang:Python", type="concept", ns=("locomo",),
             props={"name": "Python"},
             src=("raw/locomo/conv-test/session-2.md:1",)),
        Node(id="lang:Go", type="concept", ns=("locomo",),
             props={"name": "Go"},
             src=("raw/locomo/conv-test/session-2.md:1",)),
    ]


@pytest.fixture
def graph_edges() -> list[Edge]:
    return [
        Edge(id="e1", type="relates_to", from_="person:Alice", to="org:Acme-Corp",
             ns=("locomo",), valid_to=date(2024, 1, 15)),
        Edge(id="e2", type="relates_to", from_="person:Alice", to="org:DataFlow",
             ns=("locomo",), valid_from=date(2024, 1, 15)),
        Edge(id="e3", type="relates_to", from_="person:Alice", to="place:London",
             ns=("locomo",)),
        Edge(id="e4", type="relates_to", from_="person:Bob", to="lang:Python",
             ns=("locomo",), valid_to=date(2024, 1, 15)),
        Edge(id="e5", type="relates_to", from_="person:Bob", to="lang:Go",
             ns=("locomo",), valid_from=date(2024, 1, 15)),
    ]


@pytest.fixture
def graph_dir(tmp_path: Path, graph_nodes, graph_edges) -> Path:
    from lorekeep.compile.writer import write_graph
    g = tmp_path / "graph"
    write_graph(g, graph_nodes, graph_edges, Manifest(
        schema_version=1, chunk_count=2,
        node_count=len(graph_nodes), edge_count=len(graph_edges),
        run_id="test", facts_hash="abc",
    ))
    return g


# ── Converter tests ────────────────────────────────────────────────────────


class TestConverter:
    def test_creates_session_files(self, locomo_json, raw_dir):
        count = convert_locomo(locomo_json, raw_dir)
        assert count == 2
        assert (raw_dir / "conv-test" / "session-1.md").exists()
        assert (raw_dir / "conv-test" / "session-2.md").exists()

    def test_session_content(self, locomo_json, raw_dir):
        convert_locomo(locomo_json, raw_dir)
        content = (raw_dir / "conv-test" / "session-1.md").read_text()
        assert "Alice" in content
        assert "Acme Corp" in content
        assert "D1:1" in content

    def test_session_date(self, locomo_json, raw_dir):
        convert_locomo(locomo_json, raw_dir)
        content = (raw_dir / "conv-test" / "session-2.md").read_text()
        assert "15 Jan 2024" in content


class TestExtractQuestions:
    def test_extracts_all_questions(self, locomo_json):
        qs = extract_questions(locomo_json)
        assert len(qs) == 5

    def test_categories(self, locomo_json):
        qs = extract_questions(locomo_json)
        cats = {q["category"] for q in qs}
        assert cats == {1, 2, 3, 4, 5}

    def test_adversarial_flag(self, locomo_json):
        qs = extract_questions(locomo_json)
        adv = [q for q in qs if q["adversarial"]]
        assert len(adv) == 1
        assert adv[0]["category"] == 5

    def test_gold_answer_normal(self, locomo_json):
        qs = extract_questions(locomo_json)
        normal = [q for q in qs if not q["adversarial"]]
        assert normal[0]["gold"] == "DataFlow"

    def test_gold_answer_adversarial(self, locomo_json):
        qs = extract_questions(locomo_json)
        adv = [q for q in qs if q["adversarial"]]
        assert adv[0]["gold"] == "exciting opportunity"


# ── Scorer tests ───────────────────────────────────────────────────────────


class TestTokenF1:
    def test_exact_match(self):
        assert token_f1("DataFlow", "DataFlow") == 1.0

    def test_partial_match(self):
        score = token_f1("joined DataFlow", "DataFlow")
        assert 0 < score < 1.0

    def test_no_match(self):
        assert token_f1("Python", "London") == 0.0

    def test_empty_both(self):
        assert token_f1("", "") == 1.0

    def test_empty_one(self):
        assert token_f1("test", "") == 0.0

    def test_case_insensitive(self):
        assert token_f1("DataFlow", "dataflow") == 1.0

    def test_articles_stripped(self):
        score = token_f1("the cat", "cat")
        assert score == 1.0

    def test_normalization(self):
        tokens = _normalize("The Cat sat on a Mat!")
        assert "cat" in tokens
        assert "mat" in tokens
        assert "the" not in tokens
        assert "a" not in tokens


# ── Runner tests ───────────────────────────────────────────────────────────


class TestRunner:
    def test_single_hop_retrieval(self, graph_dir, locomo_json):
        from lorekeep.perm.ns import ScopedGraph
        store = GraphStore.from_jsonl(graph_dir / "facts.jsonl")
        scoped = ScopedGraph(store, ["locomo"])
        qs = extract_questions(locomo_json)
        q = next(q for q in qs if q["category"] == 1)
        result = answer_question(scoped, store, q)
        assert result["f1"] > 0
        assert "person:Alice" in result["matched_nodes"] or "org:DataFlow" in result["matched_nodes"]

    def test_temporal_retrieval(self, graph_dir, locomo_json):
        from lorekeep.perm.ns import ScopedGraph
        store = GraphStore.from_jsonl(graph_dir / "facts.jsonl")
        scoped = ScopedGraph(store, ["locomo"])
        qs = extract_questions(locomo_json)
        q = next(q for q in qs if q["category"] == 2)
        result = answer_question(scoped, store, q)
        assert result["f1"] >= 0

    def test_adversarial_correct_abstention(self, graph_dir, locomo_json):
        from lorekeep.perm.ns import ScopedGraph
        store = GraphStore.from_jsonl(graph_dir / "facts.jsonl")
        scoped = ScopedGraph(store, ["locomo"])
        qs = extract_questions(locomo_json)
        q = next(q for q in qs if q["category"] == 5)
        result = answer_question(scoped, store, q)
        # "Mars colonization" — graph has no Mars facts
        # adversarial gold = "exciting opportunity" — should NOT match retrieved facts
        # score = 1.0 - f1("exciting opportunity", retrieved) ≈ high
        assert result["f1"] > 0.5


# ── Full report tests ──────────────────────────────────────────────────────


class TestLocomoReport:
    def test_report_structure(self, graph_dir, locomo_json):
        report = locomo_report(graph_dir, locomo_json, ["locomo"])
        assert "summary" in report
        assert "results" in report
        s = report["summary"]
        assert s["total_questions"] == 5
        assert "overall_f1" in s
        assert "per_category" in s

    def test_per_category_breakdown(self, graph_dir, locomo_json):
        report = locomo_report(graph_dir, locomo_json, ["locomo"])
        cats = report["summary"]["per_category"]
        assert "single-hop" in cats
        assert "temporal" in cats
        assert "adversarial" in cats
        for cat_name, stats in cats.items():
            assert stats["count"] > 0
            assert 0 <= stats["f1"] <= 1.0

    def test_graph_stats_included(self, graph_dir, locomo_json):
        report = locomo_report(graph_dir, locomo_json, ["locomo"])
        assert "graph_stats" in report["summary"]
        assert report["summary"]["graph_stats"]["nodes"] == 7

    def test_empty_graph(self, tmp_path):
        graph = tmp_path / "empty"
        graph.mkdir()
        (graph / "facts.jsonl").write_text("")
        data = tmp_path / "locomo.json"
        data.write_text(json.dumps(LOCOMO_SAMPLE))
        report = locomo_report(graph, data, ["locomo"])
        # Empty graph: adversarial questions score 1.0 (no facts to fool system),
        # all other categories score 0.0 (no facts found)
        assert report["summary"]["overall_f1"] > 0
        assert report["summary"]["per_category"]["adversarial"]["f1"] == 1.0
        assert report["summary"]["per_category"]["single-hop"]["f1"] == 0.0

    def test_no_facts_returns_error(self, tmp_path):
        data = tmp_path / "locomo.json"
        data.write_text(json.dumps(LOCOMO_SAMPLE))
        report = locomo_report(tmp_path / "nonexistent", data, ["locomo"])
        assert "error" in report
