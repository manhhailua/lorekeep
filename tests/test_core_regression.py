"""Core regression tests — guard against breaking lorekeep's fundamental pipeline.

These tests verify the end-to-end pipeline that MUST work for lorekeep to function:
1. LiteLLMProvider has extract_json + complete methods (class structure)
2. Extraction: chunk → LLM → parse → nodes + edges
3. Full compile pipeline: raw/ → facts.jsonl
4. GraphStore: load + query (search, get_node, neighbors)
5. ScopedGraph: permission filtering
6. MCP tools: direct callable (no transport)
7. Wiki: facts.jsonl → markdown pages
8. Resolve: journal merge into facts.jsonl
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lorekeep.models import Edge, Manifest, Node

runner = CliRunner()


# ── 1. Provider class structure ────────────────────────────────────────────

class TestProviderClassStructure:
    """Regression: extract_json + complete must be methods of LiteLLMProvider,
    not nested inside setup_observability."""

    def test_litellm_provider_has_extract_json(self):
        from lorekeep.compile.providers import LiteLLMProvider
        assert hasattr(LiteLLMProvider, "extract_json")
        assert callable(getattr(LiteLLMProvider, "extract_json"))

    def test_litellm_provider_has_complete(self):
        from lorekeep.compile.providers import LiteLLMProvider
        assert hasattr(LiteLLMProvider, "complete")
        assert callable(getattr(LiteLLMProvider, "complete"))

    def test_fake_provider_has_extract_json(self):
        from lorekeep.compile.providers import FakeProvider
        assert hasattr(FakeProvider, "extract_json")

    def test_setup_observability_is_module_level_function(self):
        from lorekeep.compile import providers
        assert callable(providers.setup_observability)
        assert not isinstance(providers.setup_observability, type)


# ── 2. Extraction pipeline ─────────────────────────────────────────────────

class TestExtractionPipeline:
    """Chunk → LLM → parse → nodes + edges must produce valid facts."""

    def test_extract_chunk_produces_nodes(self):
        from lorekeep.compile.extract import extract_chunk, ExtractionCache
        from lorekeep.compile.providers import FakeProvider
        from lorekeep.models import DocChunk, Schema

        schema = Schema.load({
            "version": 1,
            "node_types": {"service": {"props": {"name": "string"}}},
            "edge_types": {"depends_on": {"from": "service", "to": "service"}},
        })
        chunk = DocChunk(
            path="test.md", start_line=1, end_line=5,
            text="The payments-api is a Go service that depends on auth-api.",
            namespace="backend",
        )
        canned = json.dumps({
            "nodes": [
                {"id": "svc:payments-api", "type": "service", "name": "payments-api"},
                {"id": "svc:auth-api", "type": "service", "name": "auth-api"},
            ],
            "edges": [
                {"type": "depends_on", "from": "svc:payments-api", "to": "svc:auth-api"},
            ],
            "aliases": {},
        })
        provider = FakeProvider([canned])
        cache = ExtractionCache(Path("/tmp/test-cache.json"))
        nodes, edges, aliases = extract_chunk(chunk, schema, provider, cache)
        assert len(nodes) == 2
        assert len(edges) == 1
        assert all(n.ns == ("backend",) for n in nodes)
        assert all(n.src == ("test.md:1",) for n in nodes)

    def test_extract_chunk_uses_cache_on_second_call(self, tmp_path):
        from lorekeep.compile.extract import extract_chunk, ExtractionCache
        from lorekeep.compile.providers import FakeProvider
        from lorekeep.models import DocChunk, Schema

        schema = Schema.load({"version": 1, "node_types": {"x": {}}, "edge_types": {}})
        chunk = DocChunk(path="a.md", start_line=1, end_line=1, text="x", namespace="ns")
        canned = json.dumps({"nodes": [], "edges": [], "aliases": {}})
        provider = FakeProvider([canned])
        cache_path = tmp_path / "cache.json"
        cache_path.write_text("{}")
        cache = ExtractionCache(cache_path)

        extract_chunk(chunk, schema, provider, cache)
        assert len(provider.calls) == 1
        extract_chunk(chunk, schema, provider, cache)
        assert len(provider.calls) == 1  # cache hit, no new LLM call


# ── 3. Full compile pipeline ───────────────────────────────────────────────

class TestCompilePipeline:
    """raw/*.md → ingest → extract → resolve → facts.jsonl must produce a valid graph."""

    def test_compile_produces_nonempty_graph(self, tmp_path, monkeypatch, patch_make_provider):
        from lorekeep.cli import app

        home = tmp_path / "home"
        home.mkdir()
        (home / "raw" / "backend").mkdir(parents=True)
        (home / "raw" / "backend" / "test.md").write_text(
            "# Test\n\npayments-api (go) depends on auth-service.\n"
        )
        (home / "schema.json").write_text(json.dumps({
            "version": 1,
            "node_types": {"service": {"props": {"name": "string", "lang": "string"}}},
            "edge_types": {"depends_on": {"from": "service", "to": "service"}},
        }))

        monkeypatch.setenv("LOREKEEP_HOME", str(home))
        monkeypatch.setattr("lorekeep.cli._start_daemon", lambda p: None)

        result = runner.invoke(app, ["init", "--yes"])
        assert result.exit_code == 0, result.stdout

        facts = home / "graph" / "facts.jsonl"
        assert facts.exists(), "facts.jsonl must exist after compile"
        lines = [l for l in facts.read_text().splitlines() if l.strip()]
        assert len(lines) > 0, "facts.jsonl must not be empty"

    def test_compile_produces_wiki(self, tmp_path, monkeypatch, patch_make_provider):
        from lorekeep.cli import app

        home = tmp_path / "home"
        home.mkdir()
        (home / "raw" / "backend").mkdir(parents=True)
        (home / "raw" / "backend" / "test.md").write_text("# Test\n\nContent here.\n")
        (home / "schema.json").write_text(json.dumps({
            "version": 1,
            "node_types": {"service": {"props": {"name": "string"}}},
            "edge_types": {},
        }))

        monkeypatch.setenv("LOREKEEP_HOME", str(home))
        monkeypatch.setattr("lorekeep.cli._start_daemon", lambda p: None)

        runner.invoke(app, ["init", "--yes"])

        wiki = home / "wiki" / "index.md"
        assert wiki.exists(), "wiki/index.md must exist after compile"

    def test_compile_is_deterministic(self, tmp_path, monkeypatch, patch_make_provider):
        """Recompiling unchanged input produces byte-identical facts.jsonl."""
        from lorekeep.cli import app

        home = tmp_path / "home"
        home.mkdir()
        (home / "raw" / "ns").mkdir(parents=True)
        (home / "raw" / "ns" / "doc.md").write_text("# Doc\nContent.\n")
        (home / "schema.json").write_text(json.dumps({
            "version": 1,
            "node_types": {"note": {"props": {"title": "string"}}},
            "edge_types": {},
        }))

        monkeypatch.setenv("LOREKEEP_HOME", str(home))
        monkeypatch.setattr("lorekeep.cli._start_daemon", lambda p: None)

        runner.invoke(app, ["init", "--yes"])
        first = (home / "graph" / "facts.jsonl").read_text()

        runner.invoke(app, ["compile"])
        second = (home / "graph" / "facts.jsonl").read_text()

        assert first == second, "Recompile of unchanged input must be byte-identical"


# ── 4. GraphStore load + query ─────────────────────────────────────────────

class TestGraphStoreCore:
    """GraphStore must load facts.jsonl and support basic queries."""

    @pytest.fixture
    def store(self, tmp_path):
        nodes = [
            Node(id="svc:a", type="service", ns=("backend",), props={"name": "a"}),
            Node(id="svc:b", type="service", ns=("backend",), props={"name": "b"}),
            Node(id="svc:c", type="service", ns=("frontend",), props={"name": "c"}),
        ]
        edges = [
            Edge(id="e1", type="depends_on", from_="svc:a", to="svc:b", ns=("backend",)),
            Edge(id="e2", type="uses", from_="svc:c", to="svc:a", ns=("frontend", "backend")),
        ]
        from lorekeep.compile.writer import write_graph
        g = tmp_path / "graph"
        write_graph(g, nodes, edges, Manifest(
            schema_version=1, chunk_count=1, node_count=3, edge_count=2,
            run_id="x", facts_hash="y",
        ))
        from lorekeep.store.graph import GraphStore
        return GraphStore.from_jsonl(g / "facts.jsonl")

    def test_load_facts(self, store):
        assert len(store.all_nodes()) == 3
        assert len(store.all_edges()) == 2

    def test_get_node(self, store):
        node = store.get_node("svc:a")
        assert node is not None
        assert node.props["name"] == "a"

    def test_get_node_missing(self, store):
        assert store.get_node("nonexistent") is None

    def test_search(self, store):
        results = store.search("a", limit=10)
        assert "svc:a" in results

    def test_neighbors(self, store):
        res = store.neighbors("svc:a", depth=1)
        ids = {n.id for n in res["nodes"]}
        assert "svc:a" in ids
        assert "svc:b" in ids
        assert len(res["edges"]) >= 1

    def test_snapshot(self, store):
        from lorekeep.store.graph import parse_date
        nodes, edges = store.snapshot(parse_date("2024-01-01"))
        assert len(nodes) == 3  # all active (no valid_from/to)


# ── 5. ScopedGraph permission ─────────────────────────────────────────────

class TestScopedGraphPermission:
    """ScopedGraph must filter by namespace — deny by default."""

    @pytest.fixture
    def scoped(self, tmp_path):
        from lorekeep.store.graph import GraphStore
        from lorekeep.perm.ns import ScopedGraph
        nodes = [
            Node(id="svc:backend", type="service", ns=("backend",)),
            Node(id="svc:frontend", type="service", ns=("frontend",)),
            Node(id="svc:public", type="service", ns=("public",)),
        ]
        store = GraphStore(nodes, [])
        return ScopedGraph(store, ["backend"])

    def test_visible_node(self, scoped):
        assert scoped.get_node("svc:backend") is not None

    def test_invisible_node(self, scoped):
        assert scoped.get_node("svc:frontend") is None

    def test_public_node_visible(self, scoped):
        assert scoped.get_node("svc:public") is not None

    def test_namespaces(self, scoped):
        ns = scoped.list_namespaces()
        assert "backend" in ns
        assert "public" in ns
        assert "frontend" not in ns

    def test_search_filtered(self, scoped):
        results = scoped.search("svc", limit=10)
        assert "svc:backend" in results
        assert "svc:public" in results
        assert "svc:frontend" not in results


# ── 6. MCP tools direct call ───────────────────────────────────────────────

class TestMCPToolsCallable:
    """MCP tools must be directly callable without MCP transport."""

    def test_search_callable(self, tmp_path):
        from lorekeep.mcp_server import configure, search
        from lorekeep.compile.writer import write_graph

        nodes = [Node(id="svc:test", type="service", ns=("backend",), props={"name": "test"})]
        g = tmp_path / "graph"
        write_graph(g, nodes, [], Manifest(
            schema_version=1, chunk_count=0, node_count=1, edge_count=0,
            run_id="x", facts_hash="y",
        ))
        configure(graph_dir=g, allowed_ns=["backend"])
        assert search("test") == ["svc:test"]

    def test_get_node_callable(self, tmp_path):
        from lorekeep.mcp_server import configure, get_node
        from lorekeep.compile.writer import write_graph

        nodes = [Node(id="svc:x", type="service", ns=("backend",), props={"name": "x"})]
        g = tmp_path / "graph"
        write_graph(g, nodes, [], Manifest(
            schema_version=1, chunk_count=0, node_count=1, edge_count=0,
            run_id="x", facts_hash="y",
        ))
        configure(graph_dir=g, allowed_ns=["backend"])
        result = get_node("svc:x")
        assert result["id"] == "svc:x"

    def test_get_node_out_of_scope(self, tmp_path):
        from lorekeep.mcp_server import configure, get_node
        from lorekeep.compile.writer import write_graph

        nodes = [Node(id="svc:secret", type="service", ns=("secret",), props={"name": "s"})]
        g = tmp_path / "graph"
        write_graph(g, nodes, [], Manifest(
            schema_version=1, chunk_count=0, node_count=1, edge_count=0,
            run_id="x", facts_hash="y",
        ))
        configure(graph_dir=g, allowed_ns=["public"])
        result = get_node("svc:secret")
        assert "error" in result

    def test_meta_callable(self, tmp_path):
        from lorekeep.mcp_server import configure, meta
        from lorekeep.compile.writer import write_graph

        nodes = [Node(id="svc:x", type="service", ns=("backend",), props={"name": "x"})]
        g = tmp_path / "graph"
        write_graph(g, nodes, [], Manifest(
            schema_version=1, chunk_count=0, node_count=1, edge_count=0,
            run_id="x", facts_hash="y",
        ))
        configure(graph_dir=g, allowed_ns=["backend"])
        result = meta()
        assert result["nodes"] == 1


# ── 7. Wiki generation ─────────────────────────────────────────────────────

class TestWikiGeneration:
    """facts.jsonl → wiki/*.md must produce readable pages."""

    def test_wiki_generates_pages(self, tmp_path):
        from lorekeep.wiki import generate_wiki
        from lorekeep.compile.writer import write_graph

        nodes = [
            Node(id="svc:a", type="service", ns=("ns",), props={"name": "a"}),
            Node(id="svc:b", type="service", ns=("ns",), props={"name": "b"}),
        ]
        edges = [
            Edge(id="e1", type="depends_on", from_="svc:a", to="svc:b", ns=("ns",)),
        ]
        g = tmp_path / "graph"
        write_graph(g, nodes, edges, Manifest(
            schema_version=1, chunk_count=1, node_count=2, edge_count=1,
            run_id="x", facts_hash="y",
        ))
        wiki = tmp_path / "wiki"
        result = generate_wiki(g, wiki)
        assert result["nodes"] == 2
        assert (wiki / "index.md").exists()
        assert (wiki / "overview.md").exists()
        assert (wiki / "entities" / "service" / "svc-a.md").exists()
        assert (wiki / "entities" / "service" / "svc-b.md").exists()


# ── 8. Resolve pipeline ────────────────────────────────────────────────────

class TestResolvePipeline:
    """Journal entries must merge into facts.jsonl."""

    def test_resolve_merges_journal(self, tmp_path, monkeypatch):
        from lorekeep.cli import app
        from lorekeep.journal import append_journal
        from lorekeep.models import JournalEntry
        from lorekeep.compile.writer import write_graph

        home = tmp_path / "home"
        home.mkdir()
        (home / "raw" / "ns").mkdir(parents=True)
        (home / "raw" / "ns" / "doc.md").write_text("# Doc\n")
        (home / "pending").mkdir()
        (home / "schema.json").write_text(json.dumps({
            "version": 1,
            "node_types": {"service": {"props": {"name": "string"}}},
            "edge_types": {},
        }))

        # Write initial facts
        g = home / "graph"
        g.mkdir()
        write_graph(g, [
            Node(id="svc:existing", type="service", ns=("ns",), props={"name": "existing"}),
        ], [], Manifest(
            schema_version=1, chunk_count=0, node_count=1, edge_count=0,
            run_id="init", facts_hash="x",
        ))

        monkeypatch.setenv("LOREKEEP_HOME", str(home))

        append_journal(
            home / "pending",
            JournalEntry(
                fact={"kind": "node", "id": "svc:agent-added", "type": "service",
                      "ns": ["ns"], "props": {"name": "agent-added"}},
                agent="test", ns="ns", confidence=0.9,
                proposed_at="2026-01-01T00:00:00Z",
            ),
            "ns",
        )

        result = runner.invoke(app, ["resolve"])
        assert result.exit_code == 0, result.stdout

        from lorekeep.facts_io import read_facts
        facts = read_facts(g / "facts.jsonl")
        ids = {f.id for f in facts}
        assert "svc:existing" in ids
        assert "svc:agent-added" in ids
