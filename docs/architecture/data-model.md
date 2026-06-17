# Data model

> Adapted from the original design spec.

The single source of truth is `facts.jsonl`: one fact per line, sorted, byte-stable. Everything else — `manifest.json`, `schema.json`, the FTS cache — is derived from it.

## Repository layout

```
lorekeep/
├── raw/<ns>/*.md               # source docs (committed) — Karpathy "source code"
├── graph/                      # compiled artifacts (committed) — "executable"
│   ├── facts.jsonl             # THE store: nodes + edges + temporal + ns, 1 fact/line
│   ├── manifest.json           # provenance: raw→fact map, chunk hashes, run id, errors, quarantine
│   └── schema.json             # node/edge type definitions
├── .lorekeep/                  # LOCAL only (gitignored)
│   ├── cache.json              # extraction cache + FTS cache, rebuilt per device
│   └── config.yaml             # namespace defaults, LLM provider config
└── src/lorekeep/
    ├── compile/{ingest,extract,resolve,writer}.py
    ├── store/{graph,fts}.py
    ├── perm/ns.py
    ├── integrations/{claude_code,cursor,codex}.py
    ├── mcp_server.py
    └── cli.py
```

The first directory under `raw/` becomes a fact's `ns` (e.g. `raw/backend/...` → `ns: ["backend"]`). `["public"]` is globally visible.

## `facts.jsonl` format

One JSON object per line. Two kinds: `node` and `edge`. Deterministic key order (sorted) for stable diffs.

```jsonl
{"kind":"node","id":"svc:payments","type":"service","ns":["backend"],"valid_from":"2024-01-15","valid_to":null,"props":{"lang":"go"},"src":["raw/backend/payments.md:12"]}
{"kind":"edge","id":"e_depends_on_0001","type":"depends_on","from":"svc:payments","to":"svc:auth","ns":["backend"],"valid_from":"2024-01-15","valid_to":"2025-03-01","props":{},"src":["raw/backend/payments.md:20"]}
```

- `valid_to: null` ⇒ still current. History = multiple edges with the same endpoints and different validity windows.
- `ns` is a **set**. `["public"]` ⇒ visible to all agents.
- `src` is provenance (path:line) for every fact ⇒ audit, trust, incremental re-compile, and agent citations.

The Python field for an edge's source endpoint is `from_`; `"from"` is its JSON alias — always serialize with `by_alias=True`.

## `schema.json`

Defines allowed node types and edge types with their property schemas. The extractor is constrained to this schema (structured output / JSON-schema response) so the graph is typed and predictable. The schema version is part of the chunk hash, so changing the schema forces re-extraction.

## Components

Each component has one responsibility, a clear input/output interface, and is testable in isolation.

| Component | Input | Output | Responsibility |
|---|---|---|---|
| `compile/ingest` | raw path | `[DocChunk]` | Parse markdown into chunks with source location (path:line). Stateless reader. |
| `compile/extract` | chunk + schema | candidate facts | **The compiler.** LLM-driven, provider-pluggable. Constrained to `schema.json`. Idempotent per chunk via hash cache. |
| `compile/resolve` | candidate facts | clean facts | Entity dedup (alias → canonical id), validate edge endpoints exist, enforce ns-consistency, quarantine malformed facts. |
| `compile/writer` | clean facts | `facts.jsonl` + `manifest.json` | **Deterministic emit**: facts sorted by `(kind, type, id)`, sorted JSON keys, stable formatting ⇒ byte-identical output for unchanged input ⇒ clean git diffs. |
| `store/graph` | `facts.jsonl` | networkx `MultiDiGraph` (temporal) | Load + query API: `get_node`, `neighbors`, `snapshot`, `history`, `changes`. Pure functions; no I/O after load. |
| `perm/ns` | `allowed_ns` (set) | filter / guard | **Single permission chokepoint.** Every store query passes through here. |
| `store/fts` (optional) | `facts.jsonl` | FTS cache | FTS5 over node text/props for text search. Local, gitignored, rebuilt from `facts.jsonl`. Falls back to in-memory scan if absent. |
| `integrations/*` | agent type, scope, ns | config file + snippet | Write Claude Code / Cursor / Codex MCP config; emit agent-memory text. |
| `mcp_server` | store + perm | MCP tool calls | FastMCP server, stdio (default). Loads store once; enforces permission per request. |
| `cli` | — | — | `compile`, `serve`, `eval`, `check`, `mcp add`, `doctor`, `init`, `import`. |

### Dependency order

`ingest → extract → resolve → writer` (compile chain); `store → perm → mcp_server` (serve chain). Compile and serve share only `facts.jsonl` + `schema.json`. The two chains are developed and tested independently.

## Next

- [Permission model](permission.md) — how `ns` becomes visibility.
- [Temporal model](temporal.md) — `valid_from` / `valid_to` semantics.
- [Compile pipeline](pipeline.md) — how `raw/` becomes `facts.jsonl`.
