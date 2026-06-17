# Serve & MCP

> Adapted from the original design spec.

The serve chain loads `facts.jsonl` once and exposes it read-only to coding agents over MCP. Permission is applied per request. There is **no write path** — compile is a CLI build step, never an MCP tool.

## Server

- **Transport:** stdio (default, for coding agents); streamable HTTP is a phase-2 team-server option.
- **Load:** `facts.jsonl` loaded into an in-memory `GraphStore` (networkx `MultiDiGraph`); optional FTS cache rebuilt lazily.
- **Auth → ns:** reads `LOREKEEP_NS` / config at startup; every tool call is scoped through `ScopedGraph`.
- **Lazy-reload:** every query stats `facts.jsonl`'s mtime; if it changed (after `lorekeep compile`) the graph is rebuilt automatically. Connect the server once — `compile` is visible without reconnecting. Reconnect is only needed for code or scope (`.mcp.json` `LOREKEEP_NS`) changes.

## Tools (read-only, v1)

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

Every tool is auto-scoped by `allowed_ns`. The agent surface is purely read-only, minimizing attack surface. See [permission](permission.md) and [temporal](temporal.md) for the filtering these tools apply.

Tools are plain module functions registered with `@mcp.tool()` but remain directly callable, so tests invoke them without the MCP transport.

## Coding-agent integration

`lorekeep mcp add --agent {claude|cursor|codex} [--scope project|user] [--ns <ns>]` writes the correct config and prints an agent-memory snippet to paste into `CLAUDE.md` / `.cursorrules` / `AGENTS.md`.

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
```

### `lorekeep doctor`

Verifies: `facts.jsonl` loads, schema is valid, ns mapping resolves, and a tool responds. Fast onboarding feedback before the agent hits a real query.

## Sync and multi-device

- **git (primary):** commit `raw/` + `graph/` (`facts.jsonl`, `manifest.json`, `schema.json`). Each device clones/pulls and spawns its local MCP server. No binary store is committed; the FTS cache is gitignored and rebuilt locally.
- **S3 (alternative):** `aws s3 sync` the same paths to an object store; devices sync down.
- **Write conflicts:** compile is an explicit, curator-run CLI. v1 assumes a single compile host per period, or git-PR-based compile (line-based JSONL merges cleanly). Concurrent compiles from two devices are out of scope.
- **Future scale (>50k facts):** partition `facts.jsonl` to Parquet on S3; query via DuckDB/Polars directly on objects.

## Error handling (serve-side)

- **Load:** skip corrupt lines with a warning; do not crash the server.
- **Permission:** deny-by-default; unknown ns ⇒ see only `public`; never leak cross-namespace existence.
- **Cache:** missing FTS cache ⇒ fall back to in-memory scan, rebuild lazily.
- **Integration:** `doctor` surfaces config/load/ns/tool failures before the agent hits them.

## Next

- Usage: [Serving the graph to agents](../guides/serve.md), [Data home & paths](../guides/data-home.md).
