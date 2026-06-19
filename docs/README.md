# Lorekeep documentation

Lorekeep compiles a team's raw docs into a temporal knowledge graph (`facts.jsonl`) and serves it **read-only** to coding agents over MCP, with per-namespace permission.

- New here? Start with the **[Quickstart](../README.md#quickstart)** in the project README, then the [Compiling](guides/compile.md) + [Serving](guides/serve.md) guides.
- Want the why and how? Read **[Architecture overview](architecture/overview.md)**.

## Architecture

Concepts and design — how the system fits together.

- [**Overview**](architecture/overview.md) — compile-only model, goals, architecture diagram, key decisions, tech stack.
- [Data model](architecture/data-model.md) — `facts.jsonl` format, schema, repository layout, components.
- [Permission model](architecture/permission.md) — namespace visibility rules, deny-by-default, the single `ScopedGraph` chokepoint.
- [Temporal model](architecture/temporal.md) — `valid_from`/`valid_to`, `at_time` / `history` / `changes`.
- [Compile pipeline](architecture/pipeline.md) — ingest → extract → resolve → writer, determinism, incremental compile.
- [Serve & MCP](architecture/serve-mcp.md) — the 8 read-only tools, lazy-reload, agent integration, sync.
- [Testing & evaluation](architecture/evaluation.md) — the three-tier eval strategy and scope.

## Guides

How to use it.

- [Importing agent sessions](guides/import.md) — Claude Code + Cursor → `raw/`.
- [Compiling the knowledge graph](guides/compile.md) — `raw/*.md` → `facts.jsonl`.
- [Serving the graph to coding agents](guides/serve.md) — wire Claude Code / Cursor / Codex over MCP.
- [Data home & path resolution](guides/data-home.md) — env / `LOREKEEP_HOME` / dev mode / XDG.
