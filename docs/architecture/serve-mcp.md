# Serve & MCP

> Adapted from the original design spec.

The serve chain loads `facts.jsonl` once and exposes it to coding agents over MCP — both **read** queries and **write** proposals. Read queries go directly to the graph; write proposals append to pending journals and are merged into the graph by a periodic resolve pass.

## Server

- **Transport:** stdio (default, for coding agents); streamable HTTP is a phase-2 team-server option.
- **Load:** `facts.jsonl` loaded into an in-memory `GraphStore` (networkx `MultiDiGraph`); optional FTS cache rebuilt lazily.
- **Auth → ns:** reads `LOREKEEP_NS` / config at startup; every tool call is scoped through `ScopedGraph`.
- **Lazy-reload:** every query stats `facts.jsonl`'s mtime; if it changed (after compile or resolve) the graph is rebuilt automatically. Connect the server once — graph updates are visible without reconnecting. Reconnect is only needed for code or scope (`.mcp.json` `LOREKEEP_NS`) changes.
- **Journals:** write tools append to `pending/<ns>/journal.jsonl`; facts enter the graph on the next resolve pass, not immediately. This avoids write conflicts and keeps the read path fast.

## Read tools (9 tools, scoped)

| Tool | Purpose |
|---|---|
| `search(query, limit)` | Text search (FTS if cached, else scan) within ns scope. |
| `get_node(id)` | Node + props + provenance `src`. |
| `neighbors(id, edge_type?, depth?)` | Traverse (both directions, depth ≤ 5), ns-scoped. |
| `at_time(time)` | Snapshot of facts valid at `time`. |
| `history(id)` | Temporal versions of an entity + touching edges. |
| `changes(from_t, to_t)` | Edges whose window began/ended in the range. |
| `list_namespaces()` | Namespaces visible to this caller. |
| `schema()` | Available node/edge types. |

Every read tool is auto-scoped by `allowed_ns`. See [permission](permission.md) and [temporal](temporal.md) for the filtering these tools apply.

## Write tools (5 tools, journal-based)

Write tools **do not mutate** `facts.jsonl` directly. They append to `pending/<ns>/journal.jsonl`. Facts become visible after the next resolve pass (see [pipeline](pipeline.md)).

| Tool | Purpose | Confidence |
|---|---|---|
| `propose_fact(fact, confidence)` | Propose a new node or edge. `ns` is server-enforced from `LOREKEEP_NS`, caller-provided `fact.ns` is stripped. | Agent-estimated (0-1) |
| `link_facts(from_id, to_id, type, confidence)` | Create an edge between two existing nodes | Typically high (≥0.8) |
| `flag_contradiction(fact_a_id, fact_b_id, description)` | Report conflicting facts for curator review | N/A (review, not merge) |
| `update_fact(id, props, confidence)` | Propose updated props for an existing fact | Typically medium-high |
| `suggest_improvement(description)` | Suggest a non-fact improvement (gap, missing entity) | N/A (review only) |

All write tools derive `ns` from the server's verified `LOREKEEP_NS` scope, never from caller input. The `fact.ns` field inside proposed facts is stripped and replaced with the server-verified namespace at journal-append time. This prevents an agent scoped to `backend` from injecting facts into `frontend`.

### Confidence guidance for agents

Agents should self-estimate confidence when proposing facts:

- **≥ 0.8**: Explicit claim with source citation from conversation context. "The codebase shows service X uses database Y."
- **0.5 to <0.8**: Mentioned or implied without explicit source. "Based on the architecture discussion, service X likely depends on Y."
- **< 0.5**: Speculation or hedging. "It might be the case that..." — these are quarantined by resolve.

### Write tool flow

```
Agent discovers knowledge during conversation
  │
  ▼
Agent calls MCP write tool (e.g. propose_fact)
  │
  ▼
Server validates: ns derived from LOREKEEP_NS (caller cannot override), fact.ns stripped and replaced, fact matches schema
  │  (validation errors returned immediately, not written)
  ▼
Server appends to pending/<LOREKEEP_NS>/journal.jsonl
  │  (atomic append: write line + flush)
  ▼
Returns to agent: {"accepted": true, "id": "<fact_id>", "status": "pending"}
  │
  ▼ ... later (resolve pass) ...
  │
Resolve loads journal, merges fact into facts.jsonl
  │  (or quarantines if low confidence / invalid)
  ▼
MCP server lazy-reloads on next query → fact is now searchable
```

### Why journal-based writes?

1. **Zero LLM cost**: The coding agent already ran the LLM for the conversation. Proposing a fact is just formatting existing output.
2. **No write conflicts**: Append-only journals are trivially concurrent — multiple agents write to different files (partitioned by ns or agent id).
3. **Gate before merge**: Low-confidence or malformed facts never enter `facts.jsonl`. Resolve validates, deduplicates, and prioritizes before merging.
4. **Audit trail**: Journal entries carry `agent`, `proposed_at`, `confidence` — full provenance for every fact that enters the graph.
5. **Read path unaffected**: Facts are only visible after resolve. The read path (GraphStore + ScopedGraph) serves only validated facts from `facts.jsonl`.

## Coding-agent integration

`lorekeep mcp add --agent {claude|cursor|codex|opencode} [--scope project|user] [--ns <ns>]` writes the correct config and prints an agent-memory snippet to paste into `CLAUDE.md` / `.cursorrules` / `AGENTS.md`.

> **Install source.** The snippets use `uvx lorekeep`, which assumes the package is on PyPI. `mcp add` detects `install_source` from `.lorekeep/config.yaml` so the emitted config matches the deployment (PyPI `uvx`, `git+https`, or a local `uv tool install .`).

### Claude Code — `.mcp.json` (project scope)

```json
{
  "mcpServers": {
    "lorekeep": {
      "command": "uvx",
      "args": ["lorekeep", "serve", "--transport", "stdio"],
      "env": { "LOREKEEP_NS": "backend" }
    }
  }
}
```

### Agent-memory snippet (printed by `mcp add`)

```markdown
## Lorekeep knowledge base (MCP)
Before answering architecture/code/domain questions, query Lorekeep:
search(q) → get_node(id) → neighbors / at_time / history as needed.
Always cite `src` provenance. Knowledge is namespace-scoped — if a fact is
missing, it may be outside your scope, not nonexistent.

When you discover new knowledge during conversation (services, dependencies,
decisions), call propose_fact or link_facts to contribute it back. Estimate
confidence: ≥0.8 for explicit claims with source, 0.5 to <0.8 for implications.
Facts enter the graph on the next resolve pass.
```

### `lorekeep doctor`

Verifies: `facts.jsonl` loads, schema is valid, ns mapping resolves, journal directory is writable, and a tool responds. Fast onboarding feedback before the agent hits a real query.

## Sync and multi-device

- **git (primary):** commit `raw/` + `graph/` + `pending/` (`facts.jsonl`, `manifest.json`, `schema.json`, journals). Each device clones/pulls and spawns its local MCP server. No binary store is committed; the FTS cache is gitignored and rebuilt locally.
- **S3 (alternative):** `aws s3 sync` the same paths to an object store; devices sync down.
- **Write conflicts:** journals are append-only, per-namespace or per-agent. No two agents write to the same journal line. Resolve serializes all journals into a single deterministic `facts.jsonl`. Concurrent journal appends from different devices merge cleanly via git.
- **Future scale (>50k facts):** partition `facts.jsonl` to Parquet on S3; query via DuckDB/Polars directly on objects.

## Error handling (serve-side)

- **Load:** skip corrupt lines with a warning; do not crash the server.
- **Permission:** deny-by-default; unknown ns ⇒ see only `public`; never leak cross-namespace existence.
- **Cache:** missing FTS cache ⇒ fall back to in-memory scan, rebuild lazily.
- **Write:** validate fact schema and derive ns from `LOREKEEP_NS` (never trust caller-provided `ns`). Return clear error for invalid proposals (don't write bad data). Strip `fact.ns` and overwrite with server-verified namespace.
- **Journal:** atomic append (write line + flush); corrupted journal lines skipped on load.
- **Integration:** `doctor` surfaces config/load/ns/tool failures before the agent hits them.

## Next

- Usage: [Serving the graph to agents](../guides/serve.md), [Data home & paths](../guides/data-home.md).
- Design: [Pipeline](pipeline.md), [Journal](journal.md), [Agent](agent.md).
