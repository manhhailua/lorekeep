"""Pipeline: ingest -> extract -> resolve -> writer."""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace
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

# Exception names that indicate a systemic provider failure (bad API key,
# unreachable endpoint, etc.). These will fail identically on every chunk, so
# we short-circuit the compile loop after the first occurrence.
_FATAL_PROVIDER_ERRORS = frozenset({
    "AuthenticationError",
    "PermissionDeniedError",
    "NotFoundError",
    "ConnectionError",
    "Timeout",
    "APIConnectionError",
    "APITimeoutError",
    "ServiceUnavailableError",
    "InternalServerError",
    "RateLimitError",
})


def _is_fatal_provider_error(exc: Exception) -> bool:
    """True if *exc* is a systemic provider error that will recur on every chunk."""
    exc_name = type(exc).__name__
    if exc_name in _FATAL_PROVIDER_ERRORS:
        return True
    # Walk the cause chain (litellm wraps errors inside retries).
    cause = exc.__cause__ or exc.__context__
    while cause is not None and cause is not exc:
        if type(cause).__name__ in _FATAL_PROVIDER_ERRORS:
            return True
        cause = getattr(cause, "__cause__", None) or getattr(cause, "__context__", None)
    return False


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
    language: str = "en",
    prev_aliases: dict[str, str] | None = None,
    max_workers: int = 4,
    flush_interval: int = 10,
    check_image_links: bool = True,
    image_check_timeout: float = 10.0,
    image_check_workers: int = 8,
) -> Manifest:
    chunks = ingest(raw_root, chunk_lines=chunk_lines)
    cache = ExtractionCache(cache_path)
    total = len(chunks)
    log.info(
        "compile started chunk_count=%s max_workers=%s flush_interval=%s",
        total, max_workers, flush_interval,
        extra={"event": "compile.start"},
    )

    all_nodes: list[Node] = []
    all_edges: list[Edge] = []
    all_aliases: dict[str, list[str]] = {}
    errors: list[dict] = []
    completed = 0
    aborted = False

    def _accumulate(result: tuple[list[Node], list[Edge], dict[str, list[str]]]) -> None:
        """Thread-safe accumulation (called from main thread via as_completed)."""
        nodes, edges, aliases = result
        all_nodes.extend(nodes)
        all_edges.extend(edges)
        for ak, av in aliases.items():
            all_aliases[ak] = list(dict.fromkeys(all_aliases.get(ak, []) + av))

    def _maybe_flush(completed_count: int) -> None:
        """Stream intermediate resolve + write so serve sees live graph updates.

        Each flush resolves the full accumulated set (not just new chunks) so
        union-find entity resolution and prev_aliases dedup are applied across
        all chunks extracted so far. The final resolve+write overwrites this
        with deterministic edge IDs.
        """
        if flush_interval <= 0 or completed_count >= total:
            return
        if completed_count % flush_interval != 0:
            return
        partial = resolve(
            list(all_nodes), list(all_edges),
            name_aliases={k: list(v) for k, v in all_aliases.items()},
            aliases_map=prev_aliases, schema=schema,
        )
        provisional = Manifest(
            schema_version=schema.version, chunk_count=total,
            node_count=len(partial.nodes), edge_count=len(partial.edges),
            run_id="streaming", facts_hash="",
        )
        write_graph(out_dir, partial.nodes, partial.edges, provisional)
        log.info(
            "compile flush completed=%s nodes=%s edges=%s",
            completed_count, len(partial.nodes), len(partial.edges),
            extra={"event": "compile.flush"},
        )

    import concurrent.futures

    if max_workers <= 1:
        # Sequential extraction — preserves exact short-circuit behavior.
        for i, chunk in enumerate(chunks):
            if aborted:
                break
            if on_progress is not None:
                on_progress(i, total, chunk)
            try:
                result = extract_chunk(
                    chunk, schema, provider, cache,
                    personal_ns=personal_ns, language=language,
                )
                _accumulate(result)
            except Exception as exc:
                log.exception(
                    "compile: chunk failed line=%s error_type=%s",
                    chunk.start_line, type(exc).__name__,
                    extra={"event": "compile.chunk_failed"},
                )
                errors.append({"path": chunk.path, "line": chunk.start_line,
                               "message": str(exc)})
                if _is_fatal_provider_error(exc):
                    log.error(
                        "compile aborted: fatal provider error, skipping %s remaining chunk(s)",
                        total - i - 1,
                        extra={"event": "compile.aborted_fatal"},
                    )
                    aborted = True
            completed += 1
            _maybe_flush(completed)
    else:
        # Parallel extraction — submit all, collect via as_completed.
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_chunk = {
                executor.submit(
                    extract_chunk, chunk, schema, provider, cache,
                    personal_ns=personal_ns, language=language,
                ): chunk
                for chunk in chunks
            }

            for future in concurrent.futures.as_completed(future_to_chunk):
                if aborted:
                    future.cancel()
                    continue
                chunk = future_to_chunk[future]
                if on_progress is not None:
                    on_progress(completed, total, chunk)
                try:
                    result = future.result()
                    _accumulate(result)
                except Exception as exc:
                    log.exception(
                        "compile: chunk failed line=%s error_type=%s",
                        chunk.start_line, type(exc).__name__,
                        extra={"event": "compile.chunk_failed"},
                    )
                    errors.append({"path": chunk.path, "line": chunk.start_line,
                                   "message": str(exc)})
                    if _is_fatal_provider_error(exc):
                        log.error(
                            "compile aborted: fatal provider error, skipping remaining chunk(s)",
                            extra={"event": "compile.aborted_fatal"},
                        )
                        aborted = True
                completed += 1
                _maybe_flush(completed)

    cache.save()

    resolved = resolve(
        all_nodes, all_edges, name_aliases=all_aliases,
        aliases_map=prev_aliases, schema=schema,
    )

    # The schema asks for links that really open; only a fetch can confirm it.
    # Runs after resolve so each URL is probed once, not once per chunk.
    nodes = resolved.nodes
    if check_image_links:
        from lorekeep.compile.imagecheck import verify_nodes

        nodes, _img = verify_nodes(
            nodes,
            cache_path.parent / "image-links.json",
            timeout=image_check_timeout,
            max_workers=image_check_workers,
        )
        resolved = replace(resolved, nodes=nodes)

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
    log.info(
        "compile completed chunk_count=%s node_count=%s edge_count=%s error_count=%s",
        len(chunks), len(resolved.nodes), len(resolved.edges), len(errors),
        extra={"event": "compile.complete"},
    )
    return manifest
