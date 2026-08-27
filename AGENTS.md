# AGENTS.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Lorekeep compiles a team's raw markdown docs into a **temporal knowledge graph** (`facts.jsonl`) and exposes it to coding agents (Claude Code, Cursor, Codex, opencode, Grok Build, Qoder, GitHub Copilot, Command Code) over MCP, with per-namespace permission. The MCP surface has 8 composable tools plus passive context resources. Agent writes are confidence-gated journals merged on resolve. Knowledge is processed once at compile time, not re-RAG'd per query.

## Commands

Python 3.11+, managed with **uv**. The CLI is `lorekeep` (entry: `src/lorekeep/cli.py`, Typer).

```bash
uv run pytest                                # full suite (~1,200 tests)
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
| `init` | Bootstrap data home (config + schema + raw/graph dirs); installs daemon OS service |
| `compile [--foreground]` | `raw/*.md` → `graph/facts.jsonl` + `manifest.json` + `wiki/` — defaults to **background** in interactive mode (delegates to daemon via `.compile-requested` sentinel), `--foreground` for synchronous |
| `wiki` | Regenerate `wiki/` from `facts.jsonl` (Obsidian-compatible markdown) |
| `serve [--transport stdio\|http]` | Run the MCP server (8 tools + passive resources) |
| `agent watch` | Start the daemon (drain lifecycle events, watch memory/raw/pending, compile/resolve) |
| `agent heal` | Run self-heal standalone (remove dangling edges, dedupe, flag issues) |
| `agent service install/uninstall/status` | Install/uninstall/status the daemon as an OS service (launchd/systemd) |
| `quarantine detect [--apply]` | List orphaned (zero-edge) nodes; `--apply` parks them via `props.quarantined_at`/`quarantined_reason` |
| `quarantine review` | Per quarantined node: `[r]estore` / `[k]eep` / `[s]kip` |
| `schema upgrade` | Upgrade stock schema to latest version (backs up previous, `--dry-run`/`--force` for custom schemas) |
| `mcp add --agent claude\|cursor\|codex\|opencode\|grok\|qoder\|copilot\|cmd [--scope user\|project] [--read-ns NS]` | Write agent MCP config (default scope from `agents.wire_scope`) |
| `config show` | Print config.yaml |
| `config set <key> <value>` | Set nested config value (dot notation) |
| `import --from claude\|cursor\|codex\|opencode\|grok\|qoder\|copilot\|cmd` | Import agent sessions into `raw/` |
| `doctor` | Validate full install: graph loads (no dangling edges), schema valid, MCP tools respond, provider reachable |
| `backup [--init <remote-url>] [--force]` | Sync durable inputs plus graph/wiki snapshot to a private backup Git repo; `--force` auto-resolves snapshot conflicts (remote wins) |
| `version` | Print version |
| `update [--check]` | Upgrade lorekeep to latest from PyPI (detects uv/pipx/pip); `--check` previews without upgrading |

**Offline / no-LLM mode:** tests inject `FakeProvider` via monkeypatch (`patch_make_provider` / `patch_make_import_provider` fixtures in `conftest.py`). **All CLI/compile/import tests use this** — no API key or real model required.

## Architecture: two strictly separated phases

```
COMPILE (offline, curator):  raw/<ns>/*.md → ingest → extract(LLM) → resolve → writer → facts.jsonl → wiki/
SERVE   (runtime, per device): facts.jsonl → GraphStore → ScopedGraph(ns) → MCP → agent
```

`compile` mutates `facts.jsonl` and auto-generates `wiki/`; `resolve` regenerates wiki only on actual merge (gated on `merge_count > 0`); `serve` reads `facts.jsonl` and lazily reloads on mtime change. Core write tools (`propose_change`, `merge_entities`, `review_note`) append to `pending/` journals; resolve merges accepted entries into the graph. Wiki regen is **best-effort** — never blocks `compile` or `resolve`. Wiki builds into a temp dir then `os.rename` swaps into place (atomic — never partially populated).

### Compile pipeline (`src/lorekeep/compile/`, orchestrated by `pipeline.py`)
`ingest` chunks markdown with `path:line` provenance → `extract` calls the LLM provider for schema-constrained nodes/edges/aliases (human-readable output uses the lowercase ISO 639-1 `compile.language`, default `en`; per-chunk SHA-256 hash cache → unchanged chunks return cached output, giving byte-stable recompiles) — **extraction is parallel** via `ThreadPoolExecutor` with `compile.max_workers` (default 4; 1 = sequential). **Streaming flush** — every `compile.flush_interval` completed chunks (default 10), resolve + write an intermediate `facts.jsonl` so the serve layer sees live graph updates during compile; the final resolve + write overwrites with deterministic edge IDs. Each flush applies `prev_aliases` graph dedup so intermediate graphs have no duplicate entities. → `resolve` collapses alias variants to canonical entities (using **union-find** over `same_as` edges and `merged_ids` props — handles conflicting directions, multi-target, transitive chains, and cycles deterministically) and quarantines invalid facts → `writer` emits **sorted** `facts.jsonl` + `manifest.json`. **Entity merge decisions persist** across recompiles — `compile_graph()` loads `merged_ids` from the previous `facts.jsonl` (via `_load_prev_aliases()` in `cli.py`) and passes them as `prev_aliases` to `resolve()`, so manual and LLM-detected merges are not lost when rebuilding from `raw/`. Changing `compile.language` changes the prompt fingerprint and invalidates affected cache entries. Failures are skip-and-log (partial compile is valid); errors/quarantine land in the manifest. **Compile errors are surfaced** — `_report_compile_errors()` in `cli.py` prints per-chunk failures to stderr and exits non-zero (code 1) when ALL chunks fail (0 nodes from non-empty input). The daemon (`agent watch`) passes `exit_on_total_failure=False` so it can keep running, but still logs errors. **Node ID prefixes are enforced deterministically** — `TypeSpec.id_prefix` (e.g. `svc` for `service`, `prj` for `project`) defines the canonical slug prefix; `parse_response()` normalizes LLM-supplied IDs (`service:x` → `svc:x`) and remaps edge endpoints to match. Legacy schemas without `id_prefix` are passed through unchanged. **Thread safety** — `ExtractionCache` and `FakeProvider` use `threading.Lock` for concurrent access.

### Shared contract (`models.py`)
Pydantic, all `frozen=True`, `extra="forbid"`. `Node` / `Edge` are the two `kind`s of a fact. **`Edge.from_` is the Python field name; `"from"` is its JSON alias** — always serialize with `by_alias=True`. `Schema`, `Manifest`, `DocChunk` round out the contract. `facts_io.py` loads facts; `Node`/`Edge` carry `ns`, `valid_from`/`valid_to`, `props`, and `src` (compile-time provenance — the `path:line` of the source chunk). **`provenance`** (a separate optional `dict[str, Any] | None` field) is stamped by `resolve.py` when journal-merged facts enter the graph — it records `{agent, confidence, proposed_at, device}` so every agent-contributed fact carries identity metadata.

### Store + permission (the most important layering to get right)
- **`store/graph.py` `GraphStore`** — pure graph logic over `networkx.MultiDiGraph`. **No permission, no MCP here.** Temporal queries: `snapshot(T)` (half-open `[valid_from, valid_to)`, `None` = unbounded), `history(id)`, `changes(t1,t2)`, BFS `neighbors`. **Alias resolution** — `GraphStore.__init__` builds `_alias_to_canonical` from every node's `merged_ids` props; `resolve_alias(id)` maps alias IDs to canonical; `get_node`, `neighbors`, and `history` all resolve aliases before lookup, so `get_node("person:manhhailua")` returns the canonical node that absorbed it. **`store/fts.py` `FTSIndex`** provides SQLite FTS5 full-text search over node names/props **and relationship facts** (edge type, endpoints, `description`). **`store/rank.py`** reranks FTS hits (facts: type weights demote `relates_to` + hop-distance to `center_id`, BFS cap 4; nodes: hop-distance then FTS) and packs up to four stock-schema semantic 1-hop neighbors (not `relates_to`/`same_as`). The `search` MCP tool queries that index first and falls back to an in-memory scan; the index is rebuilt lazily on stale mtime. MCP `search` defaults `as_of` to today (`as_of="all"` disables) as a **hit filter**, not a full graph snapshot (`temporal_query` `at_time`); `ScopedGraph.search*` leave `as_of=None` (no time filter) so eval keeps full history.
- **`perm/ns.py` `ScopedGraph`** — the **single permission chokepoint**. Wraps a `GraphStore` and filters *every* query. Deny-by-default: `effective_ns = allowed ∪ {public}`; a node is visible iff `ns ∩ effective_ns ≠ ∅`; an edge iff **both** endpoints visible **and** `edge.ns ∩ effective_ns ≠ ∅` (an edge never leaks a neighbor the caller can't see). **Any new query path must go through `ScopedGraph`, not `GraphStore` directly.**

### Serve (`mcp_server.py`)
`FastMCP` exposes exactly 8 tools: `search`, `get_node`, `neighbors`, `temporal_query`, `context`, `propose_change`, `merge_entities`, `review_note`. Schema, visible namespaces, and status also have passive resources (`lorekeep://schema`, `lorekeep://namespaces`, `lorekeep://status`). Module-global `ScopedGraph` is set by `configure()`; `_require()` lazy-reloads when `facts.jsonl` mtime changes. `_manifest` loads `manifest.json` for freshness/coverage. Tools remain plain directly callable functions — **tests invoke them directly, no MCP transport**. Every query, including `context("status")`, passes through `ScopedGraph`; no unscoped store or hidden namespace/count metadata is exposed over MCP. **`get_node` resolves alias IDs** — if the requested id was absorbed into a canonical entity during resolve, the canonical node is returned with `_resolved_from_alias` set to the original id for transparency. The writer uses atomic `os.replace` so lazy-reload never reads a half-written file.

### Wiki (`wiki.py`)
Pure JSONL → markdown transform (no LLM). `generate_wiki(graph_dir, wiki_dir)` reads `facts.jsonl` via `GraphStore` → entity pages (`entities/<type>/<slug>.md` with YAML frontmatter, wikilinks, props table), `index.md` (catalog), `overview.md` (stats dashboard), `log.md` (append-only, preserved across regen). Builds into `.wiki-build.tmp` then `os.rename` swaps into place (atomic). `_slug()` replaces `:`/`/` with `-`; collisions raise `ValueError`. YAML scalars quoted via `json.dumps` so IDs like `svc:payments-api` parse correctly. Props table escapes `\|`, collapses newlines, serializes non-strings. Regenerates on every `facts.jsonl` mutation: `compile` (single regen — `_do_auto_resolve` returns bool, compile skips if resolve already regend), `resolve` (gated on merge/flag), daemon auto-resolve (on actual merge). Best-effort — never blocks compile or resolve.

### Path resolution (`paths.py`)
Pure (no I/O), 4-tier precedence high→low: explicit `LOREKEEP_RAW/OUT/CACHE/SCHEMA/CONFIG/WIKI` env → `LOREKEEP_HOME` → **dev mode** (`.lorekeep/` present in CWD, or `LOREKEEP_DEV=1`; auto-detected in a source checkout) — all data lives under `cwd/.lorekeep/` (`config.yaml`, `schema.json`, `raw/`, `graph/`, `wiki/`, `pending/`, `cache.json`), mirroring the `LOREKEEP_HOME` layout → default `~/.lorekeep/`. Running `uv run lorekeep …` from the repo uses the repo's own `.lorekeep/` data home with zero migration.

### Daemon (`cli.py` watch command)
Polls every 60s. Native lifecycle handlers only enqueue bounded metadata under device-local `hook-events/`; exact `SessionEnd`/`sessionEnd` events drain immediately, while opencode `session.idle` and Command Code `Stop` wait `agents.session_end_idle_seconds` (default 300). The daemon resolves the named transcript through the registry, writes deterministic `raw/<agent>-session/` Markdown, retries failures with backoff, and forces compile in the same cycle. Copilot capture is user/local-only because repository hooks also run in cloud jobs; Cursor `sessionEnd` is IDE-local and supports both scopes. A one-time startup recovery handles missed events; live transcript polling is not the primary path. Claude/Codex curated memory dirs are still polled and quick-imported. raw/ watch tracks both file count and mtime; pending/ watch triggers resolve. Interactive `compile` delegates through `.compile-requested`; non-interactive mode runs foreground. Self-heal, auto-backup/external-compile detection, and persistent service behavior are detailed in `docs/architecture/agent.md`.

### Provider pluggability (`compile/providers.py`)
`LiteLLMProvider` (OpenAI / Anthropic / DashScope/Qwen / DeepSeek / Ollama via litellm model strings) and `FakeProvider` (tests/offline). Model is set in `config.yaml` as a litellm string. Model names from the provider picker are **always normalized** to `{provider}/{model}` format (e.g., `deepseek/deepseek-chat`, not `deepseek-chat`) — litellm's catalog contains both prefixed and non-prefixed variants, but non-prefixed names fail at runtime for providers like DeepSeek/Gemini that litellm can't auto-detect by pattern. `list_models()` in `providers.py` de-duplicates and normalizes. `setup_observability()` configures litellm success/failure callbacks for Langfuse or Langsmith when `observability.provider` is set in config. Prefix cache optimized: schema in system prompt (constant across chunks), user message = chunk text only.

## Configuration & keys

`config.yaml` (precedence: explicit `LOREKEEP_*` env > `LOREKEEP_HOME` > dev marker > XDG). **API keys never go in committed files** — use `provider.api_key_env` (name of an env var); inline `provider.api_key` is allowed only in the gitignored local config. `compile.language` is a lowercase ISO 639-1 code that controls extracted prose and defaults to `en`. Read scope comes from `LOREKEEP_READ_NS` or `namespaces.read` (default `["*"]`); write ownership is one concrete `namespaces.write` value (default `me`). `agents.session_end_idle_seconds` controls only fallback lifecycle events. Default wiring follows central read scope unless `--read-ns` is explicit. Runtime migrates legacy `LOREKEEP_NS` and `ns.default` / `ns.personal`. Template: `.lorekeep/config.yaml.example`.

### Backup

`lorekeep backup --init <remote-url>` initializes a **separate** git repo inside the data home and pushes to a private remote on `backup.branch` (default `main`). It tracks durable `raw/`, `schema.json`, `pending/` plus graph/wiki snapshots, while ignoring credentials, caches, indexes, logs, and device-local `hook-events/`. Generated snapshots are non-mergeable. `--force` accepts the remote graph/wiki snapshot during conflicts; `backup.auto_resolve_durable` structurally merges JSON/JSONL and uses the provider for Markdown conflicts. See `docs/guides/backup.md`.

## Conventions

- **Git workflow: always use pull requests, always merge-commit.** Never push directly to `main`, and **never squash**. Repo settings have squash + rebase disabled — only merge-commit is available. Every change goes through a feature branch → PR → review → **merge commit**. Reason: merge-commit preserves each conventional-commit on `main`, so `release-please` generates a per-commit changelog (squash collapses a multi-commit PR into one entry). Branch naming: `type/short-desc` (e.g. `feat/agent-ingest`, `fix/watch-try-except`). All PRs must pass CI before merge.
- **Conventional Commits are enforced on every commit** — a `commit-msg` pre-commit hook (`scripts/check-conventional-commit.py`) plus CI (`lint-commits.yml`, checking each commit message in the PR and the PR title). With merge-commit, **every commit in a PR is linted**, so write a conventional message for each (not just the PR title). Types: `build|chore|ci|docs|feat|fix|perf|refactor|revert|style|test`. Merge commits themselves are exempt (`Merge pull request …`).
- **Releases are automated** — `release-please` runs on every push to `main` (`feat`=minor, `fix`=patch, `!`=major), opens an auto-merging Release PR, tags a GitHub Release on merge, and publishes to PyPI via OIDC trusted publishing (inline publish job). Do not version-bump by hand.
- **Determinism is a hard requirement** — recompiling unchanged input must be byte-identical (kept green by `test_determinism.py`). Preserve the sorted-output / cache behavior in `writer.py` and `extract.py` when changing the pipeline.
- **Core regression tests are mandatory** — `test_core_regression.py` guards the 8 pillars of the pipeline (provider class structure, extraction, compile→graph, GraphStore queries, ScopedGraph permission, MCP tools, wiki, resolve). Any change to `providers.py`, `extract.py`, `pipeline.py`, `mcp_server.py`, `wiki.py`, `store/graph.py`, or `perm/ns.py` MUST pass `test_core_regression.py`. If you add a new method to `LiteLLMProvider`, verify it's INSIDE the class (not accidentally outside due to a function insertion — this caused a critical bug where `extract_json` became a nested function of `setup_observability`).
- **In the Lorekeep source-code repository**, dev-home `graph/facts.jsonl`, `graph/manifest.json`, and `wiki/` remain gitignored (regenerated by `compile`), while `.lorekeep/schema.json` is committed. This is separate from the private data-home backup repository, which tracks the latest graph/wiki snapshot.

## Code change discipline — tests and docs in every PR

Every source code change (bug fix, feature, refactor) ships with **both** updated tests and updated docs in the same PR. A PR with code changes but missing tests or docs is incomplete and will be blocked at review. Do not defer either to a follow-up.

### 1. Tests

- **Run coverage** on the touched module(s): `uv run pytest --cov=lorekeep.<module> --cov-report=term-missing tests/`. Lines added or changed must be exercised by at least one test.
- **New code path (branch/condition) → new test.** If you add an `if`/`elif`/`except` branch, write a test that hits it. Untested branches are treated as incomplete work.
- **Regression guard**: any fix must include a test that fails without the fix and passes with it.
- **One test per public function/method** that changed. If the function has multiple code paths, write one test per path.
- **Follow the `FakeProvider` pattern** (`patch_make_provider` / `patch_make_import_provider` in `conftest.py`) — never hit a real LLM in tests.
- **Test naming**: `test_<unit>_<scenario>_<expected_outcome>` (e.g. `test_merge_journals_high_confidence_auto_merges`).
- **Fixtures over setup blocks** — reuse and extend `conftest.py` fixtures rather than duplicating boilerplate.
- **`test_core_regression.py` is the gate** — any change to `providers.py`, `extract.py`, `pipeline.py`, `mcp_server.py`, `wiki.py`, `store/graph.py`, or `perm/ns.py` MUST pass it.
- **Determinism tests** (`test_determinism.py`) must remain green if the pipeline or writer changes.

### 2. Docs

- **AGENTS.md** is the single source of truth for agents working in this repo. When you add, rename, or remove a module, command, tool, field, or config key, update AGENTS.md in the same PR. Stale instructions cause bugs.
- **Architecture and guide docs** (`docs/architecture/*.md`, `docs/guides/*.md`) must reflect current behavior, not aspirations. When a feature changes how the system works, update the relevant page.
- **When adding a new CLI command**, run `scripts/generate_cli_reference.py` to regenerate `docs/reference/cli.md`, then commit it.
- **When changing a model** (`models.py`), update `docs/architecture/data-model.md` with the new field and a facts.jsonl example.
- **`test_docs_contract.py`** is the docs gate — it enforces that active docs don't advertise removed commands, local links resolve, the MCP tool list matches the runtime, and the CLI reference is generated from the live Typer app.
- **Doc inconsistency checklist** — before merging, verify: the CLI table in AGENTS.md matches `grep '@.*command' src/lorekeep/cli.py`; the MCP tool count in AGENTS.md matches `len(asyncio.run(mcp.mcp.list_tools()))`; the schema version in AGENTS.md matches `defaults.py`; the Node/Edge field list in AGENTS.md matches `models.py`.

### 3. Keeping docs lean

Docs that grow without pruning become unreadable and then unmaintained. Every doc edit must respect these rules:

- **One canonical location per fact.** If a feature is described in `pipeline.md`, do not re-describe it in `compile.md` — link with `[Pipeline: section](../architecture/pipeline.md#section)` instead. AGENTS.md is a reference index, not a second copy of the architecture docs.
- **Edit in place, do not append.** When behavior changes, rewrite the existing paragraph to match — do not add a new paragraph that contradicts or supersedes the old one.
- **Delete stale text.** If a sentence no longer reflects reality, remove it. Do not leave it with a "(legacy)" or "(deprecated)" footnote.
- **Prefer tables and bullet lists over prose.** A 3-row table is easier to scan and maintain than a paragraph of the same information.
- **Document what and why, not line-by-line how.** If the code already says it clearly, the doc should say it once.
- **AGENTS.md stays compact.** If it grows past ~200 lines of prose, move detail to the relevant `docs/` page and replace with a pointer.

## Tests

`~1,200` tests, no network. Gold corpus in `tests/fixtures/gold/`, raw fixtures in `tests/fixtures/raw/`. **`test_core_regression.py` is the critical safety net** — it verifies the full pipeline end-to-end (provider → extract → compile → graph → MCP → wiki → resolve). Compile/serve/import tests inject `FakeProvider` via `patch_make_provider` / `patch_make_import_provider` conftest fixtures to pin paths and avoid the LLM — follow this pattern for any new CLI test rather than hitting a real provider.

## Cursor Cloud specific instructions

The startup update script already runs `uv sync`; the toolchain (Python 3.11+ via `uv`) is ready. Standard commands live in the `## Commands` table above — use those.

Non-obvious caveats for running/testing here:

- **No API key needed for tests.** Tests inject `FakeProvider` via `patch_make_provider` / `patch_make_import_provider` conftest fixtures. For manual smoke testing, configure a real provider in `config.yaml`. To avoid polluting the repo's `.lorekeep/` data home, run demos against a throwaway data home: `LOREKEEP_HOME=/tmp/lk uv run lorekeep <cmd>`.
- **End-to-end smoke flow** (offline): `init` → drop a markdown file under `.lorekeep/raw/<ns>/` → `compile` → `doctor`. Then query the graph by calling the `mcp_server.py` tools directly after `ms.configure(graph_dir=..., allowed_ns=[...], write_ns="me", schema_path=...)`. `search` returns `{nodes, facts}`; `get_node` returns a node; `neighbors` and `temporal_query` return scoped graph data; `context` returns ontology, namespaces, and status.
- **`lorekeep serve` blocks on stdio** waiting for an MCP client — it won't return on its own. To just confirm it boots, run it under `timeout` with stdin closed (`timeout 3 uv run lorekeep serve --transport stdio </dev/null`); a clean timeout with no error output means success.
- **`tests/test_mcp_reload.py::test_lazy_reload_on_facts_change` is timing-flaky** — lazy-reload triggers off `facts.jsonl` mtime, and the test can rewrite the file within one mtime tick on fast filesystems, so it intermittently fails then passes on re-run. Treat an isolated failure of just this test as a flake, not a regression.
