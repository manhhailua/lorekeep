"""Pipeline: ingest -> extract -> resolve -> writer."""
from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from lorekeep.compile.extract import ExtractionCache, extract_chunk
from lorekeep.compile.ingest import ingest
from lorekeep.compile.providers import LLMProvider
from lorekeep.compile.resolve import resolve
from lorekeep.compile.writer import facts_hash, run_id, write_graph
from lorekeep.models import (
    ContentQuality,
    DocChunk,
    Edge,
    Manifest,
    Node,
    Schema,
    now_iso,
)

log = logging.getLogger("lorekeep")

# Optional per-chunk progress hook: (index, total, chunk). Default None → silent.
ProgressCb = Callable[[int, int, DocChunk], None]


def measure_content_quality(
    nodes: list[Node], edges: list[Edge], schema: Schema,
) -> ContentQuality:
    """Measure whether the compiled graph can support a human-readable wiki."""
    def populated(value: object) -> bool:
        return isinstance(value, str) and bool(value.strip())

    def ratio(count: int, total: int) -> float:
        return round(count / total, 4) if total else 1.0

    labels: list[tuple[str, str]] = []
    labeled = summarized = described = 0
    for node in nodes:
        spec = schema.node_types.get(node.type)
        display_prop = spec.display_prop if spec and spec.display_prop else None
        value = (
            node.props.get(display_prop) if display_prop else None
        ) or node.props.get("name") or node.props.get("title")
        if populated(value):
            labeled += 1
            labels.append((node.type, " ".join(str(value).split()).casefold()))
        summarized += int(populated(node.props.get("summary")))
        described += int(populated(node.props.get("description")))

    label_counts: dict[tuple[str, str], int] = {}
    for key in labels:
        label_counts[key] = label_counts.get(key, 0) + 1
    duplicate_labels = sum(count - 1 for count in label_counts.values() if count > 1)
    described_edges = sum(
        populated(edge.props.get("description")) for edge in edges
    )
    generic_edges = sum(edge.type == "relates_to" for edge in edges)
    return ContentQuality(
        node_label_coverage=ratio(labeled, len(nodes)),
        node_summary_coverage=ratio(summarized, len(nodes)),
        node_description_coverage=ratio(described, len(nodes)),
        edge_description_coverage=ratio(described_edges, len(edges)),
        generic_edge_ratio=(round(generic_edges / len(edges), 4) if edges else 0.0),
        duplicate_label_count=duplicate_labels,
    )


def compile_graph(
    raw_root: Path,
    out_dir: Path,
    schema: Schema,
    provider: LLMProvider,
    cache_path: Path,
    chunk_lines: int = 60,
    on_progress: ProgressCb | None = None,
    personal_ns: str = "me",
) -> Manifest:
    chunks = ingest(raw_root, chunk_lines=chunk_lines)
    cache = ExtractionCache(cache_path)
    total = len(chunks)

    all_nodes: list[Node] = []
    all_edges: list[Edge] = []
    all_aliases: dict[str, list[str]] = {}
    errors = []
    for i, chunk in enumerate(chunks):
        if on_progress is not None:
            on_progress(i, total, chunk)
        try:
            nodes, edges, aliases = extract_chunk(
                chunk, schema, provider, cache, personal_ns=personal_ns,
            )
            all_nodes.extend(nodes)
            all_edges.extend(edges)
            for ak, av in aliases.items():       # union variants, last-writer-wins drops aliases
                all_aliases[ak] = list(dict.fromkeys(all_aliases.get(ak, []) + av))
        except Exception as exc:               # skip-and-log; partial compile is valid
            log.exception("compile: chunk failed path=%s line=%s", chunk.path, chunk.start_line)
            errors.append({"path": chunk.path, "line": chunk.start_line,
                           "message": str(exc)})
    cache.save()

    resolved = resolve(
        all_nodes, all_edges, name_aliases=all_aliases, schema=schema,
    )
    content_quality = measure_content_quality(resolved.nodes, resolved.edges, schema)

    rid = run_id(chunks, schema.version)
    provisional = Manifest(schema_version=schema.version, chunk_count=len(chunks),
                           node_count=len(resolved.nodes), edge_count=len(resolved.edges),
                           run_id=rid, facts_hash="")
    write_graph(out_dir, resolved.nodes, resolved.edges, provisional)
    fh = facts_hash(out_dir)

    chunk_hashes: dict[str, list[str]] = {}
    for c in chunks:
        chunk_hashes[c.hash[:16]] = [n.id for n in resolved.nodes
                                     if c.src in n.src] + [e.id for e in resolved.edges
                                                                    if c.src in e.src]
    manifest = Manifest(
        schema_version=schema.version,
        chunk_count=len(chunks),
        node_count=len(resolved.nodes),
        edge_count=len(resolved.edges),
        run_id=rid,
        facts_hash=fh,
        compiled_at=now_iso(),
        chunk_hashes=chunk_hashes,
        errors=errors,
        quarantine=[{"fact": q[0], "reason": q[1]} for q in resolved.quarantined],
        content_quality=content_quality,
    )
    write_graph(out_dir, resolved.nodes, resolved.edges, manifest)
    return manifest
