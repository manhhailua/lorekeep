"""Pipeline: ingest -> extract -> resolve -> writer."""
from __future__ import annotations

from pathlib import Path

from lorekeep.compile.extract import ExtractionCache, extract_chunk
from lorekeep.compile.ingest import ingest
from lorekeep.compile.providers import LLMProvider
from lorekeep.compile.resolve import resolve
from lorekeep.compile.writer import facts_hash, run_id, write_graph
from lorekeep.models import Edge, Manifest, Node, Schema, now_iso


def compile_graph(
    raw_root: Path,
    out_dir: Path,
    schema: Schema,
    provider: LLMProvider,
    cache_path: Path,
    chunk_lines: int = 60,
) -> Manifest:
    chunks = ingest(raw_root, chunk_lines=chunk_lines)
    cache = ExtractionCache(cache_path)

    all_nodes: list[Node] = []
    all_edges: list[Edge] = []
    all_aliases: dict[str, list[str]] = {}
    errors = []
    for chunk in chunks:
        try:
            nodes, edges, aliases = extract_chunk(chunk, schema, provider, cache)
            all_nodes.extend(nodes)
            all_edges.extend(edges)
            for ak, av in aliases.items():       # union variants, last-writer-wins drops aliases
                all_aliases[ak] = list(dict.fromkeys(all_aliases.get(ak, []) + av))
        except Exception as exc:               # skip-and-log; partial compile is valid
            errors.append({"path": chunk.path, "line": chunk.start_line,
                           "message": str(exc)})
    cache.save()

    resolved = resolve(all_nodes, all_edges, name_aliases=all_aliases)

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
    )
    write_graph(out_dir, resolved.nodes, resolved.edges, manifest)
    return manifest
