# Lorekeep

<p align="center"><img src="cover.jpeg" alt="Lorekeep" /></p>

**A temporal knowledge graph for AI agents, over MCP — agents read at query time, contribute at compile time (runtime write planned for phase 2).**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Lorekeep compiles a team's raw documentation into a versioned, time-aware
knowledge graph (`facts.jsonl`) and exposes it to coding agents (Claude Code,
Cursor, Codex) through the Model Context Protocol — with per-namespace
permission and zero servers to run.

It applies Andrej Karpathy's "LLM Knowledge Base" idea: raw docs are the
**source code**, the compiled graph is the **executable**. Knowledge is
processed once at compile time, not re-RAG'd per query — and agent
conversations continuously enrich the graph through append-only journals.

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

- **Append-and-resolve** — three write paths (raw/ compile, agent ingest,
  import sessions) converge into one resolve step. Journals are append-only;
  resolve is pure logic, zero LLM cost.
- **Agent-driven knowledge** [planned] — agents propose facts at runtime via MCP write
  tools at **zero marginal LLM cost**. Confidence-gated: high-confidence
  auto-merge, low-confidence quarantine.
- **File-sovereign** — `facts.jsonl` (one fact per line, sorted) is the single
  source of truth and the sync unit (git or S3). No binary store committed.
- **Temporal** — every fact carries `valid_from`/`valid_to` (half-open
  `[from, to)`); query "what was true at *T*", history, diffs.
- **Namespace permission** — facts are tagged `ns` from the directory tree
  (`raw/<ns>/`); agents scoped to namespaces; cross-namespace edges
  hidden unless both endpoints are visible. Deny-by-default.
- **MCP, stdio-first** — `lorekeep serve` exposes 8 read tools (5 write tools planned);
  `lorekeep mcp add` wires Claude Code / Cursor / Codex.
- **Autonomous agent daemon** — `lorekeep agent watch` keeps the graph current:
  auto-compile on raw/ change, auto-resolve pending journals, delta import of
  agent session memory. Runs in the background; MCP server lazy-reloads.
- **Lazy-reload** — graph updates (compile or resolve) are visible on the next
  query. Connect once, use forever.
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

Restart Claude Code → 8 Lorekeep read tools are available, scoped to your namespace.

## Lifecycle

The full journey from install to continuous use — see the
[Getting started guide](docs/guides/getting-started.md) for details.

```
 INIT          CURATE              SERVE              KEEP CURRENT          SYNC
 ════          ═══════             ══════             ════════════          ════
 ┌─────┐   ┌──────────┐        ┌─────────┐       ┌───────────────┐    ┌────────┐
 │init │──►│raw/*.md  │──►     │mcp add  │──►    │ agent watch   │──► │backup  │
 │     │   │compile   │ compile│serve    │ serve │  raw/  → compile│   │        │
 │     │   │          │────────│         │       │  pending/ → resolve   │
 └─────┘   └──────────┘        └─────────┘       │  memory/ → import │    └────────┘
                                   ▲             └───────┬───────┘         │
                                   │                     │ lazy-reload     │ git sync
                                   │◄────────────────────┘                 │
                                   │◄──────────────────────────────────────┘
```

| Step | Command | What it does |
|---|---|---|
| 1. Bootstrap | `lorekeep init` | Create data home (config + schema + dirs) |
| 2. Curate | `raw/<ns>/*.md` | Drop markdown docs under namespace dirs |
| 3. Compile | `lorekeep compile` | LLM-extract facts → `facts.jsonl` (cached, deterministic) |
| 4. Wire agent | `lorekeep mcp add --agent claude --ns <ns>` | Write `.mcp.json`, scoped to namespace |
| 5. Verify | `lorekeep doctor` | Graph loads, schema valid, tool responds |
| 6. Serve | `lorekeep serve` | MCP server (read-only, 8 tools, lazy-reload) |
| 7. Keep current | `lorekeep agent watch &` | Daemon: auto-compile, auto-resolve, delta-import sessions |
| 8. Back up | `lorekeep backup` | Push data home to private git repo (raw/ + schema.json) |

Steps 1–6 are one-time setup. Step 7 runs in the background for continuous
updates. Step 8 syncs across machines.

## How it works

```
               THREE WRITE PATHS                            SYNC
               ════════════════
raw/<ns>/*.md ──► ingest ──► extract(LLM) ──┐
                                            │
agent propose ──► MCP write tools ──► ──────┤  [planned phase 2]
  (ZERO LLM cost, journal append)           │
                                            ├──► resolve ──► writer ──► facts.jsonl
import ──► raw/ ──► compile ────────────────┘    (pure logic,          │
                                                   ZERO LLM)            │
                                                        ┌───────────────┘
                                                        ▼ (git / S3 sync)
               SERVE + QUERY (runtime, per device)
facts.jsonl ──load──► GraphStore ──► ScopedGraph(ns) ──► MCP ──► agent
                         ▲              ▲                      │
                         │              │         ◄── read queries
                          │              └────────── write proposals (journal) [planned]
                         └── lazy-reload on mtime change

               AUTONOMOUS AGENT DAEMON
               lorekeep agent watch:
                 ├── watch raw/ → auto-compile
                 ├── watch pending/ → auto-resolve
                 └── watch Claude memory/ → delta import → raw/
```

**Three write paths → one resolve**: markdown is compiled by an LLM (chunked + cached); agents will propose facts at runtime through MCP write tools at **zero marginal LLM cost** (the agent already ran the LLM for the conversation) — **planned for phase 2**; agent sessions are imported into raw/. All converge at `resolve` — pure Python logic that merges, deduplicates, validates, and writes byte-stable `facts.jsonl`.

**Serve**: `GraphStore` loads `facts.jsonl` into a networkx graph with temporal
queries. `ScopedGraph` is the single permission chokepoint — every query is
filtered through strict visibility rules. The FastMCP server exposes 8 read tools
(5 write tools planned for phase 2) over `ScopedGraph`. It lazy-reloads when
`facts.jsonl` changes, so `compile` is instantly visible without reconnecting.

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

**Agent-driven knowledge** [planned] — agents will propose facts at runtime through MCP write tools (zero LLM cost). Facts land in `pending/<ns>/journal.jsonl` with agent id, confidence score, and timestamp. Resolve merges them into the graph: high-confidence (≥0.8) auto-merge, medium (0.5-0.8) merge + flag, low (<0.5) quarantine.

**Autonomous agent daemon** — `lorekeep agent watch` keeps the graph current: watches `raw/` for changes → auto-compile; monitors `pending/` → auto-resolve; delta-imports Claude session memory into `raw/`. Scheduled lint and weekly suggestions are planned. See [docs/architecture/agent.md](docs/architecture/agent.md).

## MCP tools (8 read, scoped; 5 write planned)

**Read:** `search` · `get_node` · `neighbors` · `at_time` · `history` · `changes` · `list_namespaces` · `schema`.

**Write** (journal-based, zero LLM cost, planned phase 2): `propose_fact` · `link_facts` · `flag_contradiction` · `update_fact` · `suggest_improvement`.

Every result is filtered to the caller's namespace. Write tools will append to `pending/` journals; facts enter the graph on the next resolve pass.

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
**dev mode** (`.lorekeep/` in CWD; auto-detected in a source checkout)
→ XDG (`~/.config/lorekeep`, `~/.local/share/lorekeep`).

Back up the data home to a private git repo with `lorekeep backup` — see
[`docs/guides/backup.md`](docs/guides/backup.md).

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
  journal.py           append-only journal writer + loader
  agent.py             autonomous agent: ingest, lint, suggest, status, watch
  store/{graph,fts}.py                          GraphStore + optional FTS cache
  perm/ns.py                                    ScopedGraph permission chokepoint
  mcp_server.py                                 FastMCP + 8 read tools (5 write planned)
  integrations/{claude_code,cursor,codex,common}.py
  pipeline.py, cli.py
  eval/{gold,construction,retrieval}.py
tests/                 ~140 tests
docs/                  README.md index, architecture/, guides/
```

## Status

**v1 (implemented)** — compile pipeline + serve (store/permission/MCP read/integrations) + import + agent daemon (watch/ingest/lint/suggest/status) + journal + resolve + data-home + dev mode + lazy-reload + backup + eval. Published to PyPI as `lorekeep`.

**Phase 2 (planned)** — MCP write tools (runtime fact proposals via `propose_fact`, `link_facts`, etc.) + `wiki.md` views (Obsidian-compatible markdown output), streamable-HTTP team server, OIDC/SSO, embeddings/hybrid search, scheduled nightly lint/suggest in daemon, schema evolve, full Tier-2 benchmark datasets (HotpotQA/CronQuestions) and the bespoke Tier-3 Lorekeep-Reason eval.

## Documentation

The [`docs/`](docs/README.md) index is the entry point.

**Guides**
- [Getting started](docs/guides/getting-started.md)
- [Importing agent sessions](docs/guides/import.md)
- [Compiling the knowledge graph](docs/guides/compile.md)
- [Serving the graph to coding agents](docs/guides/serve.md)
- [Data home & path resolution](docs/guides/data-home.md)
- [Backing up the data home](docs/guides/backup.md)

**Architecture**
- [Overview](docs/architecture/overview.md) · [Data model](docs/architecture/data-model.md) · [Pipeline](docs/architecture/pipeline.md) · [Journal](docs/architecture/journal.md) · [Agent](docs/architecture/agent.md) · [Permission](docs/architecture/permission.md) · [Temporal](docs/architecture/temporal.md) · [Serve & MCP](docs/architecture/serve-mcp.md) · [Testing & evaluation](docs/architecture/evaluation.md)

## License

Lorekeep is released under the **MIT License** — see [`LICENSE`](LICENSE).

Copyright © 2026 Manh Pham. You're free to use, copy, modify, merge, publish,
distribute, sublicense, and/or sell copies of the software, provided the
copyright and permission notice are included in all copies. The software is
provided "as is", without warranty of any kind.
