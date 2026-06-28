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
# edit model / api_base as needed
```

For strict privacy, use a local model:

```yaml
provider:
  backend: ollama
  model: ollama/llama3
  api_base: http://localhost:11434
```

## 3. Compile

```bash
uv run lorekeep compile
```

Produces `graph/facts.jsonl` + `graph/manifest.json`. Re-running is idempotent:
unchanged input yields a byte-identical file (extraction is cached under
`.lorekeep/cache.json`).

**What compile does not do:** compile processes only `raw/`. Agent-proposed
facts in `pending/` journals are merged by `resolve` (see step 5).

## 4. Resolve pending facts (manual)

```bash
uv run lorekeep resolve
```

Merges all pending agent-proposed facts from `pending/` journals into `facts.jsonl`.
Facts are gated by confidence: high (≥0.8) auto-merge, medium (0.5-0.8) merge
with review flag, low (<0.5) quarantine.

Resolve also runs automatically: the daemon (`lorekeep agent watch`) resolves
every 5 minutes or after 50 pending entries.

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

See [serve.md](serve.md) to expose the graph to Claude Code / Cursor / Codex over MCP.

## Data home

See [data-home.md](data-home.md) for the 4-tier path resolution (env > `LOREKEEP_HOME` > dev mode > XDG) and `lorekeep init`.
