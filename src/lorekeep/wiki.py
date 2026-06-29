"""Generate Obsidian-compatible markdown wiki from facts.jsonl.

The wiki is a human-browsable projection of the compiled knowledge graph.
It is fully derived from facts.jsonl — never the reverse. Re-generating
from unchanged input yields byte-identical pages (except log.md, which
is append-only by design).

Output structure:
    wiki/
    ├── index.md                # catalog of all entities, grouped by type
    ├── log.md                  # append-only generation log
    ├── overview.md             # graph stats dashboard
    └── entities/
        └── <type>/
            └── <slug>.md       # one page per node, [[wikilinks]] for edges
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from lorekeep.compile.writer import _atomic_write
from lorekeep.models import Edge, Manifest, Node
from lorekeep.store.graph import GraphStore


def _slug(node_id: str) -> str:
    """Sanitize a node ID into a filename-safe slug for wikilinks.

    Colons and slashes become hyphens. Everything else is kept as-is
    so the slug is round-trippable within a wiki build.
    """
    return re.sub(r"[:/]", "-", node_id)


def _yaml_scalar(value: str) -> str:
    """Quote a YAML scalar so special chars (colon, etc.) are safe."""
    if value is None:
        return "null"
    return json.dumps(str(value))


def _yaml_list(items: list[str]) -> str:
    if not items:
        return "[]"
    return "[" + ", ".join(json.dumps(str(i)) for i in items) + "]"


def _fmt_date(d) -> str:
    if d is None:
        return ""
    return d.isoformat() if hasattr(d, "isoformat") else str(d)


def _fmt_validity(valid_from, valid_to) -> str:
    vf = _fmt_date(valid_from)
    vt = _fmt_date(valid_to)
    if vf and vt:
        return f"{vf} \u2192 {vt}"
    if vf:
        return f"{vf} \u2192 present"
    if vt:
        return f"until {vt}"
    return "always"


def _fmt_prop_value(val) -> str:
    """Format a prop value for markdown table cell — escape pipes, collapse newlines."""
    if isinstance(val, str):
        s = val.replace("|", "\\|").replace("\n", " ")
    else:
        s = json.dumps(val, ensure_ascii=False).replace("|", "\\|").replace("\n", " ")
    return s


def _frontmatter(node: Node) -> str:
    lines = ["---"]
    lines.append(f"id: {_yaml_scalar(node.id)}")
    lines.append(f"type: {_yaml_scalar(node.type)}")
    lines.append(f"ns: {_yaml_list(list(node.ns))}")
    lines.append(f"valid_from: {_yaml_scalar(_fmt_date(node.valid_from))}")
    lines.append(f"valid_to: {_yaml_scalar(_fmt_date(node.valid_to))}")
    if node.src:
        lines.append("sources:")
        for s in node.src:
            lines.append(f"  - {json.dumps(str(s))}")
    else:
        lines.append("sources: []")
    tags = [node.type] + list(node.ns) + ["entity"]
    lines.append(f"tags: {_yaml_list(tags)}")
    lines.append("---")
    return "\n".join(lines)


def _props_table(node: Node) -> str:
    if not node.props:
        return ""
    lines = ["", "## Properties", "", "| Key | Value |", "|---|---|"]
    for key in sorted(node.props):
        lines.append(f"| {_fmt_prop_value(key)} | {_fmt_prop_value(node.props[key])} |")
    return "\n".join(lines)


def _relationships(
    node: Node, out_edges: list[Edge], in_edges: list[Edge]
) -> str:
    sections: list[str] = []

    if out_edges:
        sections.extend(["", "## Relationships", ""])
        by_type: dict[str, list[Edge]] = {}
        for e in out_edges:
            by_type.setdefault(e.type, []).append(e)
        for etype in sorted(by_type):
            sections.append(f"### {etype} \u2192")
            sections.append("")
            for e in sorted(by_type[etype], key=lambda e: e.to):
                validity = _fmt_validity(e.valid_from, e.valid_to)
                sections.append(f"- [[{_slug(e.to)}]] ({validity})")
            sections.append("")

    if in_edges:
        if not out_edges:
            sections.extend(["", "## Relationships", ""])
        by_type = {}
        for e in in_edges:
            by_type.setdefault(e.type, []).append(e)
        for etype in sorted(by_type):
            sections.append(f"### \u2190 {etype}")
            sections.append("")
            for e in sorted(by_type[etype], key=lambda e: e.from_):
                validity = _fmt_validity(e.valid_from, e.valid_to)
                sections.append(f"- [[{_slug(e.from_)}]] ({validity})")
            sections.append("")

    return "\n".join(sections)


def _timeline(node: Node) -> str:
    if node.valid_from is None and node.valid_to is None:
        return ""
    lines = ["", "## Timeline", ""]
    if node.valid_from:
        lines.append(f"- **{_fmt_date(node.valid_from)}**: Valid from")
    if node.valid_to:
        lines.append(f"- **{_fmt_date(node.valid_to)}**: Valid until")
    return "\n".join(lines)


def _entity_page(node: Node, store: GraphStore) -> str:
    title = node.props.get("name", node.id)
    out_e = store.out_edges(node.id)
    in_e = store.in_edges(node.id)

    parts = [
        _frontmatter(node),
        "",
        f"# {title}",
        "",
        f"> ID: `{node.id}`",
    ]
    parts.append(_props_table(node))
    parts.append(_relationships(node, out_e, in_e))
    parts.append(_timeline(node))
    parts.append("")
    return "\n".join(parts)


def _index_page(store: GraphStore) -> str:
    nodes = store.all_nodes()
    edge_count = store._G.number_of_edges()

    lines = [
        "---",
        "type: index",
        "tags: [index, lorekeep-wiki]",
        "---",
        "",
        "# Lorekeep Wiki \u2014 Index",
        "",
        f"Nodes: {len(nodes)} | Edges: {edge_count}",
        "",
    ]

    by_type: dict[str, list[Node]] = {}
    for n in nodes:
        by_type.setdefault(n.type, []).append(n)

    for ntype in sorted(by_type):
        lines.append(f"## {ntype.title()}s")
        lines.append("")
        for n in sorted(by_type[ntype], key=lambda n: n.id):
            title = n.props.get("name", n.id)
            slug = _slug(n.id)
            summary_parts = [title]
            if n.props.get("lang"):
                summary_parts.append(f"({n.props['lang']})")
            if n.valid_from:
                summary_parts.append(f"\u2014 since {_fmt_date(n.valid_from)}")
            lines.append(f"- [[{slug}]] \u2014 {' '.join(summary_parts)}")
        lines.append("")

    return "\n".join(lines)


def _overview_page(store: GraphStore, manifest: Manifest | None) -> str:
    nodes = store.all_nodes()
    edges = store.all_edges()

    lines = [
        "---",
        "type: overview",
        "tags: [overview, lorekeep-wiki]",
        "---",
        "",
        "# Graph Overview",
        "",
        "## Statistics",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Nodes | {len(nodes)} |",
        f"| Edges | {len(edges)} |",
    ]

    by_type: dict[str, int] = {}
    for n in nodes:
        by_type[n.type] = by_type.get(n.type, 0) + 1
    for ntype in sorted(by_type):
        lines.append(f"| node:{ntype} | {by_type[ntype]} |")

    by_type_e: dict[str, int] = {}
    for e in edges:
        by_type_e[e.type] = by_type_e.get(e.type, 0) + 1
    for etype in sorted(by_type_e):
        lines.append(f"| edge:{etype} | {by_type_e[etype]} |")

    valid_froms = [n.valid_from for n in nodes if n.valid_from]
    if valid_froms:
        oldest = min(valid_froms)
        newest = max(valid_froms)
        lines.append(f"| Oldest valid_from | {_fmt_date(oldest)} |")
        lines.append(f"| Newest valid_from | {_fmt_date(newest)} |")

    lines.append("")

    if manifest:
        lines.extend([
            "## Compile Info",
            "",
            f"- **Run ID**: `{manifest.run_id}`",
            f"- **Facts hash**: `{manifest.facts_hash}`",
            f"- **Schema version**: {manifest.schema_version}",
            f"- **Chunks compiled**: {manifest.chunk_count}",
        ])
        if manifest.merged_count:
            lines.append(f"- **Agent-merged facts**: {manifest.merged_count}")
        if manifest.quarantined_count:
            lines.append(f"- **Quarantined**: {manifest.quarantined_count}")
        if manifest.review:
            lines.append(f"- **Pending review**: {len(manifest.review)}")
        lines.append("")

    all_ns: set[str] = set()
    for n in nodes:
        all_ns.update(n.ns)
    for e in edges:
        all_ns.update(e.ns)
    if all_ns:
        lines.extend(["## Namespaces", ""])
        for ns in sorted(all_ns):
            node_count = sum(1 for n in nodes if ns in n.ns)
            lines.append(f"- `{ns}` ({node_count} nodes)")
        lines.append("")

    return "\n".join(lines)


def _atomic_dir_swap(wiki_dir: Path, build_dir: Path) -> None:
    """Atomically swap build_dir into wiki_dir.

    On POSIX: rename old wiki to .old, rename new to wiki, rmtree old.
    Never leaves wiki_dir partially populated.
    """
    parent = wiki_dir.parent
    parent.mkdir(parents=True, exist_ok=True)

    old_backup: Path | None = None
    if wiki_dir.exists():
        old_backup = wiki_dir.with_suffix(".wiki-old.tmp")
        if old_backup.exists():
            import shutil
            shutil.rmtree(old_backup)
        os.rename(wiki_dir, old_backup)

    os.rename(build_dir, wiki_dir)

    if old_backup is not None and old_backup.exists():
        import shutil
        shutil.rmtree(old_backup)


def generate_wiki(
    graph_dir: Path,
    wiki_dir: Path,
    manifest: Manifest | None = None,
) -> dict:
    """Generate Obsidian-compatible wiki pages from facts.jsonl.

    Builds into a temp sibling directory, then atomically swaps into place.
    Appends to log.md (the only non-deterministic file, preserved across regen).

    Returns a summary dict with counts.
    """
    facts_path = graph_dir / "facts.jsonl"
    if not facts_path.exists():
        return {"error": f"facts.jsonl not found at {facts_path}"}

    if manifest is None:
        manifest_path = graph_dir / "manifest.json"
        if manifest_path.exists():
            manifest = Manifest.from_json(manifest_path.read_text(encoding="utf-8"))

    store = GraphStore.from_jsonl(facts_path)
    nodes = store.all_nodes()
    edges = store.all_edges()

    existing_log = ""
    if (wiki_dir / "log.md").exists():
        existing_log = (wiki_dir / "log.md").read_text(encoding="utf-8")

    build_dir = wiki_dir.parent / ".wiki-build.tmp"
    if build_dir.exists():
        import shutil
        shutil.rmtree(build_dir)
    build_dir.mkdir(parents=True, exist_ok=True)

    slug_map: dict[str, str] = {}
    for node in nodes:
        slug = _slug(node.id)
        if slug in slug_map and slug_map[slug] != node.id:
            raise ValueError(
                f"slug collision: nodes {slug_map[slug]!r} and {node.id!r} "
                f"both slug to {slug!r}"
            )
        slug_map[slug] = node.id

    for node in sorted(nodes, key=lambda n: (n.type, n.id)):
        page = _entity_page(node, store)
        slug = _slug(node.id)
        entity_path = build_dir / "entities" / node.type / f"{slug}.md"
        _atomic_write(entity_path, page)

    _atomic_write(build_dir / "index.md", _index_page(store))
    _atomic_write(build_dir / "overview.md", _overview_page(store, manifest))

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    run_id = manifest.run_id if manifest else "unknown"
    entry = (
        f"## [{now}] wiki | run_id={run_id}, "
        f"{len(nodes)} nodes, {len(edges)} edges\n"
    )
    if not existing_log:
        existing_log = "# Lorekeep Wiki \u2014 Log\n\n"
    _atomic_write(build_dir / "log.md", existing_log + entry)

    _atomic_dir_swap(wiki_dir, build_dir)

    return {
        "nodes": len(nodes),
        "edges": len(edges),
        "pages": len(nodes) + 2,
    }
