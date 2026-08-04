# Browsing the wiki (Obsidian + Tolaria)

The compiled graph (`facts.jsonl`) is machine-readable — consumed by agents over
MCP. The **wiki** is its human-readable projection: a flat folder of markdown
pages with `[[wikilinks]]`, YAML frontmatter (including relationship fields),
and tags. **The same `wiki/` folder opens in both Obsidian and Tolaria** — no
separate build per app.

## 1. Quick start

One command generates the wiki and opens it in Obsidian:

```bash
uvx lorekeep wiki --open
```

(`compile` also auto-generates `wiki/` at the end of every run, so after
editing raw docs you can jump straight to `wiki --open`.)

**Prerequisites:** [Obsidian](https://obsidian.md) installed. If it isn't (or
the launcher can't find it), `--open` prints the folder path so you can open it
manually — the wiki is already generated.

## 2. Open the vault manually

In Obsidian: **Open vault → Open folder as vault** → select the `wiki/`
directory. The same folder also opens in [Tolaria](https://tolaria.md)
(see [§9](#9-tolaria)).

| Install | Vault path |
|---|---|
| Source checkout (dev) | `.lorekeep/wiki/` |
| Installed (Linux XDG) | `~/.local/share/lorekeep/wiki/` |
| Installed (macOS) | `~/Library/Application Support/lorekeep/wiki/` |
| Installed (Windows) | `%LOCALAPPDATA%\lorekeep\wiki\` |

> ⚠️ **Open `wiki/`, not its parent.** The parent `.lorekeep/` holds
> `config.yaml` (which may contain your API key). Scoping the vault to `wiki/`
> keeps that file — and any community-plugin access to it — out of Obsidian.

## 3. What's in the vault

Lorekeep uses a flat, portable layout—one `.md` per node at the root. Both apps
can scan it directly, and stable filename stems keep relation fields portable:

```
wiki/
├── index.md              # landing dashboard: goals/projects, decisions, people, hubs
├── catalog.md            # every entity, grouped by human type label
├── overview.md           # graph + content-quality + compile diagnostics
├── log.md                # append-only generation log
├── svc-payments-api.md   # one page per node, <slug>.md
├── svc-auth.md
└── …
```

Each entity page has YAML frontmatter that Obsidian/Dataview/Tolaria can query.
Out-edges are emitted as **relationship fields** (any frontmatter field holding
`[[wikilinks]]` — Tolaria treats these as relationships; Obsidian/Dataview as
queryable lists):

```yaml
---
kind: "node"
id: "svc:payments-api"
type: "service"
ns: ["backend"]
valid_from: "2024-01-15"
valid_to: ""                 # empty ⇒ currently valid
sources:
  - "raw/backend/payments.md:3"
tags: ["service", "backend", "entity"]
aliases: ["payments-api"]  # human-readable name; canonical ID stays above
props:                     # complete, lossless copy of the node fact's props
  lang: "go"
  name: "payments-api"
  summary: "Main API for payment requests."
  description: "Routes validated payment operations to the ledger."
lang: "go"                 # safe props are mirrored for Obsidian/Dataview
name: "payments-api"
summary: "Main API for payment requests."
depends_on:                  # ← relationship field (out-edges)
  - "[[svc-auth]]"
---
```

The note body is ordered for reading rather than graph auditing:

1. first H1 as the human name/title;
2. one-line summary lead and optional **About** prose;
3. **At a glance** attributes such as status, language, and timeframe;
4. **Connections** grouped under friendly forward/inverse labels;
5. timeline and source provenance;
6. fact IDs, namespaces, and edge audit data at the bottom under
   **Technical details**.

A relationship reads like
`Depends on [[Auth|Auth]] — Uses auth to validate access tokens`, rather than a
seven-column fact table. Edge properties, IDs, validity, namespaces, and source
locations remain available at the bottom for issue reports and audits.

Every node prop is retained under the frontmatter `props` object. Safe keys are
also mirrored at the top level so queries such as `TABLE lang` remain concise.
Reserved keys (`id`, `kind`, `tags`, and so on) cannot overwrite generated
metadata; their original fact values remain available through `props.<key>`.
Likewise, a custom edge type that collides with metadata or a mirrored prop is
emitted as `relation_<edge-type>`.

Each connection retains the source edge fact's ID, namespaces, validity window,
provenance, and properties, so a page can be checked directly against
`facts.jsonl`. Parallel temporal facts remain distinct. Duplicate logical edges
with the same type, endpoints, and validity are coalesced during resolve and
their descriptions/provenance are merged deterministically. The filename and
frontmatter `id` remain the canonical ontology ID.

## 4. Graph view

Every relationship is a `[[wikilink]]`, so Obsidian's **graph view** (left
ribbon, the connected-dots icon) renders the entity-relationship graph
directly. Tips:

- **Start focused** — open a single entity, then run *Command → Open local
  graph*. The full graph of a large vault is a hairball.
- **Filter** — exclude `index.md`, `catalog.md`, `overview.md`, and `log.md` in graph
  settings, or color/filter by the `#entity` tag. Entity notes use a flat
  layout, so there is no `entities/` path to filter.
- **Color groups** — use type tags (`#service`, `#team`) or namespace tags
  (`#backend`, `#frontend`) to color by ontology type or namespace.
- **Backlinks** — the panel at the bottom of each page is Obsidian's
  auto-generated inbound-reference list (incoming edges); the Connections
  section in the body is the explicit version.

## 5. Tags

Every entity page is tagged `[<type>, <ns>..., entity]` (e.g.
`["service", "backend", "entity"]`). `index`/`catalog`/`overview` carry their
page kind plus `lorekeep-wiki`.

- **Tag pane** (right sidebar) — click a tag to filter the file list.
- **Graph coloring** — color groups can key off tags (`#service`, `#backend`).
- **Search** — `tag:#service [lang:go]` finds Go services using Obsidian's
  property-search syntax.

## 6. Dataview queries (community plugin)

Install the **Dataview** community plugin (Settings → Community plugins →
Browse → "Dataview"), then paste any of these into a note:

All services with their stack and start date:

```dataview
TABLE lang, valid_from
FROM #service
```

The complete fact props are also queryable as nested Dataview fields, for
example `TABLE props.lang, props.status`. Top-level mirrors are provided for
ordinary, non-reserved prop keys; `props.<key>` is the canonical fallback.

Currently-valid entities (no end date — `valid_to` is the empty string for
"present"):

```dataview
TABLE type, valid_from
FROM #entity
WHERE valid_to = ""
```

Timeline (everything with a known start, newest first):

```dataview
TABLE valid_from, valid_to
FROM #entity
WHERE valid_from != ""
SORT valid_from DESC
```

By namespace (`ns` is a list, so use `contains`):

```dataview
TABLE type, valid_from
FROM #entity
WHERE contains(ns, "backend")
```

## 7. Upgrade old graphs for richer prose

The renderer has a truthful fallback for historical facts, but it never invents
domain content. If `index.md` warns that the graph schema is stale, run:

```bash
uvx lorekeep schema upgrade --dry-run
uvx lorekeep schema upgrade
uvx lorekeep compile
```

Stock v2/v3 schemas are backed up and upgraded to v4. Custom schemas are not
overwritten without `--force`. Recompile is the important second step: wiki-only
regeneration cannot manufacture summaries or relationship explanations absent
from `facts.jsonl`. See `overview.md` for exact content-quality coverage.

## 8. Refresh after compile

Wiki regeneration builds every page in a temp sibling directory, then
atomically replaces each `.md` file inside the existing vault. The vault root
is never replaced, so Obsidian's watcher and `.obsidian/` settings remain
intact while `compile` or the daemon regenerates. Each individual note is
always either its complete old or complete new version. Before publishing,
Lorekeep snapshots the existing markdown pages; if a replacement or stale-page
deletion fails, every attempted change is rolled back. If rollback itself
cannot complete, the snapshot is retained in `.wiki-rollback.tmp` for recovery.

You rarely need `lorekeep wiki` manually — it auto-regenerates after every
`facts.jsonl` mutation (`compile`, a real `resolve`, daemon auto-resolve on
merge). Use it (or `--open`) only to force a refresh.

## 9. Tolaria

The same `wiki/` folder opens in [Tolaria](https://tolaria.md)
(file-first, git-first, macOS/Win/Linux). The layout + relationship frontmatter
are shaped for it:

- **Portable flat vault** — [Tolaria can recursively discover Markdown notes](https://tolaria.md/reference/file-layout);
  Lorekeep deliberately emits one flat root so the same stable filenames and
  wikilinks behave identically in Tolaria and Obsidian.
- **Relationship panel + neighborhood** — every out-edge is a frontmatter field
  holding `[[wikilinks]]` (`depends_on`, `relates_to`, …), which Tolaria detects
  as relationships. Open an entity → the Inspector's Relationships panel shows
  them as clickable chips; *Neighborhood* mode pivots the note list by them.
- **Types** — the `type:` field (`service`, `team`, `decision`, …) drives Tolaria's
  type grouping in the sidebar.
- **Backlinks** — inbound edges (body `[[wikilinks]]`) show in Tolaria's
  backlinks panel.

To open: **File → Open vault** → select the `wiki/` folder (same one as Obsidian
— you can point either app at it). `lorekeep wiki --open` launches Obsidian; for
Tolaria, open the printed path manually.

> **One vault, two apps.** Obsidian and Tolaria read the same files, so you can
> use either (or both) on the same `wiki/`. Lorekeep keeps the generated vault
> flat for stable cross-app links; use the `type:` field, tags, or `catalog.md`
> to group notes.

## 10. Multi-device

The wiki is **derived**, not part of the backup (`lorekeep backup` tracks
`raw/` + `schema.json`). Each machine regenerates its own `wiki/` from
`facts.jsonl`. **Don't hand-edit wiki pages** — the next regen overwrites them
(`log.md` is the only append-only exception).

## Troubleshooting

- **Empty wiki / `facts.jsonl not found`** — run `lorekeep compile` first.
- **Pages show only structural fallback prose** — inspect the quality table in
  `overview.md`; upgrade the schema and re-run `compile` so the LLM can enrich
  facts at compile time. Running `wiki` alone only reprojects existing facts.
- **Unresolved `[[wikilink]]`** — slugs replace `:` and `/` with `-`
  (`svc:payments-api` → `svc-payments-api`). A collision between two node IDs
  that slug identically raises `ValueError` at generation rather than
  overwriting.
- **Obsidian didn't open with `--open`** — install Obsidian, or open the folder
  path the command printed via *Open vault*.
- **Dataview shows nothing** — enable the plugin (*Settings → Community
  plugins → Dataview → Enable*), and make sure *Enable Dataview queries* is on.

## Reference

- [Compiling](compile.md) — how `facts.jsonl` is produced.
- [Data home & path resolution](data-home.md) — where `wiki/` lives (env /
  `LOREKEEP_HOME` / dev / XDG).
- Re-generating from unchanged input is **byte-identical** (determinism);
  `log.md` is the only non-deterministic file (append-only).
- Override the wiki path: `LOREKEEP_WIKI=/path uvx lorekeep wiki`.
