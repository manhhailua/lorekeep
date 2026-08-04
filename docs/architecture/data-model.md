# Data model

> Adapted from the original design spec.

The durable sources of truth are raw docs, `schema.json`, and agent journals.
`facts.jsonl` is a sorted, byte-stable derived store rebuilt by compile +
journal replay. `manifest.json`, the wiki, and the FTS cache are also derived.

## Repository layout

```
lorekeep/
├── raw/<ns>/*.md               # source docs (committed) — Karpathy "source code"
├── graph/                      # compiled artifacts (committed)
│   ├── facts.jsonl             # THE store: nodes + edges + temporal + ns, 1 fact/line
│   ├── manifest.json           # provenance: raw→fact map, chunk hashes, run id, errors, quarantine
│   └── schema.json             # node/edge type definitions
├── pending/                    # agent-proposed facts (journals)
│   ├── <ns>/journal.jsonl      # per-namespace append-only journal
│   └── <agent>/journal.jsonl   # per-agent append-only journal
├── .lorekeep/                  # LOCAL only (gitignored)
│   ├── cache.json              # extraction cache + FTS cache, rebuilt per device
│   └── config.yaml             # namespace defaults, LLM provider config
└── src/lorekeep/
    ├── compile/{ingest,extract,resolve,writer}.py
    ├── store/{graph,fts}.py
    ├── perm/ns.py
    ├── journal.py              # journal append + load
    ├── agent.py                # autonomous agent CLI
    ├── integrations/{claude_code,cursor,codex}.py
    ├── mcp_server.py
    └── cli.py
```

The first directory under `raw/` becomes a fact's `ns` (e.g. `raw/backend/...` → `ns: ["backend"]`). `["public"]` is globally visible. Journal entries inherit `ns` from the proposing agent's scope.

## `facts.jsonl` format

One JSON object per line. Two kinds: `node` and `edge`. Deterministic key order (sorted) for stable diffs.

```jsonl
{"kind":"node","id":"svc:payments","type":"service","ns":["backend"],"valid_from":"2024-01-15","valid_to":null,"props":{"description":"Handles payment authorization and capture.","lang":"go","name":"payments","summary":"Core service for customer payment requests."},"src":["raw/backend/payments.md:12"]}
{"kind":"edge","id":"e_depends_on_0001","type":"depends_on","from":"svc:payments","to":"svc:auth","ns":["backend"],"valid_from":"2024-01-15","valid_to":"2025-03-01","props":{"description":"Uses auth to validate the caller before capture."},"src":["raw/backend/payments.md:20"]}
```

- `valid_to: null` ⇒ still current. History = multiple edges with the same endpoints and different validity windows.
- `ns` is a **set**. `["public"]` ⇒ visible to all agents.
- `src` is provenance (path:line) for every fact ⇒ audit, trust, incremental re-compile, and agent citations.
- `props.summary` is concise catalog prose; `props.description` carries grounded
  detail. Edge `props.description` explains why or how a relationship exists.
  These remain optional at the storage-model level so historical/custom graphs
  are readable, while stock schema v4 asks extraction to populate them.

The Python field for an edge's source endpoint is `from_`; `"from"` is its JSON alias — always serialize with `by_alias=True`.

## `pending/` journal format

Agent-proposed facts are written to append-only JSONL journals before being merged into `facts.jsonl`. Each line is a journal entry:

```jsonl
{"fact":{"kind":"node","id":"svc:checkout","type":"service","ns":["backend"],"valid_from":null,"valid_to":null,"props":{"name":"checkout","lang":"rust"},"src":["agent:claude:session-abc123"]},"agent":"claude","ns":"backend","confidence":0.85,"proposed_at":"2026-06-20T10:30:00Z","status":"pending"}
{"fact":{"kind":"edge","id":"","type":"depends_on","from":"svc:checkout","to":"svc:payments","ns":["backend"],"valid_from":null,"valid_to":null,"props":{},"src":["agent:claude:session-abc123"]},"agent":"claude","ns":"backend","confidence":0.7,"proposed_at":"2026-06-20T10:31:00Z","status":"pending"}
```

### Journal entry fields

| Field | Type | Description |
|---|---|---|
| `fact` | `Node \| Edge` | The proposed fact (edge `id` may be empty, assigned at resolve) |
| `agent` | `str` | Agent identifier (`claude`, `codex`, `cursor`) |
| `ns` | `str` | Namespace from agent's scope |
| `confidence` | `float` (0-1) | Agent's self-estimated confidence |
| `proposed_at` | `ISO datetime` | When the fact was proposed |
| `status` | `"pending" \| "merged" \| "quarantined"` | Current lifecycle state |

### Confidence levels

| Level | Range | Meaning | Resolve behavior |
|---|---|---|---|
| **High** | ≥ 0.8 | Explicit claim with source citation | Auto-merge |
| **Medium** | 0.5 to <0.8 | Mentioned without explicit source | Merge, flag for review |
| **Low** | < 0.5 | Speculation / hedging language | Quarantine, do not merge |

### Resolve priority (when facts conflict by id)

1. **raw/-extracted facts** — curated, provenance-rich, highest trust
2. **import facts** — LLM-summarized from agent sessions
3. **agent-proposed facts (high confidence)** — explicit claims
4. **agent-proposed facts (medium confidence)** — merged but flagged

## `schema.json`

Defines allowed node types and edge types with their property schemas. Schema v4
adds optional `common_node_props` / `common_edge_props`, human type labels and
plurals, a node `display_prop`, and forward/inverse edge labels. The extractor is
constrained to this schema so the graph is typed and predictable; the wiki uses
the labels without another LLM call. The full schema and prompt are part of the
extraction-cache fingerprint, so changing either forces re-extraction.

## Components

Each component has one responsibility, a clear input/output interface, and is testable in isolation.

| Component | Input | Output | Responsibility |
|---|---|---|---|
| `compile/ingest` | raw path | `[DocChunk]` | Parse markdown into chunks with source location (path:line). Stateless reader. |
| `compile/extract` | chunk + schema | candidate facts | **The compiler.** LLM-driven, provider-pluggable. Constrained to `schema.json`. Idempotent per chunk via hash cache. |
| `compile/resolve` | candidate facts + journals | clean facts | Entity dedup (alias → canonical id), validate edge endpoints exist, enforce ns-consistency, quarantine malformed facts. Merges from three sources (raw/ > import > agent-propose). |
| `compile/writer` | clean facts | `facts.jsonl` + `manifest.json` | **Deterministic emit**: facts sorted by `(kind, type, id)`, sorted JSON keys, stable formatting ⇒ byte-identical output for unchanged input ⇒ clean git diffs. |
| `journal` | fact + agent + confidence | append to `pending/` journal | Append-only JSONL writer. Agent-proposed facts land here before resolve. |
| `agent` | — | — | Daemon: watch raw/ → auto-compile, periodic resolve, nightly lint, auto-import, suggest. CLI: `agent lint`, `agent ingest`, `agent evolve`. |
| `store/graph` | `facts.jsonl` | networkx `MultiDiGraph` (temporal) | Load + query API: `get_node`, `neighbors`, `snapshot`, `history`, `changes`. Pure functions; no I/O after load. |
| `perm/ns` | `allowed_ns` (set) | filter / guard | **Single permission chokepoint.** Every store query passes through here. |
| `store/fts` (optional) | `facts.jsonl` | FTS cache | FTS5 over node text/props for text search. Local, gitignored, rebuilt from `facts.jsonl`. Falls back to in-memory scan if absent. |
| `integrations/*` | agent type, scope, ns | config file + snippet | Write Claude Code / Cursor / Codex MCP config; emit agent-memory text. |
| `mcp_server` | store + perm | 9 read + 5 write MCP tools | FastMCP server, stdio (default). Loads store once; enforces permission per request. Write tools append to journals. |

### Dependency order

**Write paths:** `ingest → extract → resolve` (compile chain); `agent propose → journal` (agent path); `import → raw/ → compile` (import chain). All converge at `resolve → writer → facts.jsonl`.

**Serve chain:** `store → perm → mcp_server` (read queries + write proposals).

**Daemon:** `agent watch` triggers compile/resolve/lint/import on schedule or events.

## Next

- [Pipeline](pipeline.md) — three write paths → resolve → facts.jsonl.
- [Journal](journal.md) — agent-driven knowledge accumulation.
- [Agent](agent.md) — autonomous agent operations.
- [Permission model](permission.md) — how `ns` becomes visibility.
- [Temporal model](temporal.md) — `valid_from` / `valid_to` semantics.
- [Serve & MCP](serve-mcp.md) — read + write query layer.
