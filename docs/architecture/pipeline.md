# Pipeline: three write paths → one resolve

> Adapted from the original design spec.

Knowledge enters the graph through three paths, all converging at a single resolve step that outputs `facts.jsonl`. The resolve runs periodically (or on-demand), keeping the graph continuously up-to-date without per-write LLM cost.

```
                    THREE WRITE PATHS
                    ═══════════════════

PATH 1 — raw/ compile (curator, LLM-powered)
  raw/<ns>/*.md ──► ingest ──► extract(LLM) ──┐
                                               │
PATH 2 — agent propose (runtime, ZERO LLM)     │
  coding agent ──► propose_fact() ──► ─────────┤
                  link_facts()                 │
                  flag_contradiction()         │
                                               │
PATH 3 — import (curator, LLM-summarize)       │
  agent sessions ──► import ──► raw/ ──► ──────┘
                                               │
                    ┌──────────────────────────┘
                    ▼
            ┌──────────────────┐
            │     RESOLVE      │   pure Python, zero LLM calls
            │  (periodic)      │
            └──────┬───────────┘
                   ▼
            facts.jsonl + manifest.json
```

## Path 1: raw/ compile

The original compile pipeline. Runs offline, curator-side. Extraction is LLM-powered; resolution merges with other sources in the global resolve step below.

```
raw/<ns>/*.md ──► ingest ──► extract(LLM) ──► candidate facts ──► (to global resolve)
```

1. **`ingest`** reads `raw/`, produces chunks with `path:line` source provenance (`DocChunk`).
2. **`extract`** (LLM, schema-constrained) emits candidate node/edge facts with temporal + ns tags from each chunk. Candidates feed into the global resolve step alongside journal facts and import facts.

`raw/` is populated by hand or by `lorekeep import`, which converts a coding agent's sessions (Claude Code, Cursor) into markdown; see the [import guide](../guides/import.md).

## Path 2: agent propose (runtime, zero LLM cost)

Coding agents propose facts during conversation through MCP write tools. Each proposal is appended to `pending/<ns>/journal.jsonl` as a journal entry.

```python
# Agent-side (Claude Code): agent discovers checkout service during conversation
# It calls the MCP tool (ns is server-enforced, not caller-provided):
propose_fact({
    "fact": {
        "kind": "node",
        "id": "svc:checkout",
        "type": "service",
        "props": {"lang": "rust"}
    },
    "confidence": 0.85
})
# Server derives ns from LOREKEEP_NS, strips fact.ns if present
# → appended to pending/backend/journal.jsonl
# → ZERO additional LLM cost (agent already ran LLM for the conversation)
```

Write tools available: `propose_fact`, `link_facts`, `flag_contradiction`, `update_fact`, `suggest_improvement`. See [serve & MCP](serve-mcp.md) for details.

## Path 3: import sessions

Converting agent conversation history into raw/ markdown, which then feeds into Path 1 compile. See the [import guide](../guides/import.md).

## Resolve: merging all sources

Resolve loads `facts.jsonl` plus all pending journals, merges facts, and writes a new `facts.jsonl`. **Zero LLM cost** — pure Python logic.

### Trigger

| Trigger | Behavior |
|---|---|
| **Batch size** | After N pending journal entries accumulate (default 50) |
| **Time interval** | Every T minutes if pending entries exist (default 5) |
| **Manual** | `lorekeep resolve` (curator runs explicitly) |
| **Post-compile** | After a compile run, resolve immediately to integrate pending |
| **Session end** | Agent session ends → resolve to persist discoveries |

### Merge logic

```
1. Load current facts.jsonl (if exists)
2. Load all pending journals (pending/**/journal.jsonl — both ns-scoped and agent-scoped)
3. Merge by source priority:
   - raw/-extracted facts: idempotent (cache hit = skip)
   - import facts: from raw/ compile
   - agent-proposed facts: filtered by confidence, with additional gates:
     - High (≥0.8): merge automatically (see security gates below)
     - Medium (0.5 to <0.8): merge, append to manifest.review
     - Low (<0.5): quarantine, do not merge
   - Security gates on auto-merge (even for high confidence):
     - Cross-namespace edges require curator review
     - New entity types not in schema require confidence ≥ 0.9 + review
     - Contradictions between agent-proposed facts → both quarantined
4. Dedup by id: same id → merge props, src, ns (union). ns is always
   verified against the journal's namespace (server-enforced, not caller-provided).
5. Alias resolution: collapse variants to canonical entities
6. Validate: schema, ns, edge endpoints
7. Sort + write facts.jsonl (atomic os.replace)
8. Mark processed journal entries as "merged" or "quarantined"
9. Update manifest with new run_id, facts_hash, chunk_hashes
```

### Priority rules (same id conflict)

| Source | Priority | Rationale |
|---|---|---|
| raw/ extracted | Highest | Curator-curated, provenanced to `path:line` |
| import (path 3) | Medium | LLM-summarized from sessions, human-reviewed |
| agent-proposed (high confidence) | Medium-High | Agent explicit claim, verified by confidence |
| agent-proposed (medium confidence) | Low-Medium | Merged but flagged for review |

Lower-priority facts never overwrite higher-priority facts for the same id. Conflicting props are merged (union), not replaced.

## Determinism

Re-compiling unchanged input (same raw/ + same journals with same statuses) yields **byte-identical** `facts.jsonl`. The writer sorts facts by `(kind, type, id)`, serializes with sorted keys and fixed separators, one object per line terminated by `\n`. The atomic-write helper (`os.replace` onto a sibling temp file) guarantees a reader never sees a half-written file — important because the MCP server lazy-reloads on mtime change.

## Incremental compile

`manifest.json` maps each chunk hash (hash of normalized chunk text + schema version) to the fact ids it produced, and caches extraction output under `.lorekeep/`. Re-compile skips unchanged chunks. Unchanged chunks ⇒ identical contribution ⇒ minimal diffs.

## Error handling

- **LLM failure / unparseable chunk** ⇒ log to `manifest.errors`, skip chunk, continue. Partial compile is valid; re-run fills the gaps.
- **Malformed candidate fact** ⇒ `resolve` quarantines to `manifest.quarantine`, drops from output.
- **Edge with missing endpoint** ⇒ deferred to pending retry, not permanently dropped. Edges whose endpoints don't yet exist (e.g., node proposed in a different journal entry not yet resolved) are held in a retry queue and re-evaluated on subsequent resolve passes up to 5 times (or until the endpoint appears). After max retries, they are quarantined. Dangling edges also surface in `lorekeep check`.
- **Provider unavailable** ⇒ compile aborts with a clear message; partial results are not merged.
- **Low-confidence agent proposal** ⇒ quarantined, never enters `facts.jsonl`; listed in resolve report.
- **Journal parse error** ⇒ skip corrupted line, log warning, continue resolve.

The serve-side error handling (corrupt-line skip, cache fallback) is covered in [serve & MCP](serve-mcp.md).

## Next

- [Data model](data-model.md) — what the writer emits, journal format.
- [Journal](journal.md) — agent-driven knowledge accumulation in detail.
- [Serve & MCP](serve-mcp.md) — how the compiled graph is queried and written.
- Usage: [Compiling the graph](../guides/compile.md).
