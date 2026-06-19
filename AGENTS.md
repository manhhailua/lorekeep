# AGENTS.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Lorekeep compiles a team's raw markdown docs into a **temporal knowledge graph** (`facts.jsonl`) and exposes it **read-only** to coding agents (Claude Code, Cursor, Codex) over MCP, with per-namespace permission. The defining constraint: **there is no runtime write path.** A curator compiles offline; agents only read. Knowledge is processed once at compile time, not re-RAG'd per query.

## Commands

Python 3.11+, managed with **uv**. The CLI is `lorekeep` (entry: `src/lorekeep/cli.py`, Typer).

```bash
uv run pytest                                # full suite (~140 tests)
uv run pytest tests/test_perm.py -q          # one file
uv run pytest tests/test_perm.py::test_name  # one test
uv run pytest -k perm                        # by name match
uv run pytest --cov=lorekeep                 # coverage (pytest-cov)
uv build                                     # sdist + wheel (hatchling)
uv run lorekeep <command>                    # run the CLI in dev mode
```

### CLI commands (all run via `uv run lorekeep …`)

| Command | Purpose |
|---|---|
| `init` | Bootstrap data home (config + schema + raw/graph dirs) |
| `compile` | `raw/*.md` → `graph/facts.jsonl` + `manifest.json` (runs the LLM pipeline) |
| `check` | Validate compiled graph loads, no dangling edges (exit 1 on failure) |
| `eval` | Tier-1 construction P/R/F1 vs gold corpus + structure metrics |
| `serve [--transport stdio\|http]` | Run the read-only MCP server |
| `mcp add --agent claude\|cursor\|codex --ns NS` | Write agent MCP config (`.mcp.json`) |
| `import --from claude\|cursor` | Import agent sessions into `raw/` (claude: quick+deep; cursor: deep-only) |
| `doctor` | Verify install: graph loads, schema valid, a tool responds |
| `version` | Print version |

**Offline / no-LLM mode:** set `LOREKEEP_PROVIDER=fake` to make `compile` and `import` use `FakeProvider` with canned responses. **All CLI/compile/import tests use this** — no API key or real model required.

## Architecture: two strictly separated phases

```
COMPILE (offline, curator):  raw/<ns>/*.md → ingest → extract(LLM) → resolve → writer → facts.jsonl
SERVE   (runtime, per device): facts.jsonl → GraphStore → ScopedGraph(ns) → MCP → agent
```

These never overlap. `compile` mutates `facts.jsonl`; `serve` reads it and lazily reloads on mtime change — there is no live write API.

### Compile pipeline (`src/lorekeep/compile/`, orchestrated by `pipeline.py`)
`ingest` chunks markdown with `path:line` provenance → `extract` calls the LLM provider for schema-constrained nodes/edges/aliases (per-chunk SHA-256 hash cache → unchanged chunks return cached output, giving byte-stable recompiles) → `resolve` collapses alias variants to canonical entities and quarantines invalid facts → `writer` emits **sorted** `facts.jsonl` + `manifest.json`. Failures are skip-and-log (partial compile is valid); errors/quarantine land in the manifest.

### Shared contract (`models.py`)
Pydantic, all `frozen=True`, `extra="forbid"`. `Node` / `Edge` are the two `kind`s of a fact. **`Edge.from_` is the Python field name; `"from"` is its JSON alias** — always serialize with `by_alias=True`. `Schema`, `Manifest`, `DocChunk` round out the contract. `facts_io.py` loads facts; `Node`/`Edge` carry `ns`, `valid_from`/`valid_to`, `props`, and `src` (provenance).

### Store + permission (the most important layering to get right)
- **`store/graph.py` `GraphStore`** — pure graph logic over `networkx.MultiDiGraph`. **No permission, no MCP here.** Temporal queries: `snapshot(T)` (half-open `[valid_from, valid_to)`, `None` = unbounded), `history(id)`, `changes(t1,t2)`, BFS `neighbors`.
- **`perm/ns.py` `ScopedGraph`** — the **single permission chokepoint**. Wraps a `GraphStore` and filters *every* query. Deny-by-default: `effective_ns = allowed ∪ {public}`; a node is visible iff `ns ∩ effective_ns ≠ ∅`; an edge iff **both** endpoints visible **and** `edge.ns ∩ effective_ns ≠ ∅` (an edge never leaks a neighbor the caller can't see). **Any new query path must go through `ScopedGraph`, not `GraphStore` directly.**

### Serve (`mcp_server.py`)
`FastMCP` with 8 read-only tools (`search`, `get_node`, `neighbors`, `at_time`, `history`, `changes`, `list_namespaces`, `schema`). Module-global `ScopedGraph` is set by `configure()`; `_require()` lazy-reloads when `facts.jsonl` mtime changes (so `compile` is visible without reconnecting). Tools are plain functions registered with `@mcp.tool()` but stay directly callable — **tests invoke them directly, no MCP transport**. The writer uses atomic `os.replace` so lazy-reload never reads a half-written file.

### Path resolution (`paths.py`)
Pure (no I/O), 4-tier precedence high→low: explicit `LOREKEEP_RAW/OUT/CACHE/SCHEMA/CONFIG` env → `LOREKEEP_HOME` → **dev mode** (`.lorekeep/` or `raw/` present in CWD, or `LOREKEEP_DEV=1`; auto-detected in a source checkout) → XDG (`platformdirs`). Running `uv run lorekeep …` from the repo uses the repo's own `raw/` + `graph/` with zero migration.

### Provider pluggability (`compile/providers.py`)
`LiteLLMProvider` (OpenAI / Anthropic / DashScope/Qwen / Ollama via litellm model strings) and `FakeProvider` (tests/offline). Model is set in `config.yaml` as a litellm string.

## Configuration & keys

`config.yaml` (precedence: explicit `LOREKEEP_*` env > `LOREKEEP_HOME` > dev marker > XDG). **API keys never go in committed files** — use `provider.api_key_env` (name of an env var); inline `provider.api_key` is allowed only in the gitignored local config and warns on use. Serve-time scope comes from `LOREKEEP_NS` (comma-separated) env or `config.ns.default`. Template: `.lorekeep/config.yaml.example`.

## Conventions

- **Conventional Commits are enforced** — a `commit-msg` pre-commit hook (`scripts/check-conventional-commit.py`) plus CI (`lint-commits.yml`, checking both commit messages and PR title). Types: `build|chore|ci|docs|feat|fix|perf|refactor|revert|style|test`. Merge commits are exempt.
- **Releases are automated** — `release-please` runs on every push to `main` (`feat`=minor, `fix`=patch, `!`=major), opens an auto-merging Release PR, tags a GitHub Release on merge, and `release.yml` publishes to PyPI via OIDC trusted publishing. Do not version-bump by hand.
- **Determinism is a hard requirement** — recompiling unchanged input must be byte-identical (kept green by `test_determinism.py`). Preserve the sorted-output / cache behavior in `writer.py` and `extract.py` when changing the pipeline.
- **`graph/facts.jsonl` and `graph/manifest.json` are gitignored** (regenerated by `compile`); `graph/schema.json` is committed.

## Tests

`~140` tests, no network. Gold corpus in `tests/fixtures/gold/`, raw fixtures in `tests/fixtures/raw/`. Compile/serve/import tests set `LOREKEEP_*` env vars and `LOREKEEP_PROVIDER=fake` to pin paths and avoid the LLM — follow this pattern for any new CLI test rather than hitting a real provider.
