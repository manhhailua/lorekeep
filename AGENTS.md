# AGENTS.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Lorekeep compiles a team's raw markdown docs into a **temporal knowledge graph** (`facts.jsonl`) and exposes it to coding agents (Claude Code, Cursor, Codex, opencode) over MCP, with per-namespace permission. Agents read facts through 9 read tools and propose new facts through 5 journal-based write tools (confidence-gated, merged on resolve). Knowledge is processed once at compile time, not re-RAG'd per query.

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
| `compile` | `raw/*.md` → `graph/facts.jsonl` + `manifest.json` + `wiki/` (runs the LLM pipeline, auto-generates wiki) |
| `check` | Validate compiled graph loads, no dangling edges (exit 1 on failure) |
| `eval` | Tier-1 construction P/R/F1 vs gold corpus + structure metrics |
| `eval-locomo` | Tier-2 LoCoMo retrieval/temporal/abstention eval |
| `wiki` | Regenerate `wiki/` from `facts.jsonl` (Obsidian-compatible markdown) |
| `serve [--transport stdio\|http]` | Run the MCP server (9 read + 5 write tools) |
| `mcp add --agent claude\|cursor\|codex\|opencode --ns NS` | Write agent MCP config |
| `config show` | Print config.yaml |
| `config set <key> <value>` | Set nested config value (dot notation) |
| `import --from claude\|cursor\|codex\|opencode` | Import agent sessions into `raw/` |
| `doctor` | Verify install: graph loads, schema valid, a tool responds |
| `backup [--init <remote-url>]` | Commit + push `.lorekeep/` to your private backup git repo |
| `version` | Print version |

**Offline / no-LLM mode:** tests inject `FakeProvider` via monkeypatch (`patch_make_provider` / `patch_make_import_provider` fixtures in `conftest.py`). **All CLI/compile/import tests use this** — no API key or real model required.

## Architecture: two strictly separated phases

```
COMPILE (offline, curator):  raw/<ns>/*.md → ingest → extract(LLM) → resolve → writer → facts.jsonl → wiki/
SERVE   (runtime, per device): facts.jsonl → GraphStore → ScopedGraph(ns) → MCP → agent
```

`compile` mutates `facts.jsonl` and auto-generates `wiki/`; `resolve` regenerates wiki only on actual merge (gated on `merge_count > 0`); `serve` reads `facts.jsonl` and lazily reloads on mtime change. Write tools (propose_fact, link_facts, etc.) append to `pending/` journals; resolve merges them into the graph. Wiki regen is **best-effort** — never blocks `compile` or `resolve`. Wiki builds into a temp dir then `os.rename` swaps into place (atomic — never partially populated).

### Compile pipeline (`src/lorekeep/compile/`, orchestrated by `pipeline.py`)
`ingest` chunks markdown with `path:line` provenance → `extract` calls the LLM provider for schema-constrained nodes/edges/aliases (per-chunk SHA-256 hash cache → unchanged chunks return cached output, giving byte-stable recompiles) → `resolve` collapses alias variants to canonical entities and quarantines invalid facts → `writer` emits **sorted** `facts.jsonl` + `manifest.json`. Failures are skip-and-log (partial compile is valid); errors/quarantine land in the manifest.

### Shared contract (`models.py`)
Pydantic, all `frozen=True`, `extra="forbid"`. `Node` / `Edge` are the two `kind`s of a fact. **`Edge.from_` is the Python field name; `"from"` is its JSON alias** — always serialize with `by_alias=True`. `Schema`, `Manifest`, `DocChunk` round out the contract. `facts_io.py` loads facts; `Node`/`Edge` carry `ns`, `valid_from`/`valid_to`, `props`, and `src` (provenance).

### Store + permission (the most important layering to get right)
- **`store/graph.py` `GraphStore`** — pure graph logic over `networkx.MultiDiGraph`. **No permission, no MCP here.** Temporal queries: `snapshot(T)` (half-open `[valid_from, valid_to)`, `None` = unbounded), `history(id)`, `changes(t1,t2)`, BFS `neighbors`.
- **`perm/ns.py` `ScopedGraph`** — the **single permission chokepoint**. Wraps a `GraphStore` and filters *every* query. Deny-by-default: `effective_ns = allowed ∪ {public}`; a node is visible iff `ns ∩ effective_ns ≠ ∅`; an edge iff **both** endpoints visible **and** `edge.ns ∩ effective_ns ≠ ∅` (an edge never leaks a neighbor the caller can't see). **Any new query path must go through `ScopedGraph`, not `GraphStore` directly.**

### Serve (`mcp_server.py`)
`FastMCP` with 9 read tools (`search`, `get_node`, `neighbors`, `at_time`, `history`, `changes`, `list_namespaces`, `schema`, `meta`) and 5 write tools (`propose_fact`, `link_facts`, `flag_contradiction`, `update_fact`, `suggest_improvement`). Module-global `ScopedGraph` is set by `configure()`; `_require()` lazy-reloads when `facts.jsonl` mtime changes (so `compile` is visible without reconnecting). `_manifest` global loads `manifest.json` alongside facts (for `meta` tool's `compiled_at` + `merged_count`). Tools are plain functions registered with `@mcp.tool()` but stay directly callable — **tests invoke them directly, no MCP transport**. The writer uses atomic `os.replace` so lazy-reload never reads a half-written file.

### Wiki (`wiki.py`)
Pure JSONL → markdown transform (no LLM). `generate_wiki(graph_dir, wiki_dir)` reads `facts.jsonl` via `GraphStore` → entity pages (`entities/<type>/<slug>.md` with YAML frontmatter, wikilinks, props table), `index.md` (catalog), `overview.md` (stats dashboard), `log.md` (append-only, preserved across regen). Builds into `.wiki-build.tmp` then `os.rename` swaps into place (atomic). `_slug()` replaces `:`/`/` with `-`; collisions raise `ValueError`. YAML scalars quoted via `json.dumps` so IDs like `svc:payments-api` parse correctly. Props table escapes `\|`, collapses newlines, serializes non-strings. Regenerates on every `facts.jsonl` mutation: `compile` (single regen — `_do_auto_resolve` returns bool, compile skips if resolve already regend), `resolve` (gated on merge/flag), daemon auto-resolve (on actual merge). Best-effort — never blocks compile or resolve.

### Path resolution (`paths.py`)
Pure (no I/O), 4-tier precedence high→low: explicit `LOREKEEP_RAW/OUT/CACHE/SCHEMA/CONFIG/WIKI` env → `LOREKEEP_HOME` → **dev mode** (`.lorekeep/` present in CWD, or `LOREKEEP_DEV=1`; auto-detected in a source checkout) — all data lives under `cwd/.lorekeep/` (`config.yaml`, `schema.json`, `raw/`, `graph/`, `wiki/`, `pending/`, `cache.json`), mirroring the `LOREKEEP_HOME` layout → XDG (`platformdirs`). Running `uv run lorekeep …` from the repo uses the repo's own `.lorekeep/` data home with zero migration.

### Daemon (`cli.py` watch command)
Polls every 60s. `_discover_watchable_sessions()` finds Claude `memory/` + Codex `memories/` dirs (called every cycle — detects new sessions after daemon start). `_quick_import_session()` dispatches per-agent quick import (zero LLM cost — copies `.md` files to `raw/<agent>-memory/`). Cursor/opencode have no quick-import path — handled by session-end hooks (`lorekeep hook`). raw/ watch tracks both file count AND mtime — detects new files even if mtime is same (fast filesystem). pending/ watch triggers `_do_auto_resolve()` → merge journals → wiki regen. Init calls `_auto_import_and_compile()` which runs compile + wiki regen + auto-resolve — graph + wiki produced immediately if API key available.

### Provider pluggability (`compile/providers.py`)
`LiteLLMProvider` (OpenAI / Anthropic / DashScope/Qwen / DeepSeek / Ollama via litellm model strings) and `FakeProvider` (tests/offline). Model is set in `config.yaml` as a litellm string. `setup_observability()` configures litellm success/failure callbacks for Langfuse or Langsmith when `observability.provider` is set in config. Prefix cache optimized: schema in system prompt (constant across chunks), user message = chunk text only.

## Configuration & keys

`config.yaml` (precedence: explicit `LOREKEEP_*` env > `LOREKEEP_HOME` > dev marker > XDG). **API keys never go in committed files** — use `provider.api_key_env` (name of an env var); inline `provider.api_key` is allowed only in the gitignored local config. Serve-time scope comes from `LOREKEEP_NS` (comma-separated) env or `config.ns.default`. Template: `.lorekeep/config.yaml.example`.

### Backup

`lorekeep backup --init <remote-url>` initializes a **separate** git repo inside the data home (`.lorekeep/` in dev) and pushes it to your private `<remote-url>`. Subsequent `lorekeep backup` calls commit and push changes. The backup repo tracks `raw/` and `schema.json`; it ignores `config.yaml` (may hold an API key) and the regenerable `graph/facts.jsonl`, `graph/manifest.json`, `cache.json`. This is independent of the lorekeep tool repository.

## Conventions

- **Git workflow: always use pull requests.** Never push directly to `main`. Every change goes through a feature branch → PR → review → squash merge. Branch naming: `type/short-desc` (e.g. `feat/agent-ingest`, `fix/watch-try-except`). All PRs must pass CI before merge.
- **Conventional Commits are enforced** — a `commit-msg` pre-commit hook (`scripts/check-conventional-commit.py`) plus CI (`lint-commits.yml`, checking both commit messages and PR title). Types: `build|chore|ci|docs|feat|fix|perf|refactor|revert|style|test`. Merge commits are exempt.
- **Releases are automated** — `release-please` runs on every push to `main` (`feat`=minor, `fix`=patch, `!`=major), opens an auto-merging Release PR, tags a GitHub Release on merge, and publishes to PyPI via OIDC trusted publishing (inline publish job). Do not version-bump by hand.
- **Determinism is a hard requirement** — recompiling unchanged input must be byte-identical (kept green by `test_determinism.py`). Preserve the sorted-output / cache behavior in `writer.py` and `extract.py` when changing the pipeline.
- **`graph/facts.jsonl`, `graph/manifest.json`, and `wiki/` are gitignored** (regenerated by `compile`); `.lorekeep/schema.json` is committed.

## Tests

`~300` tests, no network. Gold corpus in `tests/fixtures/gold/`, raw fixtures in `tests/fixtures/raw/`. Compile/serve/import tests inject `FakeProvider` via `patch_make_provider` / `patch_make_import_provider` conftest fixtures to pin paths and avoid the LLM — follow this pattern for any new CLI test rather than hitting a real provider.

## Cursor Cloud specific instructions

The startup update script already runs `uv sync`; the toolchain (Python 3.11+ via `uv`) is ready. Standard commands live in the `## Commands` table above — use those.

Non-obvious caveats for running/testing here:

- **No API key needed for tests.** Tests inject `FakeProvider` via `patch_make_provider` / `patch_make_import_provider` conftest fixtures. For manual smoke testing, configure a real provider in `config.yaml`. To avoid polluting the repo's `.lorekeep/` data home, run demos against a throwaway data home: `LOREKEEP_HOME=/tmp/lk uv run lorekeep <cmd>`.
- **End-to-end smoke flow** (offline): `init` → drop a markdown file under `.lorekeep/raw/<ns>/` → `compile` → `check`/`doctor`. Then query the graph by calling the `mcp_server.py` tools directly after `ms.configure(graph_dir=..., allowed_ns=[...], schema_path=...)` — the read tools (`search`, `get_node`, `neighbors`, `at_time`, `history`, `list_namespaces`) are plain functions, no MCP transport required. `search` returns a list of node-id strings; `get_node` returns a dict; `neighbors`/`at_time`/`changes` return `{"nodes": [...], "edges": [...]}`.
- **`lorekeep serve` blocks on stdio** waiting for an MCP client — it won't return on its own. To just confirm it boots, run it under `timeout` with stdin closed (`timeout 3 uv run lorekeep serve --transport stdio </dev/null`); a clean timeout with no error output means success.
- **`tests/test_mcp_reload.py::test_lazy_reload_on_facts_change` is timing-flaky** — lazy-reload triggers off `facts.jsonl` mtime, and the test can rewrite the file within one mtime tick on fast filesystems, so it intermittently fails then passes on re-run. Treat an isolated failure of just this test as a flake, not a regression.
