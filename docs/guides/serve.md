# Serving the knowledge graph to coding agents

Path resolution (env > `LOREKEEP_HOME` > dev mode > XDG) is covered in [data-home.md](data-home.md).

## Installed use (recommended)

```bash
uvx lorekeep init                     # bootstrap ~/.config/lorekeep + ~/.local/share/lorekeep
# add your docs under ~/.local/share/lorekeep/raw/<ns>/
LOREKEEP_PROVIDER=fake uvx lorekeep compile  # (or set a real provider in config)
uvx lorekeep mcp add --agent claude --ns <ns>
uvx lorekeep doctor
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
uv run lorekeep compile      # reads repo raw/, writes repo graph/
uv run lorekeep serve
```

Force dev mode anywhere: `LOREKEEP_DEV=1 lorekeep ...`.

## Custom knowledge base

```bash
LOREKEEP_HOME=~/kb-work uvx lorekeep init
LOREKEEP_HOME=~/kb-work uvx lorekeep compile
```

## Tools (read-only, scoped)

`search`, `get_node`, `neighbors`, `at_time`, `history`, `changes`,
`list_namespaces`, `schema`. Results are filtered to `LOREKEEP_NS`; cross-namespace
edges are hidden unless both endpoints are visible.

## Connect once (lazy-reload)

The server loads `facts.jsonl` into memory and **lazy-reloads** it: every query
stats the file's mtime, and if it changed (after `lorekeep compile`) the graph is
rebuilt automatically. So the workflow is:

```bash
<edit raw/.../*.md>
uvx lorekeep compile          # rebuilds facts.jsonl
# next query from the agent sees the new graph — NO reconnect needed
```

Connect the MCP server **once**; memory updates via `compile` are visible
immediately. Reconnect is only needed for **code** changes (rare; the serve path
is stable) or **scope** changes (`.mcp.json` `LOREKEEP_NS`).
