"""FastMCP server exposing the scoped temporal graph with read + write tools.

Tools are plain module functions using a module-global ScopedGraph set by
configure(). @mcp.tool() registers them with FastMCP but they remain directly
callable, so tests invoke them without the MCP transport.

Write tools append to pending/<ns>/journal.jsonl; facts enter the graph on
the next resolve pass.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from lorekeep.journal import append_journal
from lorekeep.models import JournalEntry, Manifest, Schema
from lorekeep.perm.ns import ScopedGraph
from lorekeep.schema_io import load_schema
from lorekeep.store.graph import GraphStore, parse_date
from lorekeep.store.fts import FTSIndex

mcp = FastMCP("lorekeep")

_state: dict = {}          # graph_dir, allowed_ns, schema_path, pending_dir, fts_path, facts_mtime
_scope: ScopedGraph | None = None
_schema: Schema | None = None
_manifest: Manifest | None = None
_fts: FTSIndex | None = None


def configure(graph_dir, allowed_ns, schema_path=None, fts_path=None, pending_dir=None) -> None:
    """Set the graph location + scope, then build. Safe to call again to refresh."""
    _state["graph_dir"] = Path(graph_dir)
    _state["allowed_ns"] = list(allowed_ns)
    _state["schema_path"] = Path(schema_path) if schema_path else None
    _state["pending_dir"] = Path(pending_dir) if pending_dir else None
    if fts_path:
        _state["fts_path"] = Path(fts_path)
    else:
        _state["fts_path"] = _state["graph_dir"] / "fts.sqlite"
    _rebuild()


def _rebuild() -> None:
    """(Re)load the graph + schema + manifest + FTS from disk into a fresh ScopedGraph."""
    global _scope, _schema, _manifest, _fts
    facts = _state["graph_dir"] / "facts.jsonl"
    store = GraphStore.from_jsonl(facts)
    sp = _state.get("schema_path")
    _schema = load_schema(sp) if sp else None
    _scope = ScopedGraph(store, _state["allowed_ns"])
    manifest_path = _state["graph_dir"] / "manifest.json"
    if manifest_path.exists():
        _manifest = Manifest.from_json(manifest_path.read_text(encoding="utf-8"))
    else:
        _manifest = None
    try:
        fts_path = _state.get("fts_path")
        if fts_path:
            _fts = FTSIndex(fts_path)
            _fts.build(store.all_nodes())
        else:
            _fts = None
    except Exception:
        _fts = None
    _state["facts_mtime"] = facts.stat().st_mtime if facts.exists() else 0


def _require() -> ScopedGraph:
    """Return the scoped graph, lazy-reloading if facts.jsonl changed on disk."""
    if not _state:
        raise RuntimeError("mcp_server not configured; call configure() first")
    facts = _state["graph_dir"] / "facts.jsonl"
    mtime = facts.stat().st_mtime if facts.exists() else 0
    if _scope is None or mtime != _state.get("facts_mtime"):
        _rebuild()
    return _scope


@mcp.tool()
def get_node(id: str) -> dict:
    """Return a node by id (props + provenance), or error if absent/out of scope."""
    node = _require().get_node(id)
    if node is None:
        return {"error": "not found or out of scope"}
    return node.model_dump(mode="json", by_alias=True)


@mcp.tool()
def neighbors(id: str, edge_type: str = "", depth: int = 1) -> dict:
    """Traverse neighbors up to depth (both directions), scoped to the caller."""
    scoped = _require()
    depth = max(1, min(int(depth), 5))   # bound BFS cost; 5 hops spans any realistic graph
    res = scoped.neighbors(id, edge_type or None, depth)
    return {
        "nodes": [n.model_dump(mode="json", by_alias=True) for n in res["nodes"]],
        "edges": [e.model_dump(mode="json", by_alias=True) for e in res["edges"]],
    }


@mcp.tool()
def schema() -> dict:
    """Return the graph schema (node/edge types)."""
    if _schema is None:
        return {"error": "no schema loaded"}
    return _schema.model_dump(mode="json", by_alias=True)


@mcp.tool()
def list_namespaces() -> list:
    """Namespaces visible to this caller."""
    return _require().list_namespaces()


@mcp.tool()
def at_time(time: str) -> dict:
    """Snapshot of facts valid at an ISO date (half-open [valid_from, valid_to))."""
    scoped = _require()
    nodes, edges = scoped.snapshot(parse_date(time))
    return {
        "nodes": [n.model_dump(mode="json", by_alias=True) for n in nodes],
        "edges": [e.model_dump(mode="json", by_alias=True) for e in edges],
    }


@mcp.tool()
def history(id: str) -> list:
    """All versions of an entity + edges touching it, ordered by valid_from."""
    return _require().history(id)


@mcp.tool()
def changes(from_t: str, to_t: str) -> dict:
    """Edges whose validity began or ended within [from_t, to_t)."""
    return _require().changes(parse_date(from_t), parse_date(to_t))


@mcp.tool()
def search(query: str, limit: int = 10) -> list:
    """Text search over node ids/props, scoped to the caller."""
    return _require().search(query, limit, fts=_fts)


@mcp.tool()
def meta(topic: str = "") -> dict:
    """Graph coverage, provenance, and freshness.

    Agent calls this to decide whether to query the graph or work from memory.
    If ``topic`` is given, returns matching node count and ids for that topic.
    """
    scope = _require()
    result = scope.stats(topic)

    if _manifest:
        result["compile"] = {
            "run_id": _manifest.run_id,
            "compiled_at": _manifest.compiled_at or None,
            "merged_count": _manifest.merged_count,
            "quarantined_count": _manifest.quarantined_count,
        }

    pending = _pending_dir()
    if pending and pending.exists():
        from lorekeep.journal import load_journals
        journals = load_journals(pending)
        result["pending"] = sum(1 for j in journals if j.status == "pending")
    else:
        result["pending"] = 0

    return result


# --- Write tools (journal-based) -----------------------------------------


def _active_ns() -> tuple[str, ...]:
    allowed = _state.get("allowed_ns", ["public"])
    return tuple(ns for ns in allowed if ns != "public") or ("public",)


def _primary_ns() -> str:
    active = _active_ns()
    return active[0] if active else "public"


def _pending_dir() -> Path | None:
    return _state.get("pending_dir")


def _write_journal(fact_dict: dict, confidence: float, agent: str = "mcp") -> dict:
    pending = _pending_dir()
    if pending is None:
        return {"error": "no pending directory configured"}
    ns = _primary_ns()
    fact_dict["ns"] = list(_active_ns())
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    entry = JournalEntry(
        fact=fact_dict,
        agent=agent,
        ns=ns,
        confidence=max(0.0, min(1.0, float(confidence))),
        proposed_at=now,
        status="pending",
    )
    append_journal(pending, entry, ns)
    return {
        "accepted": True,
        "id": fact_dict.get("id", ""),
        "status": "pending",
        "ns": ns,
        "proposed_at": now,
    }


@mcp.tool()
def propose_fact(fact: dict, confidence: float) -> dict:
    """Propose a new node or edge. ns is server-enforced, caller ns is stripped."""
    if not _schema:
        return {"error": "no schema loaded"}
    fact_type = fact.get("type", "")
    if fact["kind"] == "node":
        if not _schema.is_valid_node_type(fact_type):
            return {"error": f"unknown node type: {fact_type}"}
    elif fact["kind"] == "edge":
        if not _schema.is_valid_edge_type(fact_type):
            return {"error": f"unknown edge type: {fact_type}"}
    else:
        return {"error": f"unknown fact kind: {fact.get('kind')}"}
    fact.pop("ns", None)
    if "src" not in fact:
        fact["src"] = []
    return _write_journal(fact, confidence)


@mcp.tool()
def link_facts(from_id: str, to_id: str, edge_type: str, confidence: float) -> dict:
    """Create an edge between two existing nodes, server-enforced ns."""
    scoped = _require()
    if scoped.get_node(from_id) is None:
        return {"error": f"from node not found or out of scope: {from_id}"}
    if scoped.get_node(to_id) is None:
        return {"error": f"to node not found or out of scope: {to_id}"}
    if not _schema:
        return {"error": "no schema loaded"}
    if not _schema.is_valid_edge_type(edge_type):
        return {"error": f"unknown edge type: {edge_type}"}
    fact = {
        "kind": "edge",
        "id": "",
        "type": edge_type,
        "from": from_id,
        "to": to_id,
        "ns": [],
        "props": {},
        "src": [],
    }
    return _write_journal(fact, confidence)


@mcp.tool()
def flag_contradiction(fact_a_id: str, fact_b_id: str, description: str) -> dict:
    """Report conflicting facts for curator review (always quarantined)."""
    pending = _pending_dir()
    if pending is None:
        return {"error": "no pending directory configured"}
    ns = _primary_ns()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    flag_fact = {
        "kind": "node",
        "id": f"contradiction:{fact_a_id}:{fact_b_id}",
        "type": "note",
        "ns": list(_active_ns()),
        "props": {
            "title": f"contradiction: {fact_a_id} vs {fact_b_id}",
            "topic": description,
        },
        "src": [],
    }
    entry = JournalEntry(
        fact=flag_fact,
        agent="mcp",
        ns=ns,
        confidence=0.0,
        proposed_at=now,
        status="pending",
    )
    append_journal(pending, entry, ns)
    return {
        "accepted": True,
        "id": flag_fact["id"],
        "status": "pending",
        "note": "Flagged for curator review. Both facts will be quarantined on next resolve.",
    }


@mcp.tool()
def update_fact(id: str, props: dict, confidence: float) -> dict:
    """Propose updated props for an existing node or edge."""
    scoped = _require()
    store = scoped._g
    node = store.get_node(id)
    if node is not None:
        fact = node.model_dump(mode="json", by_alias=True)
        fact["props"] = props
        fact.pop("ns", None)
        return _write_journal(fact, confidence)
    for e in store.all_edges():
        if e.id == id:
            edge_dict = e.model_dump(mode="json", by_alias=True)
            edge_dict["props"] = props
            edge_dict.pop("ns", None)
            return _write_journal(edge_dict, confidence)
    return {"error": f"fact not found or out of scope: {id}"}


@mcp.tool()
def suggest_improvement(description: str) -> dict:
    """Suggest a non-fact improvement (gap, missing entity) - review only."""
    pending = _pending_dir()
    if pending is None:
        return {"error": "no pending directory configured"}
    ns = _primary_ns()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    suggestion_fact = {
        "kind": "node",
        "id": f"suggestion:{_primary_ns()}:{now[:19]}",
        "type": "note",
        "ns": list(_active_ns()),
        "props": {
            "title": "improvement suggestion",
            "topic": description,
        },
        "src": [],
    }
    entry = JournalEntry(
        fact=suggestion_fact,
        agent="mcp",
        ns=ns,
        confidence=0.0,
        proposed_at=now,
        status="pending",
    )
    append_journal(pending, entry, ns)
    return {
        "accepted": True,
        "id": suggestion_fact["id"],
        "status": "pending",
        "note": "Suggestion recorded for curator review.",
    }


if __name__ == "__main__":
    mcp.run()
