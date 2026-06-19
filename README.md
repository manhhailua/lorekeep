# Lorekeep

<p align="center"><img src="cover.jpeg" alt="Lorekeep" /></p>

**A temporal knowledge graph for AI agents, served read-only over MCP.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Lorekeep compiles a team's raw documentation into a versioned, time-aware
knowledge graph (`facts.jsonl`) and exposes it to coding agents (Claude Code,
Cursor, Codex) through the Model Context Protocol — with per-namespace
permission and zero servers to run.

It applies Andrej Karpathy's "LLM Knowledge Base" idea: raw docs are the
**source code**, the compiled graph is the **executable**. Knowledge is
processed once at compile time, not re-RAG'd on every query.

---

## Why

Existing tools each miss part of what a team needs:

| | file-based | temporal KG | compile step | team permission | MCP |
|---|---|---|---|---|---|
| Obsidian + MCP | ✅ | ❌ | ❌ | ❌ | ✅ |
| mcp-knowledge-graph | ✅ | ❌ | ❌ | ❌ (local) | ✅ |
| mem0 / cognee | ❌ (DB) | partial | ❌ | partial (DB) | ✅ |

Lorekeep targets the gap: **strictly file-based + temporal graph + compile-once +
namespace-scoped permission + MCP** — for team-level (not just single-user)
knowledge.

## Features

- **Compile-only** — a curator (human + LLM) builds the graph; agents only read.
  No write path, no concurrency hell, deterministic output.
- **File-sovereign** — `facts.jsonl` (one fact per line, sorted) is the single
  source of truth and the sync unit (git or S3). No binary store committed.
- **Temporal** — every fact carries `valid_from`/`valid_to` (half-open
  `[from, to)`); query "what was true at *T*", history, diffs.
- **Namespace permission** — facts are tagged `ns` from the directory tree
  (`raw/<ns>/`); agents scoped to namespaces; cross-namespace edges
  hidden unless both endpoints are visible. Deny-by-default.
- **MCP, stdio-first** — `lorekeep serve` exposes 8 read-only tools; `lorekeep mcp add`
  wires Claude Code / Cursor / Codex. No server process to babysit.
- **Lazy-reload** — `lorekeep compile` updates the graph; the MCP server
  auto-refreshes on the next query. Connect once, use forever.
- **Provider-pluggable extraction** — litellm (OpenAI / Anthropic /
  DashScope/Qwen / Ollama). Strict-privacy → Ollama, fully local.
- **Tier-1 eval** — extraction P/R/F1 vs a gold corpus, entity-resolution F1,
  graph-structure metrics, determinism property tests.

## Install

```bash
# from PyPI:
uvx lorekeep init                 # try it without installing

# or from a clone:
git clone https://github.com/manhhailua/lorekeep && cd lorekeep
uv tool install .                 # installs the `lorekeep` command
```

## Quickstart

```bash
# 1. bootstrap a data home (~/.config/lorekeep + ~/.local/share/lorekeep)
uvx lorekeep init

# 2. add docs under the data home's raw/<namespace>/
mkdir -p ~/.local/share/lorekeep/raw/backend
cp your-docs.md ~/.local/share/lorekeep/raw/backend/

# 3. set a provider (edit ~/.config/lorekeep/config.yaml), then compile
uvx lorekeep compile                # raw/*.md -> graph/facts.jsonl

# 4. wire a coding agent (writes a portable .mcp.json)
uvx lorekeep mcp add --agent claude --ns backend

# 5. verify
uvx lorekeep doctor
```

Restart Claude Code → the 8 Lorekeep tools are available, scoped to your namespace.

## How it works

```
                       COMPILE (offline, curator)                     SYNC
raw/<ns>/*.md ──► ingest ──► extract(LLM) ──► resolve ──► writer ──► facts.jsonl
                                                                            │
                                          ┌─────────────────────────────────┘
                                          ▼  (git pull / aws s3 sync)
                    SERVE + QUERY (runtime, per device)
facts.jsonl ──load──► GraphStore (networkx, temporal) ──► ScopedGraph (ns) ──► MCP ──► agent
                              ▲                                  │
                              └── lazy-reload on mtime change ◄──┘
```

**Pipeline** (`ingest → extract → resolve → writer`): markdown is chunked with
provenance; an LLM extracts schema-constrained nodes/edges with temporal +
namespace tags; aliases collapse to canonical entities; a deterministic writer
emits sorted, byte-stable `facts.jsonl` + a `manifest.json` (provenance +
errors + quarantine). Re-compiling unchanged input is byte-identical (per-chunk
hash cache), so git diffs stay clean.

**Serve**: `GraphStore` loads `facts.jsonl` into a networkx graph with temporal
queries. `ScopedGraph` is the single permission chokepoint — every query is
filtered through strict visibility rules. The FastMCP server is a thin layer of
read-only tools over `ScopedGraph`. It lazy-reloads when `facts.jsonl` changes,
so `compile` is instantly visible without reconnecting.

## Concepts

**fact** — one line of `facts.jsonl`, a `node` or `edge`:
```jsonl
{"kind":"node","id":"svc:payments","type":"service","ns":["backend"],"valid_from":"2024-01-15","valid_to":null,"props":{"lang":"go"},"src":["raw/backend/payments.md:12"]}
{"kind":"edge","id":"e_depends_on_0001","type":"depends_on","from":"svc:payments","to":"svc:auth","ns":["backend"],"valid_from":"2024-01-15","valid_to":"2025-03-01","props":{},"src":["...:20"]}
```
- `ns` — namespace set; `["public"]` is globally visible.
- `valid_to: null` ⇒ current. History = multiple edges, same endpoints, different windows.
- `src` — provenance to raw doc line (audit, incremental re-compile, agent citations).

**Permission** — effective_ns = allowed ∪ {public}. Node visible iff
`ns ∩ effective_ns ≠ ∅`. Edge visible iff **both** endpoints visible **and**
`edge.ns ∩ effective_ns ≠ ∅`. Deny-by-default; an edge never reveals a
neighbor the caller can't see.

**Temporal queries** — `at_time(T)` (snapshot of facts valid at T, half-open
`[from,to)`), `history(id)` (versions of an entity), `changes(t1,t2)` (edges
that began/ended in the window).

## MCP tools (read-only, scoped)

`search` · `get_node` · `neighbors` · `at_time` · `history` · `changes` ·
`list_namespaces` · `schema`. Every result is filtered to the caller's
namespace.

## Configuration

`config.yaml` (resolved by precedence: explicit `LOREKEEP_*` env > `LOREKEEP_HOME` >
dev marker > XDG):
```yaml
provider:
  model: openai/qwen-plus                              # litellm model string
  api_base: https://dashscope-intl.aliyuncs.com/compatible-mode/v1
  api_key_env: DASHSCOPE_API_KEY                       # env var name (preferred)
  api_key: null                                        # or inline (gitignored config only)
ns:
  default: [public]
install_source: pypi                                   # pypi = portable .mcp.json
```
API keys never live in committed files — use `api_key_env` (env) or inline
`api_key` in the gitignored config only. Examples (DashScope / OpenAI / Ollama)
in [`.lorekeep/config.yaml.example`](.lorekeep/config.yaml.example).

## Data home & dev mode

Path resolution (high → low): explicit `LOREKEEP_*` env → `LOREKEEP_HOME` →
**dev mode** (`.lorekeep/` or `raw/` in CWD; auto-detected in a source checkout)
→ XDG (`~/.config/lorekeep`, `~/.local/share/lorekeep`).

Full details, per-path overrides, and `lorekeep init`: [`docs/guides/data-home.md`](docs/guides/data-home.md).
For usage, see the [`docs/`](docs/README.md) index.

## Evaluation

Tier-1 (CI): extraction P/R/F1 vs a gold corpus, entity-resolution pairwise F1,
graph-structure metrics, determinism. Run: `uvx lorekeep eval`. The north star is
*systematic thinking with complete information* — memory-recall benchmarks
(LoCoMo, LongMemEval) are parity checks, not the optimization target. See
[`docs/architecture/evaluation.md`](docs/architecture/evaluation.md).

## Project layout

```
src/lorekeep/
  models.py            shared contract (Node/Edge/Schema/Manifest)
  facts_io.py          facts.jsonl loader (store + eval)
  paths.py             4-tier path resolution (env/home/dev/XDG)
  defaults.py          default schema + config (for `init`)
  config.py, schema_io.py
  compile/{ingest,extract,resolve,writer}.py    the compile pipeline
  compile/providers.py                          LLMProvider (Fake/LiteLLM)
  store/{graph,fts}.py                          GraphStore + optional FTS cache
  perm/ns.py                                    ScopedGraph permission chokepoint
  mcp_server.py                                 FastMCP + 8 read tools (lazy-reload)
  integrations/{claude_code,cursor,codex,common}.py
  pipeline.py, cli.py
  eval/{gold,construction,retrieval}.py
tests/                 ~106 tests
docs/                  README.md index, architecture/, guides/
```

## Status

**v1** — compile pipeline + serve (store/permission/MCP/integrations) + data-home
+ dev mode + lazy-reload, all merged to `main`, 114 tests green. Published to
PyPI as `lorekeep`.

Roadmap (phase 2+): streamable-HTTP team server, OIDC/SSO,
embeddings/hybrid search, `wiki.md` views, full Tier-2 benchmark datasets
(HotpotQA/CronQuestions) and the bespoke Tier-3 Lorekeep-Reason eval.

## Documentation

The [`docs/`](docs/README.md) index is the entry point.

**Guides**
- [Importing agent sessions](docs/guides/import.md)
- [Compiling the knowledge graph](docs/guides/compile.md)
- [Serving the graph to coding agents](docs/guides/serve.md)
- [Data home & path resolution](docs/guides/data-home.md)

**Architecture**
- [Overview](docs/architecture/overview.md) · [Data model](docs/architecture/data-model.md) · [Permission](docs/architecture/permission.md) · [Temporal](docs/architecture/temporal.md) · [Compile pipeline](docs/architecture/pipeline.md) · [Serve & MCP](docs/architecture/serve-mcp.md) · [Testing & evaluation](docs/architecture/evaluation.md)

## License

Lorekeep is released under the **MIT License** — see [`LICENSE`](LICENSE).

Copyright © 2026 Manh Pham. You're free to use, copy, modify, merge, publish,
distribute, sublicense, and/or sell copies of the software, provided the
copyright and permission notice are included in all copies. The software is
provided "as is", without warranty of any kind.
