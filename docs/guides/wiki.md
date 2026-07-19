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
directory. The same folder also opens in [Tolaria](https://github.com/refactoringhq/tolaria)
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

Flat layout — one `.md` per node at the root (Tolaria requires a flat vault;
Obsidian works the same either way):

```
wiki/
├── index.md              # catalog of every entity, grouped by type
├── overview.md           # graph stats dashboard (counts, temporal range, ns)
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
id: "svc:payments-api"
type: "service"
ns: ["backend"]
valid_from: "2024-01-15"
valid_to: ""                 # empty ⇒ currently valid
sources:
  - "raw/backend/payments.md:3"
tags: ["service", "backend", "entity"]
depends_on:                  # ← relationship field (out-edges)
  - "[[svc-auth]]"
---
```

The body shows a Properties table, explicit **Relationships** (`depends_on →`,
`← decided_by`, with validity windows as `from → to`), and a Timeline.

## 4. Graph view

Every relationship is a `[[wikilink]]`, so Obsidian's **graph view** (left
ribbon, the connected-dots icon) renders the entity-relationship graph
directly. Tips:

- **Start focused** — open a single entity, then run *Command → Open local
  graph*. The full graph of a large vault is a hairball.
- **Filter** — in graph settings, set *Files to exclude* or filter by
  `path:entities/` to drop `index`/`overview`/`log`.
- **Color groups** — add color groups by `path:entities/service/`,
  `path:entities/team/`, etc., or by tag (`#backend`, `#frontend`) to color by
  namespace.
- **Backlinks** — the panel at the bottom of each page is Obsidian's
  auto-generated inbound-reference list (incoming edges); the Relationships
  section in the body is the explicit version.

## 5. Tags

Every entity page is tagged `[<type>, <ns>..., entity]` (e.g.
`["service", "backend", "entity"]`). `index`/`overview` carry
`[index|overview, lorekeep-wiki]`.

- **Tag pane** (right sidebar) — click a tag to filter the file list.
- **Graph coloring** — color groups can key off tags (`#service`, `#backend`).
- **Search** — `tag:service lang:go` finds Go services via Obsidian's query
  syntax.

## 6. Dataview queries (community plugin)

Install the **Dataview** community plugin (Settings → Community plugins →
Browse → "Dataview"), then paste any of these into a note:

All services with their stack and start date:

```dataview
TABLE lang, valid_from
FROM #service
```

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

## 7. Refresh after compile

Wiki regeneration is **atomic** — pages build in a temp sibling dir, then
`os.rename` swaps into place. Obsidian always sees either the old or the new
version, never a mix, so it's safe to leave the vault open while `compile` or
the daemon regenerates. Obsidian picks up file changes live; if a page looks
stale, switch notes or re-open the vault.

You rarely need `lorekeep wiki` manually — it auto-regenerates after every
`facts.jsonl` mutation (`compile`, a real `resolve`, daemon auto-resolve on
merge). Use it (or `--open`) only to force a refresh.

## 9. Tolaria

The same `wiki/` folder opens in [Tolaria](https://github.com/refactoringhq/tolaria)
(file-first, git-first, macOS/Win/Linux). The flat layout + relationship
frontmatter are shaped for it:

- **Flat vault** — Tolaria indexes only root-level `.md`, so the flat layout is
  required (and is exactly what lorekeep emits).
- **Relationship panel + neighborhood** — every out-edge is a frontmatter field
  holding `[[wikilinks]]` (`depends_on`, `relates_to`, …), which Tolaria detects
  as relationships. Open an entity → the Inspector's Relationships panel shows
  them as clickable chips; *Neighborhood* mode pivots the note list by them.
- **Types** — the `type:` field (`service`, `team`, `concept`, …) drives Tolaria's
  type grouping in the sidebar.
- **Backlinks** — inbound edges (body `[[wikilinks]]`) show in Tolaria's
  backlinks panel.

To open: **File → Open vault** → select the `wiki/` folder (same one as Obsidian
— you can point either app at it). `lorekeep wiki --open` launches Obsidian; for
Tolaria, open the printed path manually.

> **One vault, two apps.** Obsidian and Tolaria read the same files, so you can
> use either (or both) on the same `wiki/`. The cosmetic difference: Obsidian
> has no `entities/<type>/` file-tree grouping (Tolaria is flat by design) — use
> the `type:` field, tags, or `index.md` to group instead.

## 10. Multi-device

The wiki is **derived**, not part of the backup (`lorekeep backup` tracks
`raw/` + `schema.json`). Each machine regenerates its own `wiki/` from
`facts.jsonl`. **Don't hand-edit wiki pages** — the next regen overwrites them
(`log.md` is the only append-only exception).

## Troubleshooting

- **Empty wiki / `facts.jsonl not found`** — run `lorekeep compile` first.
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
