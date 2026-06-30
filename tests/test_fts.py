from pathlib import Path
from lorekeep.models import Node
from lorekeep.store.fts import FTSIndex, scan_search, node_text


def nd(id, name, lang=None):
    props = {"name": name}
    if lang:
        props["lang"] = lang
    return Node(id=id, type="service", ns=("teams/backend",), props=props)


def test_node_text_concatenates_id_name_props():
    t = node_text(nd("svc:x", "auth", "go"))
    assert "svc:x" in t and "auth" in t and "go" in t


def test_scan_search_substring(tmp_path: Path):
    nodes = [nd("svc:a", "payments-api"), nd("svc:b", "auth")]
    assert scan_search(nodes, "pay") == ["svc:a"]
    assert scan_search(nodes, "auth") == ["svc:b"]
    assert scan_search(nodes, "zzz") == []


def test_fts_index_build_and_match(tmp_path: Path):
    idx = FTSIndex(tmp_path / "fts.sqlite")
    idx.build([nd("svc:a", "payments"), nd("svc:b", "auth")])
    assert "svc:a" in idx.search("payments")
    assert idx.search("payments") == ["svc:a"]
    assert idx.search("nomatch*") == []
    idx.close()


def test_fts_different_keys_for_different_models(tmp_path: Path):
    """Cache key must differ when model changes."""
    from lorekeep.models import DocChunk
    from lorekeep.compile.extract import ExtractionCache

    cache = ExtractionCache(tmp_path / "cache.json")
    chunk = DocChunk(
        path="raw/test.md", start_line=1, end_line=10,
        text="hello", namespace="test",
    )
    key_a = cache.key(chunk, schema_version=1, model="gpt-4o-mini")
    key_b = cache.key(chunk, schema_version=1, model="claude-sonnet-4")
    assert key_a != key_b


def test_fts_cache_key_backward_compat(tmp_path: Path):
    """Old cache keys (no model) should not collide with new keys (empty model)."""
    from lorekeep.models import DocChunk
    from lorekeep.compile.extract import ExtractionCache

    cache = ExtractionCache(tmp_path / "cache.json")
    chunk = DocChunk(
        path="raw/test.md", start_line=1, end_line=10,
        text="hello", namespace="test",
    )
    key_default = cache.key(chunk, schema_version=1)
    key_empty = cache.key(chunk, schema_version=1, model="")
    assert key_default == key_empty


def test_mcp_search_uses_fts(tmp_path: Path):
    """mcp_server search tool uses FTSIndex when configured."""
    from lorekeep.mcp_server import configure, search
    from lorekeep.compile.writer import write_graph
    from lorekeep.models import Edge, Manifest

    nodes = [
        Node(id="svc:payments", type="service", ns=("backend",), props={"name": "payments", "lang": "go"}),
        Node(id="svc:auth", type="service", ns=("backend",), props={"name": "auth"}),
    ]
    graph = tmp_path / "graph"
    write_graph(graph, nodes, [], Manifest(
        schema_version=1, chunk_count=0, node_count=2, edge_count=0,
        run_id="x", facts_hash="y",
    ))

    configure(graph_dir=graph, allowed_ns=["backend"])
    results = search("payments")
    assert "svc:payments" in results
    assert "svc:auth" not in results

    results = search("auth")
    assert "svc:auth" in results

    fts_db = graph / "fts.sqlite"
    assert fts_db.exists()


def test_mcp_search_fts_fallback_on_error(tmp_path: Path):
    """If FTS fails to build, search falls back to scan."""
    from lorekeep.mcp_server import configure, search
    from lorekeep.compile.writer import write_graph
    from lorekeep.models import Manifest

    nodes = [
        Node(id="svc:payments", type="service", ns=("backend",), props={"name": "payments"}),
    ]
    graph = tmp_path / "graph"
    write_graph(graph, nodes, [], Manifest(
        schema_version=1, chunk_count=0, node_count=1, edge_count=0,
        run_id="x", facts_hash="y",
    ))

    configure(graph_dir=graph, allowed_ns=["backend"], fts_path=tmp_path / "nonexistent" / "deep" / "fts.sqlite")
    results = search("payments")
    assert "svc:payments" in results
