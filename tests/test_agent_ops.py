"""Tests for agent.py: lint(), suggest(), agent_status() — pure graph logic."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lorekeep.agent import (
    LintReport,
    SuggestionReport,
    StatusDashboard,
    lint,
    suggest,
    agent_status,
)
from lorekeep.models import Edge, Node
from lorekeep.store.graph import GraphStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _store(nodes: list[Node], edges: list[Edge] | None = None) -> GraphStore:
    return GraphStore(nodes, edges or [])


def _node(id: str, type: str = "service", ns: tuple = ("team",),
          props: dict | None = None, src: list[str] | None = None,
          valid_from: str | None = "2024-01-01") -> Node:
    p = {"name": id.split(":")[-1]}
    if props:
        p.update(props)
    return Node(id=id, type=type, ns=ns, props=p, src=src or ["doc.md"],
                valid_from=valid_from)


def _edge(id: str, from_: str, to: str, type: str = "depends_on",
          ns: tuple = ("team",)) -> Edge:
    return Edge(id=id, type=type, from_=from_, to=to, ns=ns,
                valid_from="2024-01-01")


# ---------------------------------------------------------------------------
# LintReport dataclass properties
# ---------------------------------------------------------------------------

class TestLintReport:
    def test_has_issues_false_when_empty(self):
        r = LintReport()
        assert not r.has_issues

    def test_has_issues_true_with_orphans(self):
        r = LintReport(orphans=["n1"])
        assert r.has_issues

    def test_issue_count_zero_when_empty(self):
        r = LintReport()
        assert r.issue_count == 0

    def test_issue_count_sums_all_categories(self):
        r = LintReport(
            orphans=["a", "b"],
            stale=["c"],
            missing_endpoints=[{"edge_id": "e1", "missing": ["n1"]}],
            coverage_gaps=["ns1"],
            contradictions=[{"id": "x"}],
        )
        assert r.issue_count == 6


# ---------------------------------------------------------------------------
# lint()
# ---------------------------------------------------------------------------

class TestLint:
    def test_clean_graph_no_issues(self):
        n1 = _node("svc:a")
        n2 = _node("svc:b")
        e = _edge("e1", "svc:a", "svc:b")
        store = _store([n1, n2], [e])
        r = lint(store)
        assert not r.has_issues

    def test_orphan_node_detected(self):
        n1 = _node("svc:a")
        n2 = _node("svc:orphan")  # no edges
        store = _store([n1, n2], [])
        r = lint(store)
        assert "svc:orphan" in r.orphans
        assert "svc:a" in r.orphans  # also orphan (no edges)

    def test_missing_endpoint_detected(self):
        """lint detects edges referencing non-existent node IDs.

        Note: GraphStore auto-adds endpoint IDs from edges, so this case
        requires a node that appears in an edge's from_/to but was never
        added as a Node. Since GraphStore adds it to networkx automatically,
        we test the logic at the lint level by crafting a store where the
        edge target was never explicitly added.
        """
        # This is a known limitation: GraphStore.add_edge auto-creates
        # endpoint nodes, so missing_endpoints detection requires a
        # different graph representation. Skip for now.
        pytest.skip("GraphStore auto-adds edge endpoints — missing_endpoints needs store refactor")

    def test_stale_edge_detected(self):
        from datetime import date, timedelta
        n1 = _node("svc:a")
        n2 = _node("svc:b")
        past = (date.today() - timedelta(days=30)).isoformat()
        e = _edge("e1", "svc:a", "svc:b")
        e = Edge(id="e1", type="depends_on", from_="svc:a", to="svc:b",
                 ns=("team",), valid_from="2024-01-01", valid_to=past)
        store = _store([n1, n2], [e])
        r = lint(store)
        assert "e1" in r.stale

    def test_coverage_gap_detected(self):
        # One namespace has 10 nodes, another has 1 — the small one is a gap
        big_ns = [_node(f"svc:{i}", ns=("big",)) for i in range(10)]
        small_ns = [_node("svc:solo", ns=("small",))]
        store = _store(big_ns + small_ns, [])
        r = lint(store)
        assert "small" in r.coverage_gaps
        assert "big" not in r.coverage_gaps

    def test_no_coverage_gap_when_uniform(self):
        nodes = [_node(f"svc:{i}", ns=("team",)) for i in range(5)]
        store = _store(nodes, [])
        r = lint(store)
        assert len(r.coverage_gaps) == 0

    def test_contradiction_not_detected_with_same_id(self):
        """GraphStore deduplicates by node ID — same-id nodes can't coexist."""
        n1 = _node("svc:a", props={"version": "1"})
        n2 = _node("svc:a", props={"version": "2"})  # overwrites n1
        store = _store([n1, n2], [])
        r = lint(store)
        # GraphStore.add_node with same id replaces — only one node exists
        assert len(r.contradictions) == 0

    def test_empty_graph_no_issues(self):
        store = _store([], [])
        r = lint(store)
        assert not r.has_issues


# ---------------------------------------------------------------------------
# suggest()
# ---------------------------------------------------------------------------

class TestSuggest:
    def test_under_sourced_detected(self):
        n = _node("svc:a", src=["only.md"])
        store = _store([n], [])
        r = suggest(store)
        assert "svc:a" in r.under_sourced

    def test_well_sourced_not_flagged(self):
        n = _node("svc:a", src=["doc1.md", "doc2.md", "doc3.md"])
        store = _store([n], [])
        r = suggest(store)
        assert "svc:a" not in r.under_sourced

    def test_single_namespace_suggestion(self):
        nodes = [_node(f"svc:{i}", ns=("only-ns",)) for i in range(3)]
        store = _store(nodes, [])
        r = suggest(store)
        assert any("namespace" in s.lower() for s in r.suggestions)

    def test_multi_namespace_no_expansion_suggestion(self):
        n1 = _node("svc:a", ns=("ns1",))
        n2 = _node("svc:b", ns=("ns2",))
        store = _store([n1, n2], [_edge("e1", "svc:a", "svc:b")])
        r = suggest(store)
        assert not any("namespace" in s.lower() for s in r.suggestions)

    def test_no_edges_suggestion(self):
        n1 = _node("svc:a")
        store = _store([n1], [])
        r = suggest(store)
        assert any("edge" in s.lower() for s in r.suggestions)

    def test_gap_for_missing_valid_from(self):
        n = _node("svc:a", valid_from=None, src=["only.md"])
        store = _store([n], [])
        r = suggest(store)
        assert any("valid_from" in g for g in r.gaps)

    def test_empty_graph_suggestions(self):
        store = _store([], [])
        r = suggest(store)
        # No edges → suggestion; potentially no-namespace suggestion too
        assert any("edge" in s.lower() for s in r.suggestions)


# ---------------------------------------------------------------------------
# agent_status()
# ---------------------------------------------------------------------------

class TestAgentStatus:
    def test_basic_counts(self):
        n1 = _node("svc:a", ns=("backend",))
        n2 = _node("svc:b", ns=("backend",))
        n3 = _node("svc:c", ns=("frontend",))
        e = _edge("e1", "svc:a", "svc:b")
        store = _store([n1, n2, n3], [e])

        dash = agent_status(store)

        assert dash.node_count == 3
        assert dash.edge_count == 1
        assert dash.namespace_count == 2
        assert set(dash.namespaces) == {"backend", "frontend"}

    def test_lint_issues_counted(self):
        n1 = _node("svc:a")
        store = _store([n1], [])  # orphan → 1 issue
        dash = agent_status(store)
        assert dash.lint_issues == 1

    def test_quarantined_orphans_counted(self):
        n1 = _node("svc:a", props={"name": "a", "quarantined_at": "2026-01-01"})
        n2 = _node("svc:b")
        e = _edge("e1", "svc:a", "svc:b")
        dash = agent_status(_store([n1, n2], [e]))
        assert dash.quarantined_orphans == 1
        assert dash.quarantined_ids == ["svc:a"]
        assert dash.lint_issues == 0

    def test_pending_journals_counted(self, tmp_path: Path):
        pending = tmp_path / "pending"
        ns_dir = pending / "backend"
        ns_dir.mkdir(parents=True)
        entry = {
            "agent": "test", "ns": "backend", "confidence": 1.0,
            "proposed_at": "2026-01-01T00:00:00Z", "status": "pending",
            "fact": {"kind": "node", "id": "svc:x", "type": "service",
                     "ns": ["backend"], "props": {}, "src": []},
        }
        (ns_dir / "journal.jsonl").write_text(json.dumps(entry) + "\n")

        store = _store([_node("svc:a")], [])
        dash = agent_status(store, pending_dir=pending)
        assert dash.pending_journals == 1

    def test_no_pending_dir(self):
        store = _store([_node("svc:a")], [])
        dash = agent_status(store, pending_dir=None)
        assert dash.pending_journals == 0

    def test_empty_graph(self):
        store = _store([], [])
        dash = agent_status(store)
        assert dash.node_count == 0
        assert dash.edge_count == 0
        assert dash.namespace_count == 0
