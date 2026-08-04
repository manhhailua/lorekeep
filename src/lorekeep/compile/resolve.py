"""Resolve: dedup entities, validate edges, enforce ns, quarantine bad facts.

Extraction may emit the same entity under several ids (aliases). This stage
collapses them onto one canonical id, rewrites edge endpoints, drops edges whose
endpoints disappeared, and quarantines malformed facts for review.

Journal merge: loads pending journal entries, gates by confidence, merges into
the existing graph with priority: raw/ > import > agent-propose.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field

from lorekeep.models import Edge, JournalEntry, Node, Schema


@dataclass
class ResolveResult:
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    aliases: dict[str, str] = field(default_factory=dict)      # alias_id -> canonical_id
    quarantined: list[tuple[dict, str]] = field(default_factory=list)


def _normalize_id(node_id: str) -> str:
    """Canonical form for duplicate detection.

    Lowercase, ``_`` and space -> ``-``. Diacritics (Vietnamese etc.) are
    PRESERVED — ``person:nguyễn`` stays distinct from ``person:nguyen``. So
    ``concept:context_purity`` == ``concept:Context Purity`` ==
    ``concept:context-purity`` (all merge), but diacritic differences do not.
    """
    normalized = unicodedata.normalize("NFC", node_id).lower()
    return re.sub(r"[-_\s]+", "-", normalized)


def _build_alias_map(
    nodes: list[Node],
    name_aliases: dict[str, list[str]] | None,
    explicit_map: dict[str, str] | None,
) -> dict[str, str]:
    """Return alias_id -> canonical_id with deterministic normalized ids."""
    alias_map: dict[str, str] = {}
    # 1) by display name: include the canonical name itself as well as variants.
    #    Prefer the node whose label is the canonical name, and never merge equal
    #    labels across different ontology types.
    if name_aliases:
        for canonical_name, variants in sorted(name_aliases.items()):
            canonical_key = _normalize_text(canonical_name).casefold()
            surface_keys = {
                _normalize_text(value).casefold()
                for value in (canonical_name, *variants)
                if isinstance(value, str) and value.strip()
            }
            by_type: dict[str, list[Node]] = {}
            for nd in nodes:
                label = nd.props.get("name") or nd.props.get("title")
                if (
                    isinstance(label, str)
                    and _normalize_text(label).casefold() in surface_keys
                ):
                    by_type.setdefault(nd.type, []).append(nd)
            for matches in by_type.values():
                exact = [
                    nd for nd in matches
                    if _normalize_text(
                        str(nd.props.get("name") or nd.props.get("title") or "")
                    ).casefold() == canonical_key
                ]
                candidates = exact or matches
                canonical_slug = re.sub(
                    r"[-_\s]+", "-", canonical_key,
                )
                canon = min(
                    candidates,
                    key=lambda nd: (
                        not _normalize_id(nd.id).endswith(f":{canonical_slug}"),
                        _normalize_id(nd.id),
                        nd.id,
                    ),
                ).id
                for nd in matches:
                    if nd.id != canon:
                        alias_map[nd.id] = canon
    # 2) auto-merge by normalized id (case/separator variants; diacritics kept).
    #    The normalized key itself is canonical, so source ordering cannot change
    #    stored ids across devices.
    for nd in nodes:
        canon = _normalize_id(nd.id)
        if nd.id != canon and nd.id not in alias_map:
            alias_map[nd.id] = canon
    # 3) explicit id->id overrides win
    if explicit_map:
        alias_map.update(explicit_map)
    return alias_map


def _normalize_text(value: str) -> str:
    """Collapse prose whitespace while preserving paragraph boundaries."""
    paragraphs = [
        " ".join(part.split())
        for part in re.split(r"\n\s*\n", value.strip())
        if part.strip()
    ]
    return "\n\n".join(paragraphs)


def _richer_summary(left: object, right: object) -> object:
    """Choose the most informative summary with an order-independent tie-break."""
    if not isinstance(left, str) or not left.strip():
        return _normalize_text(right) if isinstance(right, str) else right
    if not isinstance(right, str) or not right.strip():
        return _normalize_text(left)
    choices = (_normalize_text(left), _normalize_text(right))
    return max(
        choices,
        key=lambda text: (len(set(text.casefold().split())), len(text), text.casefold()),
    )


def _merge_descriptions(left: object, right: object) -> object:
    """Combine grounded prose without retaining exact or contained repeats."""
    if not isinstance(left, str) or not left.strip():
        return _normalize_text(right) if isinstance(right, str) else right
    if not isinstance(right, str) or not right.strip():
        return _normalize_text(left)

    paragraphs: list[str] = []
    for value in (left, right):
        for paragraph in _normalize_text(value).split("\n\n"):
            folded = paragraph.casefold()
            if any(folded in existing.casefold() for existing in paragraphs):
                continue
            containing = [
                index for index, existing in enumerate(paragraphs)
                if existing.casefold() in folded
            ]
            if containing:
                first = containing[0]
                paragraphs = [
                    existing for index, existing in enumerate(paragraphs)
                    if index not in containing
                ]
                paragraphs.insert(first, paragraph)
            else:
                paragraphs.append(paragraph)
    return "\n\n".join(paragraphs)


def _merge_props(
    base: dict,
    incoming: dict,
    *,
    prefer_existing: bool = False,
) -> dict:
    """Merge fact properties while treating human prose as durable content."""
    merged = dict(base)
    for key, value in incoming.items():
        if prefer_existing and key in merged:
            continue
        if key == "summary" and key in merged:
            merged[key] = _richer_summary(merged[key], value)
        elif key == "description" and key in merged:
            merged[key] = _merge_descriptions(merged[key], value)
        elif not prefer_existing or key not in merged:
            merged[key] = value
    return merged


def _stable_union(*values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(sorted({item for group in values for item in group}))


def _canonical(node_id: str, alias_map: dict[str, str]) -> str:
    seen: set[str] = set()
    cur = node_id
    while cur in alias_map and cur not in seen:
        seen.add(cur)
        cur = alias_map[cur]
    return cur


def resolve(
    nodes: list[Node],
    edges: list[Edge],
    name_aliases: dict[str, list[str]] | None = None,
    aliases_map: dict[str, str] | None = None,
    schema: Schema | None = None,
) -> ResolveResult:
    quarantined: list[tuple[dict, str]] = []
    if schema is not None:
        valid_nodes = []
        for node in nodes:
            if schema.is_valid_node_type(node.type):
                valid_nodes.append(node)
            else:
                quarantined.append((
                    node.model_dump(mode="json", by_alias=True),
                    f"unknown node type ({node.type})",
                ))
        nodes = valid_nodes

    alias_map = _build_alias_map(nodes, name_aliases, aliases_map)

    # collapse nodes
    canon_nodes: dict[str, Node] = {}
    node_inputs = sorted(
        nodes,
        key=lambda nd: (
            _canonical(nd.id, alias_map),
            nd.src,
            nd.id,
            json.dumps(nd.props, sort_keys=True, ensure_ascii=False, default=str),
        ),
    )
    for nd in node_inputs:
        cid = _canonical(nd.id, alias_map)
        if cid in canon_nodes:
            base = canon_nodes[cid]
            merged_props = _merge_props(base.props, nd.props)
            merged_src = _stable_union(base.src, nd.src)
            merged_ns = _stable_union(base.ns, nd.ns)
            canon_nodes[cid] = base.model_copy(
                update={"props": merged_props, "src": merged_src, "ns": merged_ns}
            )
        else:
            # normalize stored node id to the canonical key so node identity and
            # dict key can never diverge (covers explicit_map to a non-node id)
            canon_nodes[cid] = nd if nd.id == cid else nd.model_copy(update={"id": cid})

    out_nodes = [canon_nodes[node_id] for node_id in sorted(canon_nodes)]
    node_ids = set(canon_nodes.keys())

    # rewrite + validate edges
    edge_groups: dict[tuple[str, str, str, str, str], Edge] = {}
    edge_inputs = sorted(
        edges,
        key=lambda edge: (
            edge.type,
            _canonical(edge.from_, alias_map),
            _canonical(edge.to, alias_map),
            edge.valid_from.isoformat() if edge.valid_from else "",
            edge.valid_to.isoformat() if edge.valid_to else "",
            edge.src,
            json.dumps(edge.props, sort_keys=True, ensure_ascii=False, default=str),
            edge.id,
        ),
    )
    for ed in edge_inputs:
        f = _canonical(ed.from_, alias_map)
        t = _canonical(ed.to, alias_map)
        if f not in node_ids or t not in node_ids:
            quarantined.append((ed.model_dump(mode="json", by_alias=True),
                                f"dangling endpoint ({f}->{t})"))
            continue
        if f == t:
            quarantined.append((ed.model_dump(mode="json", by_alias=True),
                                "self-loop"))
            continue
        if schema is not None:
            from_type = canon_nodes[f].type
            to_type = canon_nodes[t].type
            if not schema.is_valid_edge_endpoints(ed.type, from_type, to_type):
                quarantined.append((
                    ed.model_dump(mode="json", by_alias=True),
                    f"invalid edge endpoints for {ed.type} "
                    f"({from_type}->{to_type})",
                ))
                continue
        key = (
            ed.type,
            f,
            t,
            ed.valid_from.isoformat() if ed.valid_from else "",
            ed.valid_to.isoformat() if ed.valid_to else "",
        )
        normalized = ed.model_copy(update={"from_": f, "to": t})
        if key in edge_groups:
            base = edge_groups[key]
            edge_groups[key] = base.model_copy(update={
                "props": _merge_props(base.props, normalized.props),
                "src": _stable_union(base.src, normalized.src),
                "ns": _stable_union(base.ns, normalized.ns),
            })
        else:
            edge_groups[key] = normalized

    out_edges = [
        edge_groups[key].model_copy(update={"id": f"e_{key[0]}_{counter:04d}"})
        for counter, key in enumerate(sorted(edge_groups), start=1)
    ]

    return ResolveResult(
        nodes=out_nodes,
        edges=out_edges,
        aliases=alias_map,
        quarantined=quarantined,
    )


@dataclass
class JournalMergeResult:
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    merged: list[tuple[JournalEntry, str]] = field(default_factory=list)
    flagged: list[tuple[JournalEntry, str]] = field(default_factory=list)
    quarantined: list[tuple[JournalEntry, str]] = field(default_factory=list)

    @property
    def merge_count(self) -> int:
        return len(self.merged)

    @property
    def flagged_count(self) -> int:
        return len(self.flagged)

    @property
    def quarantine_count(self) -> int:
        return len(self.quarantined)


def merge_journals(
    existing_nodes: list[Node],
    existing_edges: list[Edge],
    journal_entries: list[JournalEntry],
    *,
    replay_accepted: bool = False,
    schema: Schema | None = None,
) -> JournalMergeResult:
    """Gate journal entries by confidence and add to the graph.

    High (>=0.8): auto-merge. Medium (0.5 to <0.8): merge + flag for review.
    Low (<0.5): quarantine, do not merge.
    """
    result = JournalMergeResult()
    nodes_by_id: dict[str, Node] = {n.id: n for n in existing_nodes}
    new_edges: list[tuple[Edge, bool]] = []

    ordered_entries = sorted(
        journal_entries,
        key=lambda entry: (
            entry.fact.get("kind") == "edge",
            entry.entry_id or entry.proposed_at,
            entry.agent,
            entry.fact.get("id", ""),
        ),
    )
    for entry in ordered_entries:
        replaying = replay_accepted and entry.status in {"merged", "flagged"}
        if entry.status != "pending" and not replaying:
            continue
        confidence = entry.confidence
        fact_data = entry.fact
        try:
            if fact_data["kind"] == "node":
                fact = Node.model_validate(fact_data)
            else:
                fact = Edge.model_validate(fact_data)
        except Exception:
            result.quarantined.append((entry, "invalid fact schema"))
            continue

        if confidence < 0.5 and not replaying:
            result.quarantined.append((entry, "low confidence"))
            continue

        if schema is not None and fact.kind == "node":
            if not schema.is_valid_node_type(fact.type):
                result.quarantined.append((entry, f"unknown node type: {fact.type}"))
                continue

        if schema is not None and fact.kind == "edge":
            from_node = nodes_by_id.get(fact.from_)
            to_node = nodes_by_id.get(fact.to)
            if (
                from_node is None
                or to_node is None
                or not schema.is_valid_edge_endpoints(
                    fact.type, from_node.type, to_node.type,
                )
            ):
                result.quarantined.append((entry, "invalid edge endpoints"))
                continue

        if fact.kind == "node":
            if fact.id in nodes_by_id:
                base = nodes_by_id[fact.id]
                merged_props = _merge_props(
                    base.props, fact.props, prefer_existing=replaying,
                )
                merged_src = _stable_union(base.src, fact.src)
                merged_ns = _stable_union(base.ns, fact.ns)
                nodes_by_id[fact.id] = base.model_copy(
                    update={"props": merged_props, "src": merged_src, "ns": merged_ns}
                )
            else:
                nodes_by_id[fact.id] = fact
        else:
            new_edges.append((fact, replaying))

        if replaying:
            continue
        if confidence >= 0.8:
            result.merged.append((entry, ""))
        else:
            result.flagged.append((entry, "medium confidence, flagged for review"))

    result.nodes = list(nodes_by_id.values())

    # Deduplicate + ID-regenerate edges (journal edges have empty id)
    edge_by_key: dict[tuple[str, str, str, str, str], Edge] = {}
    counter = 0
    edge_inputs = [(edge, False) for edge in existing_edges] + new_edges
    for e, replaying in edge_inputs:
        key = (
            e.from_,
            e.to,
            e.type,
            e.valid_from.isoformat() if e.valid_from else "",
            e.valid_to.isoformat() if e.valid_to else "",
        )
        if key in edge_by_key:
            # Merge props and src for duplicate edges
            existing = edge_by_key[key]
            merged_props = _merge_props(
                existing.props, e.props, prefer_existing=replaying,
            )
            merged_src = _stable_union(existing.src, e.src)
            edge_by_key[key] = existing.model_copy(
                update={"props": merged_props, "src": merged_src}
            )
        else:
            counter += 1
            eid = e.id if e.id else f"e_{e.type}_{counter:04d}"
            edge_by_key[key] = e if e.id else e.model_copy(update={"id": eid})

    result.edges = list(edge_by_key.values())
    return result
