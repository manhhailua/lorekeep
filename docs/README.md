# Lorekeep documentation

Lorekeep builds a **living temporal knowledge graph** that coding agents both **read and contribute to** — served over MCP, with per-namespace permission and zero marginal LLM cost for agent contributions.

- New here? Start with the **[Getting started guide](guides/getting-started.md)** (install → compile → serve → backup in 10 minutes), or the terse **[Quickstart](../README.md#quickstart)** in the project README.
- Want the why and how? Read **[Architecture overview](architecture/overview.md)**.

## Architecture

Concepts and design — how the system fits together.

- [**Overview**](architecture/overview.md) — append-and-resolve model, three write paths, architecture diagram, key decisions, tech stack.
- [Data model](architecture/data-model.md) — `facts.jsonl` format, journal format, `pending/` directory, schema, repository layout, components.
- [Pipeline](architecture/pipeline.md) — three write paths (raw/ compile, agent propose, import) → resolve → `facts.jsonl`.
- [Journal](architecture/journal.md) — agent-driven knowledge accumulation: append-only, confidence-gated, zero LLM cost.
- [Agent](architecture/agent.md) — autonomous agent: daemon, trigger model, lint, resolve, suggest, cost profile.
- [Permission model](architecture/permission.md) — namespace visibility rules, deny-by-default, the single `ScopedGraph` chokepoint.
- [Temporal model](architecture/temporal.md) — `valid_from`/`valid_to`, `at_time` / `history` / `changes`.
- [Serve & MCP](architecture/serve-mcp.md) — 9 read + 5 write MCP tools, lazy-reload, journal-based writes, agent integration, sync.
- [Testing & evaluation](architecture/evaluation.md) — the three-tier eval strategy and scope.

## Guides

How to use it.

- [**Getting started**](guides/getting-started.md) — install → compile → serve → backup in 10 minutes. Start here.
- [Importing agent sessions](guides/import.md) — Claude Code + Cursor + Codex + opencode → `raw/`.
- [Compiling the knowledge graph](guides/compile.md) — `raw/*.md` → `facts.jsonl` + resolve pending.
- [Serving the graph to coding agents](guides/serve.md) — wire Claude Code / Cursor / Codex / opencode over MCP, write tools, daemon.
- [Browsing the wiki](guides/wiki.md) — human-readable Obsidian-compatible markdown view of the graph.
- [Data home & path resolution](guides/data-home.md) — env / `LOREKEEP_HOME` / dev mode / XDG.
- [Backing up the data home](guides/backup.md) — `lorekeep backup` to a private git repo: setup, restore, multi-device conflicts.
