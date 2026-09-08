"""Tests for autonomous self-heal: dangling edges, edge dedup, flagging.

The self-heal pass runs after compile/resolve in the daemon loop. It must be:
- Safe: only removes genuinely broken data
- Deterministic: idempotent (running twice produces the same result)
- Non-destructive: preserves all valid nodes and edges
"""
from __future__ import annotations

from datetime import date

import pytest

from lorekeep.agent import self_heal, HealReport
from lorekeep.models import Node, Edge
from lorekeep.store.graph import GraphStore


# ── Fixtures ────────────────────────────────────────────────────────────────

def make_node(
    id: str, type: str = "service", ns: str = "test",
    props: dict | None = None, src: tuple = ("test.md:1",),
    valid_from: date | None = date(2024, 1, 1),
    valid_to: date | None = None,
) -> Node:
    return Node(
        id=id, type=type, ns=(ns,), props=props or {"name": id},
        src=src, valid_from=valid_from, valid_to=valid_to,
    )


_edge_counter = [0]


def make_edge(
    from_id: str, to_id: str, edge_type: str = "depends_on",
    ns: str = "test", id: str = "", src: tuple = ("test.md:1",),
    valid_from: date | None = date(2024, 1, 1),
    valid_to: date | None = None,
    props: dict | None = None,
) -> Edge:
    if id:
        eid = id
    else:
        _edge_counter[0] += 1
        eid = f"e_{_edge_counter[0]}"
    return Edge(
        id=eid, type=edge_type,
        **{"from": from_id}, to=to_id, ns=(ns,), src=src,
        valid_from=valid_from, valid_to=valid_to,
        props=props or {},
    )


def store(nodes, edges):
    return GraphStore(nodes, edges)


# ── Dangling edges ──────────────────────────────────────────────────────────

class TestDanglingEdges:
    def test_removes_edge_with_missing_from(self):
        """Edge referencing a non-existent 'from' node is removed."""
        n1 = make_node("svc:a")
        e = make_edge("svc:ghost", "svc:a")  # svc:ghost doesn't exist
        s = store([n1], [e])
        healed, report = self_heal(s)
        assert len(healed.all_edges()) == 0
        assert len(report.edges_removed) == 1
        assert report.changes_made

    def test_removes_edge_with_missing_to(self):
        n1 = make_node("svc:a")
        e = make_edge("svc:a", "svc:ghost")
        s = store([n1], [e])
        healed, report = self_heal(s)
        assert len(healed.all_edges()) == 0
        assert len(report.edges_removed) == 1

    def test_removes_multiple_dangling(self):
        n1 = make_node("svc:a")
        e1 = make_edge("svc:ghost1", "svc:a")
        e2 = make_edge("svc:a", "svc:ghost2")
        s = store([n1], [e1, e2])
        healed, report = self_heal(s)
        assert len(healed.all_edges()) == 0
        assert len(report.edges_removed) == 2

    def test_keeps_valid_edges(self):
        n1 = make_node("svc:a")
        n2 = make_node("svc:b")
        e = make_edge("svc:a", "svc:b")
        s = store([n1, n2], [e])
        healed, report = self_heal(s)
        assert len(healed.all_edges()) == 1
        assert len(report.edges_removed) == 0
        assert not report.changes_made

    def test_dangling_removed_phantom_node_gone(self):
        """After healing, the phantom node from the dangling edge must not exist."""
        n1 = make_node("svc:a")
        e = make_edge("svc:ghost", "svc:a")
        s = store([n1], [e])
        healed, report = self_heal(s)
        assert "svc:ghost" not in healed.node_ids()
        assert "svc:a" in healed.node_ids()


# ── Edge deduplication ──────────────────────────────────────────────────────

class TestEdgeDedup:
    def test_removes_exact_duplicate(self):
        n1 = make_node("svc:a")
        n2 = make_node("svc:b")
        e1 = make_edge("svc:a", "svc:b", id="e1")
        e2 = make_edge("svc:a", "svc:b", id="e2")  # same type, from, to
        s = store([n1, n2], [e1, e2])
        healed, report = self_heal(s)
        assert len(healed.all_edges()) == 1
        assert len(report.edges_deduped) == 1

    def test_keeps_different_types(self):
        n1 = make_node("svc:a")
        n2 = make_node("svc:b")
        e1 = make_edge("svc:a", "svc:b", "depends_on")
        e2 = make_edge("svc:a", "svc:b", "relates_to")
        s = store([n1, n2], [e1, e2])
        healed, report = self_heal(s)
        assert len(healed.all_edges()) == 2
        assert len(report.edges_deduped) == 0

    def test_keeps_different_endpoints(self):
        n1 = make_node("svc:a")
        n2 = make_node("svc:b")
        n3 = make_node("svc:c")
        e1 = make_edge("svc:a", "svc:b")
        e2 = make_edge("svc:a", "svc:c")
        s = store([n1, n2, n3], [e1, e2])
        healed, report = self_heal(s)
        assert len(healed.all_edges()) == 2
        assert len(report.edges_deduped) == 0

    def test_keeps_different_validity(self):
        """Edges with different valid_from are not duplicates."""
        n1 = make_node("svc:a")
        n2 = make_node("svc:b")
        e1 = make_edge("svc:a", "svc:b", valid_from=date(2024, 1, 1))
        e2 = make_edge("svc:a", "svc:b", valid_from=date(2024, 6, 1))
        s = store([n1, n2], [e1, e2])
        healed, report = self_heal(s)
        assert len(healed.all_edges()) == 2
        assert len(report.edges_deduped) == 0


# ── Flagging (non-destructive) ─────────────────────────────────────────────

class TestFlagging:
    def test_orphan_node_quarantined_not_removed(self):
        n1 = make_node("svc:a")  # no edges
        s = store([n1], [])
        healed, report = self_heal(s)
        assert len(healed.all_nodes()) == 1  # still there
        assert report.nodes_quarantined == ["svc:a"]
        from lorekeep.store.graph import is_quarantined
        assert is_quarantined(healed.get_node("svc:a"))
        assert [f for f in report.flagged if f["type"] == "orphan"] == []

    def test_connected_node_not_flagged_orphan(self):
        n1 = make_node("svc:a")
        n2 = make_node("svc:b")
        e = make_edge("svc:a", "svc:b")
        s = store([n1, n2], [e])
        _, report = self_heal(s)
        orphan_flags = [f for f in report.flagged if f["type"] == "orphan"]
        assert len(orphan_flags) == 0

    def test_circular_dependency_flagged(self):
        n1 = make_node("svc:a")
        n2 = make_node("svc:b")
        e1 = make_edge("svc:a", "svc:b")
        e2 = make_edge("svc:b", "svc:a")  # creates cycle A→B→A
        s = store([n1, n2], [e1, e2])
        healed, report = self_heal(s)
        assert len(healed.all_edges()) == 2  # NOT removed
        circular_flags = [f for f in report.flagged if f["type"] == "circular_dependency"]
        assert len(circular_flags) >= 1

    def test_no_cycle_not_flagged(self):
        n1 = make_node("svc:a")
        n2 = make_node("svc:b")
        n3 = make_node("svc:c")
        e1 = make_edge("svc:a", "svc:b")
        e2 = make_edge("svc:b", "svc:c")  # A→B→C, no cycle
        s = store([n1, n2, n3], [e1, e2])
        _, report = self_heal(s)
        circular_flags = [f for f in report.flagged if f["type"] == "circular_dependency"]
        assert len(circular_flags) == 0


# ── Healthy graph ───────────────────────────────────────────────────────────

class TestHealthyGraph:
    def test_no_changes_on_clean_graph(self):
        n1 = make_node("svc:a")
        n2 = make_node("svc:b")
        e = make_edge("svc:a", "svc:b")
        s = store([n1, n2], [e])
        healed, report = self_heal(s)
        assert not report.changes_made
        assert len(healed.all_nodes()) == 2
        assert len(healed.all_edges()) == 1

    def test_preserves_node_props(self):
        n1 = make_node("svc:a", props={"name": "Alpha", "summary": "Service A"})
        n2 = make_node("svc:b", props={"name": "Beta"})
        e = make_edge("svc:a", "svc:b")
        s = store([n1, n2], [e])
        healed, report = self_heal(s)
        node_a = healed.get_node("svc:a")
        assert node_a is not None
        assert node_a.props["summary"] == "Service A"

    def test_preserves_edge_data(self):
        n1 = make_node("svc:a")
        n2 = make_node("svc:b")
        e = make_edge("svc:a", "svc:b", "depends_on",
                       props={"reason": "needs API"})
        s = store([n1, n2], [e])
        healed, _ = self_heal(s)
        edges = healed.all_edges()
        assert len(edges) == 1


# ── Idempotency ─────────────────────────────────────────────────────────────

class TestIdempotency:
    def test_run_twice_produces_same_result(self):
        n1 = make_node("svc:a")
        n2 = make_node("svc:b")
        e = make_edge("svc:a", "svc:ghost")  # dangling
        s = store([n1, n2], [e])

        healed1, report1 = self_heal(s)
        healed2, report2 = self_heal(healed1)

        assert not report2.changes_made  # second pass is no-op
        assert len(healed1.all_nodes()) == len(healed2.all_nodes())
        assert len(healed1.all_edges()) == len(healed2.all_edges())

    def test_idempotent_with_dedup(self):
        n1 = make_node("svc:a")
        n2 = make_node("svc:b")
        e1 = make_edge("svc:a", "svc:b", id="e1")
        e2 = make_edge("svc:a", "svc:b", id="e2")
        s = store([n1, n2], [e1, e2])

        healed1, _ = self_heal(s)
        healed2, report2 = self_heal(healed1)

        assert not report2.changes_made
        assert len(healed1.all_edges()) == len(healed2.all_edges())


# ── HealReport properties ───────────────────────────────────────────────────

class TestHealReport:
    def test_changes_made_true_when_edges_removed(self):
        report = HealReport(edges_removed=["e1"])
        assert report.changes_made
        assert report.total_fixes == 1

    def test_changes_made_false_when_only_flagged(self):
        report = HealReport(flagged=[{"type": "orphan"}])
        assert not report.changes_made
        assert report.total_fixes == 0

    def test_total_fixes_counts_all(self):
        report = HealReport(
            edges_removed=["e1", "e2"],
            edges_deduped=["e3"],
        )
        assert report.total_fixes == 3

    def test_changes_made_true_when_nodes_quarantined(self):
        report = HealReport(nodes_quarantined=["svc:a"])
        assert report.changes_made
        assert report.total_fixes == 1

    def test_total_fixes_includes_quarantine(self):
        report = HealReport(
            edges_removed=["e1"],
            nodes_quarantined=["svc:a", "svc:b"],
        )
        assert report.total_fixes == 3

    def test_empty_report(self):
        report = HealReport()
        assert not report.changes_made
        assert report.total_fixes == 0
        assert report.flagged == []
