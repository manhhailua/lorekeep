# Compile pipeline

> Adapted from the original design spec.

The compile chain turns `raw/<ns>/*.md` into a deterministic `facts.jsonl` + `manifest.json`. It runs offline, curator-side, via the CLI — never through MCP. Orchestrated by `src/lorekeep/pipeline.py`.

## Steps

```
raw/<ns>/*.md ──► ingest ──► extract(LLM) ──► resolve ──► writer ──► facts.jsonl + manifest.json
```

1. **`ingest`** reads `raw/`, produces chunks with `path:line` source provenance (`DocChunk`).
2. **`extract`** (LLM, schema-constrained) emits candidate node/edge facts with temporal + ns tags from each chunk.
3. **`resolve`** dedups entities (aliases → canonical id), validates graph integrity (edge endpoints exist), quarantines bad facts.
4. **`writer`** emits deterministic `facts.jsonl` + `manifest.json`.

## Determinism

`writer` sorts facts by `(kind, type, id)`, serializes with sorted keys and fixed separators, one object per line terminated by `\n`. Re-compiling unchanged input yields **byte-identical** output. This is essential for git-based sync and review, and is guarded by a determinism property test.

The atomic-write helper (`os.replace` onto a sibling temp file) guarantees a reader never sees a half-written `facts.jsonl` — important because the MCP server lazy-reloads it on mtime change (see [serve & MCP](serve-mcp.md)).

## Incremental compile

`manifest.json` maps each chunk hash (hash of normalized chunk text + schema version) to the fact ids it produced, and caches extraction output under `.lorekeep/`. Re-compile skips unchanged chunks. Unchanged chunks ⇒ identical contribution ⇒ minimal diffs.

## Error handling

- **LLM failure / unparseable chunk** ⇒ log to `manifest.errors`, skip chunk, continue. Partial compile is valid; re-run fills the gaps.
- **Malformed candidate fact** ⇒ `resolve` quarantines to `manifest.quarantine`, drops from output.
- **Edge with missing endpoint** ⇒ dropped (dangling edges surface in `lorekeep check`).
- **Provider unavailable** ⇒ compile aborts with a clear message; partial results are not merged.

The serve-side error handling (corrupt-line skip, cache fallback) is covered in [serve & MCP](serve-mcp.md).

## Next

- [Data model](data-model.md) — what the writer emits.
- [Serve & MCP](serve-mcp.md) — how the compiled graph is queried.
- Usage: [Compiling the graph](../guides/compile.md).
