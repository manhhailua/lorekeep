# Serving the knowledge graph to coding agents

Path resolution (env > `LOREKEEP_HOME` > dev mode > XDG) is covered in [data-home.md](data-home.md).

## Installed use (recommended)

```bash
uvx lorekeep init                     # bootstrap + wire agents + compile + start daemon
# add your docs under ~/.local/share/lorekeep/raw/<ns>/
uvx lorekeep compile                  # requires a real provider in config.yaml
uvx lorekeep mcp add --agent claude --ns <ns>
uvx lorekeep doctor

# Keep the graph current with the daemon:
uvx lorekeep agent watch &
```

`mcp add` writes a **portable** `.mcp.json` (no machine path) when
`install_source` is `pypi` (the default from `init`):

```json
{"mcpServers": {"lorekeep": {"command": "uvx",
  "args": ["lorekeep", "serve", "--transport", "stdio"],
  "env": {"LOREKEEP_NS": "<ns>"}}}}
```

## Local dev (repo co-located data)

From the Lorekeep source checkout (has `.lorekeep/` → auto dev mode):

```bash
uv run lorekeep compile      # reads .lorekeep/raw/, writes .lorekeep/graph/
uv run lorekeep serve
```

Force dev mode anywhere: `LOREKEEP_DEV=1 lorekeep ...`.

## Custom knowledge base

```bash
LOREKEEP_HOME=~/kb-work uvx lorekeep init
LOREKEEP_HOME=~/kb-work uvx lorekeep compile
```

## Read tools (9 tools, scoped)

`search`, `get_node`, `neighbors`, `at_time`, `history`, `changes`,
`list_namespaces`, `schema`, `meta`. Results are filtered to `LOREKEEP_NS`; cross-namespace
edges are hidden unless both endpoints are visible.

### `meta(topic="")` — scope awareness

Agents call `meta()` to decide whether to query the graph or work from memory:

```json
{
  "nodes": 42,
  "edges": 18,
  "node_types": {"service": 30, "decision": 12},
  "edge_types": {"depends_on": 15, "decided_by": 3},
  "namespaces": ["backend", "frontend", "public"],
  "provenance": {"curator": 38, "agent": 4},
  "freshness": {
    "oldest": "2024-01-15",
    "newest": "2026-06-20",
    "expired": 2
  },
  "compile": {
    "run_id": "abc123",
    "compiled_at": "2026-06-30T01:27:59Z",
    "merged_count": 4,
    "quarantined_count": 1
  },
  "pending": 7
}
```

Pass `topic` to check coverage for a specific subject:

```json
{
  "coverage": {
    "topic": "payments",
    "matching_nodes": 3,
    "matching_types": {"service": 2, "decision": 1},
    "node_ids": ["svc:payments-api", "dec:adr-007", "svc:payments-worker"]
  }
}
```

**Provenance signal:** `provenance.curator` counts nodes with `src` (compiled from raw docs, higher trust). `provenance.agent` counts nodes without `src` (agent-proposed via journal, lower trust). If most facts are agent-proposed, the agent should verify before relying on them.

**Freshness signal:** `freshness.expired` counts nodes whose `valid_to` has passed. `compile.compiled_at` shows when the graph was last rebuilt. `pending` shows unresolved agent proposals.

## Write tools (5 tools, journal-based)

Agents contribute knowledge during conversation at **zero LLM cost**. Facts
are appended to `pending/` journals and merged into the graph on the next
resolve pass.

| Tool | Purpose | Confidence |
|---|---|---|
| `propose_fact(fact, confidence)` | Propose a new node or edge. `ns` is server-enforced, not callable. | Agent-estimated (0-1) |
| `link_facts(from_id, to_id, type, confidence)` | Create an edge | Typically ≥ 0.8 |
| `flag_contradiction(a, b, description)` | Report conflicting facts | N/A |
| `update_fact(id, props, confidence)` | Update existing fact props | 0.5-0.8 |
| `suggest_improvement(description)` | Suggest gap or improvement | N/A |

**Confidence guidance for agents:**
- ≥ 0.8: explicit claim with source citation. "The codebase shows service X uses database Y."
- 0.5-0.8: implied without explicit source. "Based on the architecture, X likely depends on Y."
- < 0.5: speculation — these are quarantined, not merged.

Facts become visible after the next resolve pass (run `lorekeep resolve`
manually; or automatically when `lorekeep agent watch` detects new pending
entries, polling every 60s).

## Keeping the graph current

Two modes, from fully automatic to agent-controlled:

```bash
# Mode 1: Daemon (default) — fully autonomous, follows Karpathy LLM Wiki pattern
uvx lorekeep agent watch
# Watches raw/ → auto-compile (file count + mtime tracking)
# Watches pending/ → auto-resolve
# Watches Claude memory/ + Codex memories/ → delta quick-import (zero LLM)
# Session re-discovery every cycle — detects new sessions after daemon start
# Survives restart: lorekeep agent daemon install (systemd/launchd/startup)
# Use --no-watch-sessions to disable session watching

# Mode 2: Agent-controlled (--no-watch on init) — no daemon
# The coding agent triggers updates via shell commands:
#   lorekeep compile   # does compile + resolve + wiki (all-in-one, uses LLM)
#   lorekeep resolve   # merge agent-proposed facts only (zero LLM cost)
#   lorekeep wiki      # regenerate wiki from existing graph
# MCP server lazy-reloads facts.jsonl on next query — no daemon needed
```

## Connect once (lazy-reload)

The server loads `facts.jsonl` into memory and **lazy-reloads** it: every query
stats the file's mtime, and if it changed (after compile or resolve) the graph is
rebuilt automatically. So the workflow is:

```bash
<edit raw/.../*.md>
uvx lorekeep compile          # rebuilds facts.jsonl + regenerates wiki/
# OR: agent ingest + resolve  # propose facts interactively, merge journals
# OR: lorekeep agent watch    # daemon does compile + resolve automatically
# next query from the agent sees the new graph — NO reconnect needed
# wiki/ pages also regenerated automatically
```

Connect the MCP server **once**; graph updates via `compile`, `resolve`, or
the daemon are visible immediately. Reconnect is only needed for **code**
changes (rare; the serve path is stable) or **scope** changes (`.mcp.json`
`LOREKEEP_NS`).

## Human view: wiki

The same `facts.jsonl` is also projected to **Obsidian-compatible markdown**
in `wiki/` — one page per node, `[[wikilinks]]` for edges, YAML frontmatter
for Dataview. See [wiki.md](wiki.md).
