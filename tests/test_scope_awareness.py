"""Tests for scope-awareness: meta tool, graph stats, compiled_at."""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from lorekeep.models import Edge, Manifest, Node, now_iso
from lorekeep.mcp_server import configure, meta
from lorekeep.perm.ns import ScopedGraph
from lorekeep.store.graph import GraphStore


# ── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture
def sample_nodes() -> list[Node]:
    return [
        Node(
            id="svc:payments-api", type="service",
            ns=("backend",), valid_from=date(2024, 1, 15),
            props={"name": "payments-api", "lang": "go"},
            src=("raw/backend/payments.md:3",),
        ),
        Node(
            id="svc:auth", type="service",
            ns=("backend",),
            props={"name": "auth"},
            src=("raw/backend/payments.md:6",),
        ),
        Node(
            id="svc:web-ui", type="service",
            ns=("frontend",),
            props={"name": "web-ui", "lang": "ts"},
            src=("raw/frontend/web.md:1",),
        ),
        Node(
            id="svc:legacy", type="service",
            ns=("backend",), valid_to=date.today() - timedelta(days=30),
            props={"name": "legacy"},
            src=("raw/backend/legacy.md:1",),
        ),
        Node(
            id="svc:agent-found", type="service",
            ns=("backend",),
            props={"name": "agent-discovered"},
            src=(),
        ),
    ]


@pytest.fixture
def sample_edges() -> list[Edge]:
    return [
        Edge(
            id="e_dep_1", type="depends_on",
            from_="svc:payments-api", to="svc:auth",
            ns=("backend",), valid_from=date(2024, 1, 15),
            valid_to=date.today() - timedelta(days=1),
        ),
    ]


@pytest.fixture
def store(sample_nodes, sample_edges) -> GraphStore:
    return GraphStore(sample_nodes, sample_edges)


@pytest.fixture
def graph_dir(tmp_path: Path, sample_nodes, sample_edges) -> Path:
    from lorekeep.compile.writer import write_graph
    g = tmp_path / "graph"
    manifest = Manifest(
        schema_version=1, chunk_count=1,
        node_count=len(sample_nodes), edge_count=len(sample_edges),
        run_id="test123", facts_hash="abc", compiled_at=now_iso(),
        merged_count=2, quarantined_count=1,
    )
    write_graph(g, sample_nodes, sample_edges, manifest)
    return g


# ── GraphStore.stats() ─────────────────────────────────────────────────────


class TestGraphStoreStats:
    def test_basic_counts(self, store):
        s = store.stats()
        assert s["nodes"] == 5
        assert s["edges"] == 1

    def test_node_types(self, store):
        s = store.stats()
        assert s["node_types"] == {"service": 5}

    def test_edge_types(self, store):
        s = store.stats()
        assert s["edge_types"] == {"depends_on": 1}

    def test_namespaces(self, store):
        s = store.stats()
        assert "backend" in s["namespaces"]
        assert "frontend" in s["namespaces"]

    def test_provenance_curator(self, store):
        s = store.stats()
        assert s["provenance"]["curator"] == 4
        assert s["provenance"]["agent"] == 1

    def test_freshness_oldest_newest(self, store):
        s = store.stats()
        assert s["freshness"]["oldest"] == "2024-01-15"
        assert s["freshness"]["newest"] == "2024-01-15"

    def test_freshness_expired(self, store):
        s = store.stats()
        assert s["freshness"]["expired"] >= 1

    def test_empty_graph(self):
        s = GraphStore([], []).stats()
        assert s["nodes"] == 0
        assert s["edges"] == 0
        assert s["provenance"] == {"curator": 0, "agent": 0}
        assert s["freshness"]["oldest"] is None

    def test_provenance_agent_no_src(self, store):
        s = store.stats()
        assert s["provenance"]["agent"] == 1


# ── ScopedGraph.stats() ────────────────────────────────────────────────────


class TestScopedGraphStats:
    def test_ns_filter_backend(self, store):
        scope = ScopedGraph(store, ["backend"])
        s = scope.stats()
        assert s["nodes"] == 4
        assert "frontend" not in s["namespaces"]

    def test_ns_filter_frontend(self, store):
        scope = ScopedGraph(store, ["frontend"])
        s = scope.stats()
        assert s["nodes"] == 1
        assert "frontend" in s["namespaces"]
        assert "backend" not in s["namespaces"]

    def test_ns_filter_public(self, store):
        scope = ScopedGraph(store, [])
        s = scope.stats()
        assert s["nodes"] == 0

    def test_topic_match(self, store):
        scope = ScopedGraph(store, ["backend"])
        s = scope.stats(topic="payment")
        assert s["coverage"]["matching_nodes"] == 1
        assert "svc:payments-api" in s["coverage"]["node_ids"]

    def test_topic_no_match(self, store):
        scope = ScopedGraph(store, ["backend"])
        s = scope.stats(topic="nonexistent")
        assert s["coverage"]["matching_nodes"] == 0

    def test_topic_match_lang(self, store):
        scope = ScopedGraph(store, ["backend", "frontend"])
        s = scope.stats(topic="go")
        assert s["coverage"]["matching_nodes"] >= 1

    def test_topic_match_type(self, store):
        scope = ScopedGraph(store, ["backend", "frontend"])
        s = scope.stats(topic="service")
        assert s["coverage"]["matching_nodes"] == 5

    def test_ns_filter_excludes_edge(self, store):
        scope = ScopedGraph(store, ["frontend"])
        s = scope.stats()
        assert s["edges"] == 0


# ── MCP meta() tool ────────────────────────────────────────────────────────


class TestMetaTool:
    def test_meta_basic(self, graph_dir, tmp_path):
        configure(graph_dir=graph_dir, allowed_ns=["backend", "frontend"])
        result = meta()
        assert result["nodes"] == 5
        assert result["edges"] == 1
        assert result["pending"] == 0

    def test_meta_compile_info(self, graph_dir):
        configure(graph_dir=graph_dir, allowed_ns=["backend"])
        result = meta()
        assert result["compile"]["run_id"] == "test123"
        assert result["compile"]["compiled_at"] is not None
        assert result["compile"]["merged_count"] == 2
        assert result["compile"]["quarantined_count"] == 1

    def test_meta_ns_filtered(self, graph_dir):
        configure(graph_dir=graph_dir, allowed_ns=["frontend"])
        result = meta()
        assert result["nodes"] == 1
        assert result["provenance"]["curator"] == 1

    def test_meta_topic(self, graph_dir):
        configure(graph_dir=graph_dir, allowed_ns=["backend", "frontend"])
        configure(graph_dir=graph_dir, allowed_ns=["backend", "frontend"])
        result = meta(topic="payment")
        assert result["coverage"]["matching_nodes"] == 1
        assert "svc:payments-api" in result["coverage"]["node_ids"]

    def test_meta_no_manifest(self, tmp_path, sample_nodes, sample_edges):
        from lorekeep.compile.writer import write_graph
        g = tmp_path / "graph"
        write_graph(g, sample_nodes, sample_edges, Manifest(
            schema_version=1, chunk_count=0,
            node_count=6, edge_count=1,
            run_id="x", facts_hash="y",
        ))
        (g / "manifest.json").unlink()
        configure(graph_dir=g, allowed_ns=["backend"])
        result = meta()
        assert "compile" not in result or result.get("compile", {}).get("compiled_at") is None

    def test_meta_pending_count(self, graph_dir, tmp_path):
        from lorekeep.journal import append_journal
        from lorekeep.models import JournalEntry

        pending = tmp_path / "pending"
        configure(
            graph_dir=graph_dir,
            allowed_ns=["backend"],
            pending_dir=pending,
        )
        append_journal(
            pending,
            JournalEntry(
                fact={"kind": "node", "id": "svc:test", "type": "service",
                      "ns": ["backend"], "props": {"name": "test"}},
                agent="test", ns="backend", confidence=0.9,
                proposed_at="2026-06-30T00:00:00Z",
            ),
            "backend",
        )
        result = meta()
        assert result["pending"] == 1

    def test_meta_provenance(self, graph_dir):
        configure(graph_dir=graph_dir, allowed_ns=["backend", "frontend"])
        result = meta()
        assert result["provenance"]["curator"] == 4
        assert result["provenance"]["agent"] == 1

    def test_meta_empty_graph(self, tmp_path):
        g = tmp_path / "empty"
        g.mkdir()
        (g / "facts.jsonl").write_text("")
        configure(graph_dir=g, allowed_ns=["backend"])
        result = meta()
        assert result["nodes"] == 0
        assert result["pending"] == 0


# ── Manifest compiled_at ───────────────────────────────────────────────────


class TestCompiledAt:
    def test_compiled_at_in_manifest(self):
        ts = now_iso()
        m = Manifest(
            schema_version=1, chunk_count=0,
            node_count=0, edge_count=0,
            run_id="x", facts_hash="y",
            compiled_at=ts,
        )
        assert m.compiled_at == ts

    def test_compiled_at_default_empty(self):
        m = Manifest(
            schema_version=1, chunk_count=0,
            node_count=0, edge_count=0,
            run_id="x", facts_hash="y",
        )
        assert m.compiled_at == ""

    def test_compiled_at_roundtrip(self):
        ts = now_iso()
        m = Manifest(
            schema_version=1, chunk_count=0,
            node_count=1, edge_count=0,
            run_id="x", facts_hash="y",
            compiled_at=ts,
        )
        text = m.to_json()
        m2 = Manifest.from_json(text)
        assert m2.compiled_at == ts

    def test_old_manifest_without_compiled_at(self):
        """Manifests from before this feature should still load."""
        old = json.dumps({
            "schema_version": 1, "chunk_count": 1,
            "node_count": 2, "edge_count": 1,
            "run_id": "abc", "facts_hash": "def",
        })
        m = Manifest.from_json(old)
        assert m.compiled_at == ""
