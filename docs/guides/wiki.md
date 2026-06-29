# Browsing the wiki (human view)

The compiled graph (`facts.jsonl`) is machine-readable — consumed by agents over MCP. The **wiki** is its human-readable projection: Obsidian-compatible markdown pages with wikilinks, YAML frontmatter, and graph view.

## Quick start

```bash
uvx lorekeep compile      # auto-generates wiki/ after compile
# OR
uvx lorekeep wiki         # regenerate wiki/ from existing facts.jsonl
```

Open the `wiki/` directory in Obsidian:

```bash
# In your data home (default XDG):
open ~/.local/share/lorekeep/wiki

# Or dev mode (repo co-located):
open .lorekeep/wiki
```

In Obsidian: **File → Open vault** → select the `wiki/` directory.

## Lifecycle

The wiki is a **derived artifact** — fully regenerable from `facts.jsonl`, never the reverse:

```
raw/*.md → compile → facts.jsonl → wiki/*.md
                         ↑                ↓
                    agent queries     human browses
                    (MCP tools)      (Obsidian)
```

| Consumer | Reads | Format |
|---|---|---|
| Agent (MCP) | `facts.jsonl` | JSONL — structured queries, temporal, ns-scoped |
| Human (Obsidian) | `wiki/` | Markdown — browsable, searchable, graph view |

Both are always in sync because `compile` auto-generates the wiki after every run.

## Output structure

```
wiki/
├── index.md                     # catalog of all entities, grouped by type
├── log.md                       # append-only generation log
├── overview.md                  # graph stats dashboard
└── entities/
    └── <type>/
        └── <slug>.md            # one page per node
```

### Entity pages

Each node becomes a markdown page with:

- **YAML frontmatter** — `id`, `type`, `ns`, `valid_from`, `valid_to`, `sources`, `tags` (Obsidian Dataview compatible)
- **Properties table** — all node `props` as key-value rows
- **Relationships** — outgoing and incoming edges as `[[wikilinks]]`, grouped by edge type
- **Timeline** — `valid_from` / `valid_to` dates

Example entity page (`entities/service/svc-payments-api.md`):

```markdown
---
id: svc:payments-api
type: service
ns: [backend]
valid_from: 2024-01-15
valid_to: null
sources:
  - raw/backend/payments.md:3
tags: [service, backend, entity]
---

# payments-api

> ID: `svc:payments-api`

## Properties

| Key | Value |
|---|---|
| name | payments-api |
| lang | go |

## Relationships

### depends_on →

- [[svc-auth]] (2024-01-15 → 2025-03-01)

## Timeline

- **2024-01-15**: Valid from
```

### index.md

Catalog of all entities, grouped by node type (`## Services`, `## Teams`, `## Decisions`). Each entry is a `[[wikilink]]` with a one-line summary.

### overview.md

Graph-level dashboard: node/edge counts by type, temporal range, namespace breakdown, and compile metadata (run ID, facts hash).

### log.md

Append-only log of wiki generation events:

```
## [2026-06-29T21:53:00Z] wiki | run_id=abc123, 4 nodes, 2 edges
```

## Obsidian tips

- **Graph view** — all `[[wikilinks]]` render as a force-directed graph. This is the entity-relationship graph visualized.
- **Backlinks** — Obsidian automatically shows inbound references at the bottom of each page (equivalent to incoming edges).
- **Dataview** — the YAML frontmatter enables structured queries. Example: list all services written in Go:
  ```dataview
  LIST FROM #service WHERE lang = "go"
  ```
- **Tags** — each page is tagged with `[<type>, <ns>, "entity"]`, enabling filtered views.

## CLI

```bash
lorekeep wiki          # regenerate wiki/ from facts.jsonl
```

Wiki generation also runs automatically after `compile` and after daemon-triggered resolve passes — you never need to run it manually unless you want to force a refresh.

## Determinism

Re-generating the wiki from unchanged `facts.jsonl` yields **byte-identical** pages (entity pages, index, overview). The only exception is `log.md`, which is append-only by design.

## Path resolution

The wiki lives at `<data-home>/wiki/` (same level as `raw/` and `graph/`). Override with:

```bash
LOREKEEP_WIKI=/path/to/wiki uvx lorekeep wiki
```

See [data-home.md](data-home.md) for the full 4-tier path resolution.

## Next

- [Compile guide](compile.md) — how `facts.jsonl` is produced.
- [Serve guide](serve.md) — how agents consume the same graph over MCP.
