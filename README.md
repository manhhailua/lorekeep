# Lorekeep

<p align="center"><img src="cover.jpeg" alt="Lorekeep" /></p>

**A temporal knowledge graph for AI agents, over MCP — agents read at query time and propose facts at runtime through journal-based write tools.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Lorekeep compiles a team's raw docs into a temporal knowledge graph (`facts.jsonl`),
serves it to coding agents (Claude Code, Cursor, Codex, opencode) over MCP, and
lets agents propose new facts at zero LLM cost. Knowledge is processed once at
compile time, not re-RAG'd per query.

---

## Why

| | file-based | temporal KG | compile step | team permission | MCP |
|---|---|---|---|---|---|
| Obsidian + MCP | ✅ | ❌ | ❌ | ❌ | ✅ |
| mcp-knowledge-graph | ✅ | ❌ | ❌ | ❌ (local) | ✅ |
| mem0 / cognee | ❌ (DB) | partial | ❌ | partial (DB) | ✅ |

No tool combines all five. Lorekeep does: **file-based + temporal graph +
compile-once + namespace permission + MCP** — for team-level knowledge, not
just single-user.

## Features

- **Append-and-resolve** — three write paths (raw/ compile, agent ingest,
  import sessions) converge into one resolve step. Journals are append-only;
  resolve is pure logic, zero LLM cost.
- **Agent-driven knowledge** — agents propose facts at runtime via MCP write
  tools at **zero marginal LLM cost**. Confidence-gated: high-confidence
  auto-merge, low-confidence quarantine.
- **File-sovereign** — `facts.jsonl` (one fact per line, sorted) is the single
  source of truth and the sync unit (git or S3). No binary store committed.
- **Temporal** — every fact carries `valid_from`/`valid_to` (half-open
  `[from, to)`); query "what was true at *T*", history, diffs.
- **Namespace permission** — facts are tagged `ns` from the directory tree
  (`raw/<ns>/`); agents scoped to namespaces; cross-namespace edges
  hidden unless both endpoints are visible. Deny-by-default.
- **MCP, stdio-first** — `lorekeep serve` exposes 9 read + 5 write tools;
  `lorekeep mcp add` wires Claude Code / Cursor / Codex / opencode.
- **Autonomous agent daemon** — `lorekeep agent watch` keeps the graph current:
  auto-compile on raw/ change, auto-resolve pending journals, delta import of
  agent session memory. Runs in the background; MCP server lazy-reloads.
- **Session-end hooks** — `lorekeep hook` auto-imports agent memory when a
  session ends (Claude / Cursor / Codex / opencode). Wired by `mcp add`.
- **Obsidian wiki** — auto-generated after every compile/resolve: human-browsable
  markdown pages with `[[wikilinks]]`, YAML frontmatter, graph view.
- **Lazy-reload** — graph updates visible on next query. Connect once, use forever.
- **Provider-pluggable extraction** — litellm (OpenAI / Anthropic /
  DashScope/Qwen / Ollama). Strict-privacy → Ollama, fully local.
- **Tier-1 eval** — extraction P/R/F1 vs a gold corpus, entity-resolution F1,
  graph-structure metrics, determinism property tests.
- **Tier-2 LoCoMo eval** — long-term conversations → graph → retrieval QA.
  F1 per category (single-hop, temporal, multi-hop, adversarial abstention).

## Install

```bash
# no install needed — uvx runs it directly:
uvx lorekeep init

# or install permanently:
uv tool install lorekeep
```

## Quickstart

```bash
# 1. bootstrap data home (config + schema + dirs + agent wiring)
uvx lorekeep init

# 2. add docs under the data home's raw/<namespace>/
mkdir -p ~/.local/share/lorekeep/raw/private
cp your-docs.md ~/.local/share/lorekeep/raw/private/

# 3. set a provider (edit ~/.config/lorekeep/config.yaml), then compile
uvx lorekeep compile                # raw/*.md → graph/facts.jsonl + wiki/

# 4. wire a coding agent (writes a portable .mcp.json)
uvx lorekeep mcp add --agent claude --ns private

# 5. verify
uvx lorekeep doctor
```

Restart Claude Code → 14 Lorekeep tools are available (9 read + 5 write), scoped to your namespace. Open `~/.local/share/lorekeep/wiki/` in Obsidian to browse the graph as a human.

## Lifecycle

The full journey from install to continuous use — see the
[Getting started guide](docs/guides/getting-started.md) for details.

```
 INIT          CURATE              SERVE              KEEP CURRENT          SYNC
 ════          ═══════             ══════             ════════════          ════
 ┌─────┐   ┌──────────┐        ┌─────────┐       ┌───────────────┐    ┌────────┐
 │init │──►│raw/*.md  │──►     │mcp add  │──►    │ agent watch   │──► │backup  │
 │     │   │compile   │ compile│serve    │ serve │  raw/    → compile   │
 │     │   │          │────────│+hook    │       │  pending/ → resolve  │
 └─────┘   └──────────┘   wiki │         │       │  memory/  → import   └────────┘
                               └─────────┘       └───────┬───────┘         │
                                    ▲                     │ lazy-reload     │ git sync
                                    │◄────────────────────┘                 │
                                    │◄──────────────────────────────────────┘
```

| Step | Command | What it does |
|---|---|---|
| 1. Bootstrap | `lorekeep init` | Create data home (config + schema + dirs) |
| 2. Curate | `raw/<ns>/*.md` | Drop markdown docs under namespace dirs |
| 3. Compile | `lorekeep compile` | LLM-extract → `facts.jsonl` + `wiki/` (cached, deterministic) |
| 4. Wire agent | `lorekeep mcp add --agent claude --ns <ns>` | Write `.mcp.json` + session-end hook, scoped to namespace |
| 5. Verify | `lorekeep doctor` | Graph loads, schema valid, tool responds |
| 6. Serve | `lorekeep serve` | MCP server (9 read + 5 write tools, lazy-reload) |
| 7. Keep current | `lorekeep agent watch &` | Daemon: auto-compile, auto-resolve, delta-import sessions |
| 8. Back up | `lorekeep backup` | Push data home to private git repo (raw/ + schema.json) |
| 9. Persist daemon | `lorekeep agent daemon install` | Survive restart (systemd/launchd/startup) |

Steps 1–6 are one-time setup. Step 7 runs in the background for continuous
updates. Step 8 syncs across machines.

## How it works

```
               THREE WRITE PATHS                            SYNC
               ════════════════
raw/<ns>/*.md ──► ingest ──► extract(LLM) ──┐
                                            │
agent propose ──► MCP write tools ──► ──────┤
  (ZERO LLM cost, journal append)           │
                                            ├──► resolve ──► writer ──► facts.jsonl ──► wiki/*.md
import ──► raw/ ──► compile ────────────────┘    (pure logic,    (sorted)       (Obsidian)
                                                   ZERO LLM)
                                                        ┌───────────────┘
                                                        ▼ (git / S3 sync)
               SERVE + QUERY (runtime, per device)
facts.jsonl ──load──► GraphStore ──► ScopedGraph(ns) ──► MCP (9 read + 5 write) ──► agent
                         ▲              ▲                      │
                          │              │         ◄── read queries
                          │              └────────── write proposals (journal)
                         └── lazy-reload on mtime change

               AUTONOMOUS AGENT DAEMON
               lorekeep agent watch:
                 ├── watch raw/ → auto-compile
                 ├── watch pending/ → auto-resolve
                 └── watch agent memory/ → delta import → raw/
```

**Three write paths → one resolve**: markdown is compiled by an LLM (chunked + cached); agents propose facts at runtime through MCP write tools at **zero marginal LLM cost** (the agent already ran the LLM for the conversation); agent sessions are imported into raw/. All converge at `resolve` — pure Python logic that merges, deduplicates, validates, and writes byte-stable `facts.jsonl`.

**Serve**: `GraphStore` loads `facts.jsonl` into a networkx graph with temporal
queries. `ScopedGraph` is the single permission chokepoint — every query is
filtered through strict visibility rules. The FastMCP server exposes 9 read
+ 5 write tools over `ScopedGraph`. It lazy-reloads when
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

**Agent-driven knowledge** — agents propose facts at runtime through MCP write tools (zero LLM cost). Facts land in `pending/<ns>/journal.jsonl` with agent id, confidence score, and timestamp. Resolve merges them into the graph: high-confidence (≥0.8) auto-merge, medium (0.5-0.8) merge + flag, low (<0.5) quarantine.

**Autonomous agent daemon** — `lorekeep agent watch` keeps the graph current: watches `raw/` for changes → auto-compile; monitors `pending/` → auto-resolve; delta-imports agent session memory (Claude / Cursor / Codex / opencode) into `raw/`. Session-end hooks auto-trigger `lorekeep hook` when the agent exits. Scheduled lint and weekly suggestions are planned. See [docs/architecture/agent.md](docs/architecture/agent.md).

## MCP tools (9 read + 5 write, scoped)

**Read:** `search` · `get_node` · `neighbors` · `at_time` · `history` · `changes` · `list_namespaces` · `schema` · `meta`.

**Write** (journal-based, zero LLM cost): `propose_fact` · `link_facts` · `flag_contradiction` · `update_fact` · `suggest_improvement`.

Every result is filtered to the caller's namespace. Write tools append to `pending/` journals; facts enter the graph on the next resolve pass.

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

**Tier-1** (CI): extraction P/R/F1 vs a gold corpus, entity-resolution pairwise F1,
graph-structure metrics, determinism. Run: `uvx lorekeep eval`.

**Tier-2 LoCoMo** (per-release): long-term conversation → graph → retrieval QA.
Run: `uvx lorekeep eval-locomo --data locomo10.json --compile`.

| Category | Recall | Description |
|---|---|---|
| Temporal | 0.71 | "When did X happen?" |
| Single-hop | 0.58 | "What is X's Y?" |
| Multi-hop | 0.49 | "Who did X's sister work with?" |
| Descriptive | 0.73 | "What did X do?" |
| Adversarial | 0.85 | Abstention — plausible-but-wrong answer not found |
| **Overall** | **0.71** | 199 QA, 1 conversation, DeepSeek extraction |

*Graph-guided retrieval: keyword search → get_node → neighbors(depth=1-2) →
source markdown enrichment. No agent-LLM synthesis (programmatic baseline).*

The north star is *systematic thinking with complete information* — memory-recall
benchmarks (LoCoMo, LongMemEval) are parity checks, not the optimization target.
See [`docs/architecture/evaluation.md`](docs/architecture/evaluation.md).

## Configuration

After `init`, adjust settings without editing YAML:

```bash
lorekeep config show                                    # print current config
lorekeep config set provider.model deepseek/deepseek-chat
lorekeep config set provider.api_key_env DEEPSEEK_API_KEY
lorekeep config set ns.default backend,frontend
lorekeep config set observability.provider langfuse     # optional tracing
```

Observability (optional): set `observability.provider` to `langfuse` or
`langsmith` for LLM call tracing via litellm callbacks.

## Project layout

```
src/lorekeep/
  models.py            shared contract (Node/Edge/Schema/Manifest)
  facts_io.py          facts.jsonl loader (store + eval)
  paths.py             4-tier path resolution (env/home/dev/XDG)
  providers.py         litellm provider/model enumeration for init
  defaults.py          default schema + config (for `init`)
  config.py, schema_io.py
  compile/{ingest,extract,resolve,writer}.py    the compile pipeline
  compile/providers.py                          LiteLLMProvider + observability
  journal.py           append-only journal writer + loader
  agent.py             autonomous agent: ingest, lint, suggest, status, watch
  store/{graph,fts}.py                          GraphStore + FTS5 cache
  perm/ns.py                                    ScopedGraph permission chokepoint
  mcp_server.py                                 FastMCP + 9 read + 5 write tools
  wiki.py                                        Obsidian-compatible wiki generator
  importer/{claude,cursor,codex,opencode}.py    agent session → raw/ importers
  integrations/{claude_code,cursor,codex,opencode,common}.py
  pipeline.py, cli.py
  eval/{gold,construction,retrieval}.py
  eval/locomo.py                                Tier-2 LoCoMo eval
tests/                 ~410 tests
docs/                  README.md index, architecture/, guides/
```

## Status

**v1 (implemented)** — compile pipeline + serve (store/permission/MCP 9 read+5 write/4-agent integrations) + import (Claude/Cursor/Codex/opencode) + session-end hooks + agent daemon (watch/ingest/lint/suggest/status) + journal + resolve + data-home + dev mode + lazy-reload + backup + eval + scope awareness (`meta` tool) + **wiki** (Obsidian-compatible markdown output). Published to PyPI as `lorekeep`.

**Phase 2 (planned)** — streamable-HTTP team server, OIDC/SSO, embeddings/hybrid search, scheduled nightly lint/suggest in daemon, schema evolve, HotpotQA/CronQuestions/LongMemEval benchmark datasets and the bespoke Tier-3 Lorekeep-Reason eval.

## Documentation

The [`docs/`](docs/README.md) index is the entry point.

**Guides**
- [Getting started](docs/guides/getting-started.md)
- [Importing agent sessions](docs/guides/import.md)
- [Compiling the knowledge graph](docs/guides/compile.md)
- [Serving the graph to coding agents](docs/guides/serve.md)
- [Browsing the wiki](docs/guides/wiki.md)
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
