"""Generate Obsidian-compatible markdown wiki from facts.jsonl.

The wiki is a human-browsable projection of the compiled knowledge graph.
It is fully derived from facts.jsonl — never the reverse. Re-generating
from unchanged input yields byte-identical pages (except log.md, which
is append-only by design).

Output structure (flat — one .md per node at the root, browsable in BOTH
Obsidian and Tolaria from the same folder):
    wiki/
    ├── index.md                # human-first landing page
    ├── catalog.md              # all entities, grouped by type
    ├── log.md                  # append-only generation log
    ├── overview.md             # graph stats dashboard
    └── <slug>.md               # one page per node, [[wikilinks]] for edges,
                                #   out-edges also as frontmatter relationship
                                #   fields (Tolaria relationship panel)
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lorekeep.compile.writer import _atomic_write
from lorekeep.models import Edge, Manifest, Node, Schema
from lorekeep.store.graph import GraphStore


_RESERVED_FRONTMATTER_KEYS = frozenset({
    "kind",
    "id",
    "type",
    "ns",
    "valid_from",
    "valid_to",
    "sources",
    "tags",
    "aliases",
    "props",
})


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
    return json.dumps(str(value), ensure_ascii=False)


def _yaml_list(items: list[str]) -> str:
    if not items:
        return "[]"
    return "[" + ", ".join(json.dumps(str(i), ensure_ascii=False) for i in items) + "]"


def _yaml_value(value: Any) -> str:
    """Render JSON-compatible data as deterministic, YAML-compatible syntax."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


def _yaml_key(value: str) -> str:
    """Keep ordinary ontology keys readable and quote YAML-ambiguous keys."""
    key = str(value)
    ambiguous = {"null", "true", "false", "yes", "no", "on", "off"}
    if (
        re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", key)
        and key.lower() not in ambiguous
    ):
        return key
    return _yaml_scalar(key)


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


def _humanize(value: str) -> str:
    return " ".join(part for part in re.split(r"[_-]+", value) if part).title()


def _node_type_label(node_type: str, schema: Schema | None = None) -> str:
    spec = schema.node_types.get(node_type) if schema else None
    return spec.label if spec and spec.label else _humanize(node_type)


def _node_type_plural(node_type: str, schema: Schema | None = None) -> str:
    spec = schema.node_types.get(node_type) if schema else None
    if spec and spec.plural:
        return spec.plural
    label = _node_type_label(node_type, schema)
    return label if label.endswith("s") else f"{label}s"


def _node_title(node: Node, schema: Schema | None = None) -> str:
    """Return the human label using the ontology's name/title conventions.

    Most ontology node types use ``props.name``. Goals, decisions, and
    documents use ``props.title`` instead, so treating ``name`` as the only
    display field makes a correct v2 fact look like an opaque ID in the wiki.
    """
    spec = schema.node_types.get(node.type) if schema else None
    preferred = spec.display_prop if spec and spec.display_prop else None
    for key in dict.fromkeys(key for key in (preferred, "name", "title") if key):
        value = node.props.get(key)
        if value is not None:
            label = str(value).strip()
            if label:
                return " ".join(label.split())
    return node.id


def _node_aliases(node: Node) -> list[str]:
    """Return all distinct human labels carried by the ontology fact."""
    aliases: list[str] = []
    for key in ("name", "title"):
        value = node.props.get(key)
        if value is None:
            continue
        alias = str(value).strip()
        if alias and alias != node.id and alias not in aliases:
            aliases.append(alias)
    return aliases


def _wikilink(node: Node, schema: Schema | None = None) -> str:
    """Return a readable Obsidian link while retaining the stable file slug."""
    label = " ".join(_node_title(node, schema).split()).replace("|", "\\|")
    return f"[[{_slug(node.id)}|{label}]]"


def _frontmatter(node: Node, out_edges: list[Edge] | None = None) -> str:
    out_edges = out_edges or []
    lines = ["---"]
    lines.append(f"kind: {_yaml_scalar(node.kind)}")
    lines.append(f"id: {_yaml_scalar(node.id)}")
    lines.append(f"type: {_yaml_scalar(node.type)}")
    lines.append(f"ns: {_yaml_list(list(node.ns))}")
    lines.append(f"valid_from: {_yaml_scalar(_fmt_date(node.valid_from))}")
    lines.append(f"valid_to: {_yaml_scalar(_fmt_date(node.valid_to))}")
    if node.src:
        lines.append("sources:")
        for s in node.src:
            lines.append(f"  - {json.dumps(str(s), ensure_ascii=False)}")
    else:
        lines.append("sources: []")
    tags = [node.type] + list(node.ns) + ["entity"]
    lines.append(f"tags: {_yaml_list(tags)}")
    aliases = _node_aliases(node)
    if aliases:
        # Obsidian resolves aliases to the same stable file, so humans can
        # search/link by the ontology's display property without losing the
        # canonical fact ID in the filename/frontmatter.
        lines.append(f"aliases: {_yaml_list(aliases)}")

    # ``props`` is the canonical, lossless projection of the node properties.
    # Safe keys are mirrored at the top level for ergonomic Obsidian/Dataview
    # queries. Reserved metadata remains authoritative; a custom prop with the
    # same name is still available under ``props``.
    if node.props:
        lines.append("props:")
        for key in sorted(node.props):
            lines.append(
                f"  {_yaml_key(key)}: {_yaml_value(node.props[key])}"
            )
    else:
        lines.append("props: {}")

    mirrored_prop_keys: set[str] = set()
    for key in sorted(node.props):
        if key in _RESERVED_FRONTMATTER_KEYS:
            continue
        lines.append(f"{_yaml_key(key)}: {_yaml_value(node.props[key])}")
        mirrored_prop_keys.add(key)

    # Out-edges as relationship frontmatter fields. Tolaria detects any
    # frontmatter field holding [[wikilink]] values as a relationship (panel +
    # neighborhood graph); Obsidian/Dataview treat them as queryable lists.
    # Inbound edges stay body-only (both apps surface them as backlinks).
    if out_edges:
        by_type: dict[str, list[str]] = {}
        for e in out_edges:
            by_type.setdefault(e.type, []).append(_slug(e.to))
        used_keys = set(_RESERVED_FRONTMATTER_KEYS) | mirrored_prop_keys
        for etype in sorted(by_type):
            field = etype
            if field in used_keys:
                base = f"relation_{etype}"
                field = base
                suffix = 2
                while field in used_keys:
                    field = f"{base}_{suffix}"
                    suffix += 1
            used_keys.add(field)
            targets = sorted(set(by_type[etype]))
            lines.append(f"{_yaml_key(field)}:")
            for t in targets:
                lines.append(f'  - "[[{t}]]"')
    lines.append("---")
    return "\n".join(lines)


_HUMAN_TEXT_PROPS = frozenset({"name", "title", "summary", "description"})
_PROP_PRIORITY = (
    "status",
    "role",
    "org",
    "lang",
    "level",
    "timeframe",
    "start_date",
    "decided_on",
    "kind",
    "domain",
)


def _body_text(value: Any) -> str:
    if isinstance(value, str):
        return " ".join(value.split())
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _summary_text(node: Node, *, limit: int = 220) -> str:
    """Return extracted summary, then a legacy-fact description fallback."""
    value = node.props.get("summary")
    if value is None or not str(value).strip():
        value = node.props.get("description")
    summary = _body_text(value) if value is not None else ""
    if not summary:
        return ""
    if len(summary) <= limit:
        return summary
    return summary[:limit - 1].rstrip() + "…"


def _description(node: Node) -> str:
    """Render detailed prose in the body, below the one-line lead."""
    value = node.props.get("description")
    if value is None:
        return ""
    text = str(value).replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return ""
    summary = _summary_text(node)
    if "\n" not in text and summary and summary == " ".join(text.split()):
        return ""
    return "\n".join(["", "## About", "", text])


def _friendly_key(key: str) -> str:
    return _humanize(key).replace(" Id", " ID")


def _at_a_glance(node: Node, schema: Schema | None = None) -> str:
    """Render meaningful entity attributes as scan-friendly bullets."""
    keys = [key for key in node.props if key not in _HUMAN_TEXT_PROPS]
    priority = {key: index for index, key in enumerate(_PROP_PRIORITY)}
    keys.sort(key=lambda key: (priority.get(key, len(priority)), key))
    lines = ["", "## At a glance", ""]
    lines.append(f"- **Type:** {_node_type_label(node.type, schema)}")
    for key in keys:
        lines.append(f"- **{_friendly_key(key)}:** {_body_text(node.props[key])}")
    return "\n".join(lines)


def _relation_label(
    edge_type: str,
    *,
    outgoing: bool,
    schema: Schema | None = None,
) -> str:
    spec = schema.edge_types.get(edge_type) if schema else None
    if spec:
        label = spec.label if outgoing else spec.inverse_label
        if label:
            return label
    return _humanize(edge_type).capitalize()


def _edge_description(edge: Edge) -> str:
    for key in ("description", "reason", "context", "note"):
        value = edge.props.get(key)
        if value is not None and str(value).strip():
            return _body_text(value)
    return ""


def _relationships(
    out_edges: list[Edge],
    in_edges: list[Edge],
    store: GraphStore,
    schema: Schema | None = None,
) -> str:
    """Render graph context as natural, described wikilink bullets."""
    if not out_edges and not in_edges:
        return ""

    grouped: dict[tuple[str, bool], list[Edge]] = {}
    for edge in out_edges:
        grouped.setdefault((edge.type, True), []).append(edge)
    for edge in in_edges:
        grouped.setdefault((edge.type, False), []).append(edge)

    sections = ["", "## Connections", ""]
    ordered_groups = sorted(
        grouped,
        key=lambda key: (
            _relation_label(key[0], outgoing=key[1], schema=schema),
            not key[1],
            key[0],
        ),
    )
    for edge_type, outgoing in ordered_groups:
        sections.append(
            f"### {_relation_label(edge_type, outgoing=outgoing, schema=schema)}"
        )
        sections.append("")
        edges = sorted(
            grouped[(edge_type, outgoing)],
            key=lambda edge: ((edge.to if outgoing else edge.from_), edge.id),
        )
        for edge in edges:
            other_id = edge.to if outgoing else edge.from_
            other = store.get_node(other_id)
            link = _wikilink(other, schema) if other else f"`{other_id}`"
            line = f"- {link}"
            description = _edge_description(edge)
            if description:
                line += f" — {description}"
            validity = _fmt_validity(edge.valid_from, edge.valid_to)
            if validity != "always":
                line += f" _({validity})_"
            sections.append(line)
        sections.append("")
    return "\n".join(sections).rstrip()


def _timeline(node: Node) -> str:
    if node.valid_from is None and node.valid_to is None:
        return ""
    lines = ["", "## Timeline", ""]
    if node.valid_from:
        lines.append(f"- **{_fmt_date(node.valid_from)}**: Valid from")
    if node.valid_to:
        lines.append(f"- **{_fmt_date(node.valid_to)}**: Valid until")
    return "\n".join(lines)


def _sources(node: Node) -> str:
    if not node.src:
        return ""
    lines = ["", "## Sources", ""]
    lines.extend(f"- `{source}`" for source in node.src)
    return "\n".join(lines)


def _technical_details(
    node: Node,
    out_edges: list[Edge],
    in_edges: list[Edge],
    store: GraphStore,
    schema: Schema | None = None,
) -> str:
    lines = [
        "",
        "## Technical details",
        "",
        f"- **Fact ID:** `{node.id}`",
        f"- **Namespaces:** {_body_text(list(node.ns))}",
        f"- **Validity:** {_fmt_validity(node.valid_from, node.valid_to)}",
    ]
    touching = [(edge, True) for edge in out_edges] + [
        (edge, False) for edge in in_edges
    ]
    if touching:
        lines.extend(["", "### Relationship facts", ""])
        for edge, outgoing in sorted(touching, key=lambda item: item[0].id):
            other_id = edge.to if outgoing else edge.from_
            other = store.get_node(other_id)
            other_label = _node_title(other, schema) if other else other_id
            relation = _relation_label(edge.type, outgoing=outgoing, schema=schema)
            detail = f"- `{edge.id}` — {relation} {other_label}"
            metadata = [f"namespaces: {_body_text(list(edge.ns))}"]
            validity = _fmt_validity(edge.valid_from, edge.valid_to)
            if validity != "always":
                metadata.append(f"validity: {validity}")
            if edge.src:
                metadata.append(
                    "sources: " + ", ".join(f"`{source}`" for source in edge.src)
                )
            if edge.props:
                metadata.append(f"properties: `{_body_text(edge.props)}`")
            detail += "; " + "; ".join(metadata)
            lines.append(detail)
    return "\n".join(lines)


def _entity_page(
    node: Node, store: GraphStore, schema: Schema | None = None,
) -> str:
    title = _node_title(node, schema)
    out_e = store.out_edges(node.id)
    in_e = store.in_edges(node.id)
    summary = _summary_text(node)
    if not summary:
        summary = f"{title} — {_node_type_label(node.type, schema)}."

    sections = [
        _frontmatter(node, out_e),
        f"# {title}",
        f"> {summary}",
        _description(node).strip(),
        _at_a_glance(node, schema).strip(),
        _relationships(out_e, in_e, store, schema).strip(),
        _timeline(node).strip(),
        _sources(node).strip(),
        _technical_details(node, out_e, in_e, store, schema).strip(),
    ]
    return "\n\n".join(section for section in sections if section) + "\n"


def _catalog_page(store: GraphStore, schema: Schema | None = None) -> str:
    nodes = store.all_nodes()
    edge_count = len(store.all_edges())

    lines = [
        "---",
        "type: catalog",
        "tags: [catalog, lorekeep-wiki]",
        "---",
        "",
        "# Entity Catalog",
        "",
        f"Nodes: {len(nodes)} | Edges: {edge_count}",
        "",
    ]

    by_type: dict[str, list[Node]] = {}
    for n in nodes:
        by_type.setdefault(n.type, []).append(n)

    for ntype in sorted(by_type):
        lines.append(f"## {_node_type_plural(ntype, schema)}")
        lines.append("")
        for node in sorted(
            by_type[ntype],
            key=lambda item: (_node_title(item, schema).casefold(), item.id),
        ):
            lines.append(_catalog_line(node, schema))
        lines.append("")

    return "\n".join(lines)


def _schema_warning(
    manifest: Manifest | None, schema: Schema | None,
) -> list[str]:
    if not manifest or not schema or manifest.schema_version >= schema.version:
        return []
    return [
        "> [!warning] Graph schema is out of date",
        "> This graph was compiled with schema "
        f"v{manifest.schema_version}, while the current schema is v{schema.version}. "
        "Run `lorekeep compile` to enrich the facts before judging wiki quality.",
        "",
    ]


def _quality_warning(manifest: Manifest | None) -> list[str]:
    quality = manifest.content_quality if manifest else None
    if not quality:
        return []
    if (
        quality.node_summary_coverage >= 1.0
        and quality.edge_description_coverage >= 1.0
        and quality.duplicate_label_count == 0
    ):
        return []
    return [
        "> [!note] Content quality needs attention",
        "> Human summaries cover "
        f"{quality.node_summary_coverage:.0%} of entities; relationship explanations "
        f"cover {quality.edge_description_coverage:.0%} of edges; duplicate labels: "
        f"{quality.duplicate_label_count}. Recompile source documents to improve this view.",
        "",
    ]


def _catalog_line(node: Node, schema: Schema | None = None) -> str:
    line = f"- {_wikilink(node, schema)}"
    details: list[str] = []
    summary = _summary_text(node, limit=180)
    if summary:
        details.append(summary)
    status = node.props.get("status")
    if status is not None and str(status).strip():
        details.append(f"status: {_body_text(status)}")
    if node.valid_from:
        details.append(f"since {_fmt_date(node.valid_from)}")
    if details:
        line += " — " + " · ".join(details)
    return line


def _index_page(
    store: GraphStore,
    manifest: Manifest | None = None,
    schema: Schema | None = None,
) -> str:
    nodes = store.all_nodes()
    edges = store.all_edges()
    lines = [
        "---",
        "type: index",
        "tags: [index, lorekeep-wiki]",
        "---",
        "",
        "# Lorekeep Wiki",
        "",
        "A human-readable view of the compiled temporal knowledge graph.",
        "",
        f"**{len(nodes)} entities · {len(edges)} connections**",
        "",
    ]
    lines.extend(_schema_warning(manifest, schema))
    lines.extend(_quality_warning(manifest))
    lines.extend([
        "- [[catalog|Browse every entity]]",
        "- [[overview|Inspect graph quality and compile details]]",
        "- [[log|View generation history]]",
        "",
    ])

    inactive_statuses = {
        "done", "complete", "completed", "closed", "cancelled", "archived",
    }
    current_work = [
        node for node in nodes
        if node.type in {"goal", "project"}
        and str(node.props.get("status", "")).strip().casefold()
        not in inactive_statuses
    ]
    if current_work:
        lines.extend(["## Goals and projects", ""])
        for node in sorted(
            current_work,
            key=lambda item: (
                item.type, _node_title(item, schema).casefold(), item.id,
            ),
        )[:12]:
            lines.append(_catalog_line(node, schema))
        lines.append("")

    decisions = [node for node in nodes if node.type == "decision"]
    if decisions:
        lines.extend(["## Decisions", ""])
        for node in sorted(
            decisions,
            key=lambda item: (
                str(item.props.get("decided_on") or _fmt_date(item.valid_from)),
                item.id,
            ),
            reverse=True,
        )[:10]:
            lines.append(_catalog_line(node, schema))
        lines.append("")

    people = [node for node in nodes if node.type in {"person", "team"}]
    if people:
        lines.extend(["## People and teams", ""])
        for node in sorted(
            people,
            key=lambda item: (_node_title(item, schema).casefold(), item.id),
        )[:12]:
            lines.append(_catalog_line(node, schema))
        lines.append("")

    hubs = sorted(
        (
            (len(store.out_edges(node.id)) + len(store.in_edges(node.id)), node)
            for node in nodes
        ),
        key=lambda item: (
            -item[0], _node_title(item[1], schema).casefold(), item[1].id,
        ),
    )
    hubs = [(degree, node) for degree, node in hubs if degree > 0][:10]
    if hubs:
        lines.extend(["## Key connected entities", ""])
        for degree, node in hubs:
            lines.append(f"- {_wikilink(node, schema)} — {degree} connections")
        lines.append("")

    by_type: dict[str, int] = {}
    for node in nodes:
        by_type[node.type] = by_type.get(node.type, 0) + 1
    if by_type:
        lines.extend(["## Browse by type", ""])
        for node_type in sorted(by_type):
            plural = _node_type_plural(node_type, schema)
            lines.append(f"- [[catalog#{plural}|{plural}]] ({by_type[node_type]})")
        lines.append("")
    return "\n".join(lines)


def _overview_page(
    store: GraphStore,
    manifest: Manifest | None,
    schema: Schema | None = None,
) -> str:
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
        *_schema_warning(manifest, schema),
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

        if manifest.content_quality:
            quality = manifest.content_quality
            lines.extend([
                "## Content Quality",
                "",
                "| Measure | Coverage |",
                "|---|---:|",
                f"| Entity labels | {quality.node_label_coverage:.0%} |",
                f"| Entity summaries | {quality.node_summary_coverage:.0%} |",
                f"| Entity descriptions | {quality.node_description_coverage:.0%} |",
                f"| Relationship descriptions | {quality.edge_description_coverage:.0%} |",
                f"| Generic relationships | {quality.generic_edge_ratio:.0%} |",
                f"| Duplicate labels | {quality.duplicate_label_count} |",
                "",
            ])

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


def _rollback_publish(
    wiki_dir: Path,
    backup_dir: Path,
    original_pages: set[str],
    attempted_pages: set[str],
) -> None:
    """Restore every page touched by a failed publish from its snapshot."""
    errors: list[Exception] = []

    for name in sorted(attempted_pages - original_pages):
        destination = wiki_dir / name
        try:
            if destination.exists():
                destination.unlink()
        except Exception as exc:
            errors.append(exc)

    for name in sorted(attempted_pages & original_pages):
        backup = backup_dir / name
        destination = wiki_dir / name
        restore_tmp: Path | None = None
        try:
            fd, tmp_name = tempfile.mkstemp(
                dir=wiki_dir,
                prefix=".lorekeep-rollback-",
                suffix=".tmp",
            )
            os.close(fd)
            restore_tmp = Path(tmp_name)
            shutil.copy2(backup, restore_tmp)
            os.replace(restore_tmp, destination)
        except Exception as exc:
            errors.append(exc)
        finally:
            if restore_tmp is not None and restore_tmp.exists():
                try:
                    restore_tmp.unlink()
                except Exception as exc:
                    errors.append(exc)

    if errors:
        raise ExceptionGroup("wiki rollback failed", errors)


def _publish_build(wiki_dir: Path, build_dir: Path) -> None:
    """Publish staged pages while keeping the vault directory stable.

    Obsidian watches the vault root. Replacing that directory breaks its file
    watcher and removes its ``.obsidian`` settings, leaving notes visible in
    the sidebar but blank when opened. Snapshot all current markdown pages,
    then replace each staged page and remove stale pages. A failure during
    either phase rolls every attempted change back to the snapshot.
    Non-markdown content (including ``.obsidian``) is left untouched.
    """
    backup_dir = wiki_dir.parent / ".wiki-rollback.tmp"
    backup_created = False
    preserve_backup = False
    try:
        wiki_dir.mkdir(parents=True, exist_ok=True)
        if backup_dir.exists():
            raise RuntimeError(
                f"wiki recovery snapshot already exists at {backup_dir}; "
                "inspect or remove it before regenerating"
            )

        staged_pages = {path.name for path in build_dir.glob("*.md")}
        original_pages = {path.name for path in wiki_dir.glob("*.md")}
        stale_pages = original_pages - staged_pages

        backup_dir.mkdir(parents=True)
        backup_created = True
        for name in sorted(original_pages):
            shutil.copy2(wiki_dir / name, backup_dir / name)

        attempted_pages: set[str] = set()
        try:
            for name in sorted(staged_pages):
                attempted_pages.add(name)
                os.replace(build_dir / name, wiki_dir / name)
            for name in sorted(stale_pages):
                attempted_pages.add(name)
                (wiki_dir / name).unlink()
        except Exception as publish_error:
            try:
                _rollback_publish(
                    wiki_dir,
                    backup_dir,
                    original_pages,
                    attempted_pages,
                )
            except Exception as rollback_error:
                preserve_backup = True
                raise RuntimeError(
                    "wiki publish and rollback both failed; the original "
                    f"markdown snapshot is preserved at {backup_dir}. "
                    f"Publish error: {publish_error!r}; "
                    f"rollback error: {rollback_error!r}"
                ) from rollback_error
            raise
    finally:
        shutil.rmtree(build_dir, ignore_errors=True)
        if backup_created and not preserve_backup:
            shutil.rmtree(backup_dir, ignore_errors=True)


def generate_wiki(
    graph_dir: Path,
    wiki_dir: Path,
    manifest: Manifest | None = None,
    schema: Schema | None = None,
) -> dict:
    """Generate Obsidian-compatible wiki pages from facts.jsonl.

    Builds into a temp sibling directory, then atomically replaces each page
    without replacing the vault root.
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
        page = _entity_page(node, store, schema)
        slug = _slug(node.id)
        # A portable flat root keeps filename stems and wikilinks identical in
        # Obsidian and Tolaria; both apps can index this layout directly.
        entity_path = build_dir / f"{slug}.md"
        _atomic_write(entity_path, page)

    _atomic_write(build_dir / "index.md", _index_page(store, manifest, schema))
    _atomic_write(build_dir / "catalog.md", _catalog_page(store, schema))
    _atomic_write(
        build_dir / "overview.md", _overview_page(store, manifest, schema),
    )

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    run_id = manifest.run_id if manifest else "unknown"
    entry = (
        f"## [{now}] wiki | run_id={run_id}, "
        f"{len(nodes)} nodes, {len(edges)} edges\n"
    )
    if not existing_log:
        existing_log = "# Lorekeep Wiki \u2014 Log\n\n"
    _atomic_write(build_dir / "log.md", existing_log + entry)

    _publish_build(wiki_dir, build_dir)

    return {
        "nodes": len(nodes),
        "edges": len(edges),
        # One page per node plus the four generated vault pages.
        "pages": len(nodes) + 4,
    }
