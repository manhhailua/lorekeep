"""Autonomous agent operations: ingest, lint, suggest, status, watch.

The agent keeps the knowledge graph current: ingest reads a source file
and extracts facts via LLM with human-in-the-loop review, lint checks
structural health, suggest identifies improvement opportunities, status
provides a dashboard, and watch runs a daemon that monitors filesystem
changes (auto-compile and auto-resolve are handled in the CLI layer).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from lorekeep.compile.extract import (
    build_prompt,
    build_system_prompt,
    parse_response,
)
from lorekeep.compile.providers import LLMProvider
from lorekeep.models import DocChunk, Schema
from lorekeep.store.graph import GraphStore, is_quarantined


@dataclass
class LintReport:
    contradictions: list[dict] = field(default_factory=list)
    orphans: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    missing_endpoints: list[dict] = field(default_factory=list)
    coverage_gaps: list[str] = field(default_factory=list)

    @property
    def has_issues(self) -> bool:
        return bool(self.contradictions or self.orphans or self.stale
                    or self.missing_endpoints or self.coverage_gaps)

    @property
    def issue_count(self) -> int:
        return (len(self.contradictions) + len(self.orphans)
                + len(self.stale) + len(self.missing_endpoints)
                + len(self.coverage_gaps))


def lint(store: GraphStore) -> LintReport:
    report = LintReport()

    # Orphans: nodes with zero inbound or outbound edges. Already-quarantined
    # nodes are excluded — a human has parked them for review (see
    # `lorekeep quarantine`), so they should stop resurfacing as lint noise.
    # Iterate all_nodes() rather than node_ids(): the latter includes NetworkX
    # phantom endpoints from dangling edges, and get_node() KeyErrors on those.
    for n in store.all_nodes():
        if is_quarantined(n):
            continue
        if not store.out_edges(n.id) and not store.in_edges(n.id):
            report.orphans.append(n.id)

    # Missing endpoints: edges referencing non-existent nodes
    for e in store.all_edges():
        f = e.from_
        t = e.to
        missing = []
        if f not in store.node_ids():
            missing.append(f)
        if t not in store.node_ids():
            missing.append(t)
        if missing:
            report.missing_endpoints.append({"edge_id": e.id, "missing": missing})

    # Stale facts: nodes/edges with expired valid_to, no superseding edge
    from datetime import date
    today = date.today()
    for e in store.all_edges():
        if e.valid_to is not None and e.valid_to < today:
            report.stale.append(e.id)

    # Coverage gaps: count facts per namespace
    ns_counts: dict[str, int] = {}
    for n in store.all_nodes():
        for ns in n.ns:
            ns_counts[ns] = ns_counts.get(ns, 0) + 1
    if ns_counts:
        avg = sum(ns_counts.values()) / len(ns_counts)
        for ns, cnt in ns_counts.items():
            if cnt < avg * 0.25 and cnt < 3:
                report.coverage_gaps.append(ns)

    # Contradictions: facts with same id but conflicting props
    id_nodes: dict[str, list] = {}
    for n in store.all_nodes():
        id_nodes.setdefault(n.id, []).append(n)
    for nid, nodes in id_nodes.items():
        if len(nodes) > 1:
            props_sets = [set(n.props.items()) for n in nodes]
            for i in range(len(props_sets)):
                for j in range(i + 1, len(props_sets)):
                    if props_sets[i] != props_sets[j]:
                        report.contradictions.append({
                            "id": nid,
                            "props_a": dict(props_sets[i]),
                            "props_b": dict(props_sets[j]),
                        })

    return report


# ── Self-heal: autonomous graph maintenance ─────────────────────────────────

@dataclass
class HealReport:
    """Result of autonomous self-heal pass.

    Only high-confidence fixes are applied automatically; flagged items
    are reported but left untouched.
    """
    edges_removed: list[str] = field(default_factory=list)
    edges_deduped: list[str] = field(default_factory=list)
    flagged: list[dict] = field(default_factory=list)

    @property
    def changes_made(self) -> bool:
        return bool(self.edges_removed or self.edges_deduped)

    @property
    def total_fixes(self) -> int:
        return len(self.edges_removed) + len(self.edges_deduped)


def self_heal(
    store: GraphStore,
    schema: Schema | None = None,
) -> tuple[GraphStore, HealReport]:
    """Apply high-confidence fixes to the graph.

    Returns a **new** GraphStore with cleaned data plus a HealReport.
    Does NOT write to disk — the caller decides whether to persist.

    Safe auto-fixes:
      - Dangling edges (endpoint node missing) → removed
      - Exact-duplicate edges (same type, from, to, validity) → deduplicated

    Flagged (NOT auto-fixed):
      - Circular dependencies (A→B→A)
      - Orphan nodes (zero edges — might be newly imported)
    """
    report = HealReport()

    # all_nodes() skips phantom nodes created by dangling edges
    real_nodes = store.all_nodes()
    real_ids: set[str] = {n.id for n in real_nodes}

    # ── 1. Remove dangling edges ─────────────────────────────────────────
    clean_edges: list[Edge] = []
    for e in store.all_edges():
        if e.from_ not in real_ids or e.to not in real_ids:
            report.edges_removed.append(e.id)
        else:
            clean_edges.append(e)

    # ── 2. Deduplicate exact-duplicate edges ─────────────────────────────
    edge_keys: dict[tuple, Edge] = {}
    for e in clean_edges:
        key = (e.type, e.from_, e.to, e.valid_from, e.valid_to)
        if key in edge_keys:
            report.edges_deduped.append(e.id)
        else:
            edge_keys[key] = e
    clean_edges = list(edge_keys.values())

    # ── 3. Flag circular dependencies ────────────────────────────────────
    adjacency: dict[str, set[str]] = {}
    for e in clean_edges:
        adjacency.setdefault(e.from_, set()).add(e.to)
    for start in sorted(real_ids):
        visited: set[str] = set()
        stack = [start]
        while stack:
            current = stack.pop()
            if current == start and visited:
                report.flagged.append({
                    "type": "circular_dependency",
                    "node": start,
                    "description": f"Circular dependency detected involving {start}",
                })
                break
            if current in visited:
                continue
            visited.add(current)
            stack.extend(adjacency.get(current, set()))

    # ── 4. Flag orphan nodes (informational — not auto-removed) ───────────
    nodes_with_edges: set[str] = set()
    for e in clean_edges:
        nodes_with_edges.add(e.from_)
        nodes_with_edges.add(e.to)
    for n in real_nodes:
        if n.id not in nodes_with_edges and not is_quarantined(n):
            report.flagged.append({
                "type": "orphan",
                "node": n.id,
                "description": f"Node {n.id} has no edges — might be noise or newly imported",
            })

    # Build new store with cleaned data (no phantom nodes)
    healed = GraphStore(real_nodes, clean_edges)
    return healed, report


@dataclass
class SuggestionReport:
    gaps: list[str] = field(default_factory=list)
    under_sourced: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)


def suggest(store: GraphStore) -> SuggestionReport:
    report = SuggestionReport()

    # Under-sourced areas: facts with few src entries
    for n in store.all_nodes():
        if len(n.src) <= 1:
            report.under_sourced.append(n.id)
        if not n.valid_from and len(n.src) <= 1:
            report.gaps.append(f"{n.id}: missing valid_from, single src")

    # Graphs with only one namespace suggest expansion
    ns_set: set[str] = set()
    for n in store.all_nodes():
        ns_set.update(n.ns)
    if len(ns_set) <= 1:
        report.suggestions.append("Only one namespace active — consider adding more raw/ directories")

    if not store.all_edges():
        report.suggestions.append("No edges in graph — consider linking related entities")

    return report


@dataclass
class StatusDashboard:
    node_count: int = 0
    edge_count: int = 0
    namespace_count: int = 0
    namespaces: list[str] = field(default_factory=list)
    lint_issues: int = 0
    pending_journals: int = 0


def agent_status(
    store: GraphStore,
    pending_dir: Path | None = None,
) -> StatusDashboard:
    dash = StatusDashboard(
        node_count=len(store.node_ids()),
        edge_count=len(store.all_edges()),
    )
    ns_set: set[str] = set()
    for n in store.all_nodes():
        ns_set.update(n.ns)
    dash.namespace_count = len(ns_set)
    dash.namespaces = sorted(ns_set)

    lr = lint(store)
    dash.lint_issues = lr.issue_count

    if pending_dir and pending_dir.exists():
        from lorekeep.journal import load_journals
        journals = load_journals(pending_dir)
        dash.pending_journals = len([j for j in journals if j.status == "pending"])

    return dash


@dataclass
class IngestResult:
    """Result of ingesting a single source file."""
    source_path: str
    ns: str
    nodes: list[dict] = field(default_factory=list)
    edges: list[dict] = field(default_factory=list)
    chunk_count: int = 0


def ingest_source(
    source_path: Path,
    raw_root: Path,
    provider: LLMProvider,
    schema: Schema,
    chunk_lines: int = 60,
    on_progress: Callable[[int, int, DocChunk], None] | None = None,
    personal_ns: str = "me",
    language: str = "en",
) -> IngestResult:
    """Read a source file, chunk it, and extract facts via LLM.

    Returns proposed nodes and edges as raw dicts for human review.
    Does NOT write to the journal — the CLI layer handles that after
    the human approves.
    """
    from lorekeep.compile.ingest import namespace_for
    ns = namespace_for(raw_root, source_path)
    rel = str(source_path.relative_to(raw_root))
    lines = source_path.read_text(encoding="utf-8").splitlines()

    # pre-count non-empty blocks for the progress total (cheap; file already read)
    total = sum(
        1 for start in range(0, len(lines), chunk_lines)
        if any(line.strip() for line in lines[start:start + chunk_lines])
    )

    all_nodes: list[dict] = []
    all_edges: list[dict] = []
    chunk_count = 0

    for start in range(0, len(lines), chunk_lines):
        block = lines[start:start + chunk_lines]
        if not any(line.strip() for line in block):
            continue
        chunk = DocChunk(
            path=rel,
            start_line=start + 1,
            end_line=start + len(block),
            text="\n".join(block),
            namespace=ns,
        )
        if on_progress is not None:
            on_progress(chunk_count, total, chunk)
        chunk_count += 1
        raw = provider.extract_json(
            build_system_prompt(
                schema, chunk.namespace, personal_ns, language=language,
            ),
            build_prompt(chunk, schema),
        )
        nodes, edges, _aliases = parse_response(raw, chunk, schema)
        for n in nodes:
            all_nodes.append(n.model_dump(mode="json", by_alias=True))
        for e in edges:
            all_edges.append(e.model_dump(mode="json", by_alias=True))

    return IngestResult(
        source_path=str(rel),
        ns=ns,
        nodes=all_nodes,
        edges=all_edges,
        chunk_count=chunk_count,
    )
