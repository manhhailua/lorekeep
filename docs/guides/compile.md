# Compiling the knowledge graph

Knowledge enters Lorekeep through three paths. This guide covers the curator-side compile path; agents contribute knowledge at runtime through [write tools](serve.md).

## 1. Add raw docs

Drop markdown under `raw/<namespace>/`:

```
raw/backend/payments.md
raw/frontend/web.md
```

The first directory under `raw/` becomes the fact's `ns` (e.g. `backend`, `frontend`).

## 2. Configure a provider

```bash
cp .lorekeep/config.yaml.example .lorekeep/config.yaml
# edit model as needed (native providers need no api_base)
```

For strict privacy, use a local model:

```yaml
provider:
  model: ollama/llama3                      # default http://localhost:11434; set api_base only for a non-default host
  timeout_seconds: 120
  max_retries: 2
```

## 3. Compile

```bash
uv run lorekeep compile
```

Produces `graph/facts.jsonl` + `graph/manifest.json` + auto-generates `wiki/`
(Obsidian-compatible markdown, with each page replaced atomically). If `pending/`
journals exist, compile also merges them via `_do_auto_resolve` — in that
case wiki regenerates once from the resolved facts (never double).
Re-running is idempotent: unchanged input yields byte-identical files
(extraction is cached under `.lorekeep/cache.json`).

Schema v4 enriches facts during this existing LLM pass; it does **not** add a
second wiki-time LLM call. Each node is asked for a source-grounded `summary`
and optional longer `description`, and each edge for a concrete
`props.description` explaining the relationship. Prose stays in the source
language. `resolve` deterministically merges complementary descriptions and
coalesces duplicate logical edges before the wiki is projected.

`manifest.json` records `content_quality` coverage for labels, summaries,
descriptions, relationship explanations, generic edges, and duplicate display
labels. Missing prose produces a warning but does not discard an otherwise
valid fact or make a partial compile fail.

Each LLM request defaults to a 120-second timeout and two retries. Override
`provider.timeout_seconds` or `provider.max_retries` when the configured
endpoint needs a different policy. If every attempt fails, Lorekeep records
that chunk in `manifest.json` and continues producing a partial graph.

**What compile does not do:** compile processes only `raw/`. Agent-proposed
facts in `pending/` journals are merged by `resolve` (see step 5).
Compile does re-merge pending journals as a convenience, but standalone
`resolve` is needed if you add journals after compile.

## 4. Resolve pending facts (manual)

```bash
uv run lorekeep resolve
```

Merges all pending agent-proposed facts from `pending/` journals into `facts.jsonl`.
Facts are gated by confidence: high (≥0.8) auto-merge, medium (0.5-0.8) merge
with review flag, low (<0.5) quarantine. Wiki regenerates only if facts
actually changed (`merge_count > 0` or `flagged_count > 0`).

Resolve also runs automatically: the daemon (`lorekeep agent watch`) detects
new pending entries on every poll cycle (default 60s interval).

## Upgrade an existing ontology

Before recompiling an existing stock v2/v3 data home:

```bash
uv run lorekeep schema upgrade --dry-run
uv run lorekeep schema upgrade
uv run lorekeep compile
```

The upgrade writes a versioned backup such as `schema.v3.backup.json`, is
idempotent, and leaves custom schemas untouched unless `--force` is explicit.
Historical facts still render, but only recompilation can add grounded summaries
and edge explanations that were absent from the old graph.

## 5. Full pipeline (compile + resolve)

```bash
uv run lorekeep compile && uv run lorekeep resolve
```

Or let the daemon handle it:

```bash
uv run lorekeep agent watch    # watches raw/ + pending/ → auto-compile + resolve
```

## 6. Evaluate construction quality

Author gold facts under `tests/fixtures/gold/<name>.facts.jsonl`, then:

```bash
uv run lorekeep eval
```

Reports extraction P/R/F1, entity-resolution F1, and graph-structure metrics.
Snapshots to `.lorekeep/eval/results.json`.

## 7. Validate

```bash
uv run lorekeep check
```

Exits non-zero if the graph has dangling edges.

## How agents contribute knowledge

Agents propose facts at runtime through MCP write tools (see [serve.md](serve.md)).
These are appended to `pending/<ns>/journal.jsonl` at **zero LLM cost** — the
agent already ran the LLM for the conversation. Facts become searchable after
the next resolve.

## Next: serve to agents

See [serve.md](serve.md) to expose the graph to Claude Code / Cursor / Codex over MCP,
or [wiki.md](wiki.md) to browse the graph as Obsidian markdown.

## Data home

See [data-home.md](data-home.md) for the 4-tier path resolution (env > `LOREKEEP_HOME` > dev mode > XDG) and `lorekeep init`.
