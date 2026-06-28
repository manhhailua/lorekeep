# Architecture overview

> Adapted from the original design spec.

Lorekeep builds a **living temporal knowledge graph** that coding agents both **read and contribute to** — served over the **Model Context Protocol (MCP)**. It applies Andrej Karpathy's "LLM Knowledge Base" idea — treat raw docs as source code and the compiled graph as the executable — with three additions the open-source landscape does not provide together:

1. **Agent-driven knowledge accumulation** — agents propose facts during conversation at zero marginal LLM cost.
2. **Strictly file-based storage** (`facts.jsonl` + `pending/` journals), for privacy and portability.
3. **Namespace-scoped permission**, for team-level use rather than a single local user.

The system has two phases: **compile** (offline, curator-side) and **serve** (runtime, per device). Raw docs are compiled offline; agents propose facts at runtime through journal-based write tools; a periodic resolve pass merges all sources into `facts.jsonl` without additional LLM calls. This keeps the graph continuously up-to-date while strictly controlling API cost.

## North star

Lorekeep exists to let an agent reason about a domain **systematically and with complete information** — not to maximize memory-recall benchmark scores. Memory benchmarks (LoCoMo, LongMemEval) are parity checks, not the objective. The real measures are completeness, coherence, temporal correctness, and reasoning support (see [evaluation](evaluation.md)).

## Architecture

```
                    KNOWLEDGE SOURCES (three write paths)
                    ═══════════════════════════════════════

  1. raw/<ns>/*.md ──► ingest ──► extract(LLM) ──┐
     (curator docs, provenance-rich)              │
                                                  │
  2. Agent propose ──► MCP write tools ──► ───────┤
     (runtime, ZERO LLM cost)                     │
     propose_fact / link_facts / flag_contradiction│
                                                  │
  3. Import sessions ──► import --from claude ──►─┘
     (agent history → raw/ → compile)              │
                                                   │
                    ┌──────────────────────────────┘
                    ▼
            pending/<ns>/journal.jsonl          append-only, per-namespace
            pending/<agent>/journal.jsonl       append-only, per-agent
                    │
                    ▼ (trigger: N writes OR T minutes)
    ┌───────────────────────────────────────────┐
    │           RESOLVE  (pure logic, ZERO LLM)  │
    │  load facts.jsonl + all journals           │
    │  dedup → merge → sort → validate           │
    │  priority: raw/ > import > agent-propose   │
    │  atomic os.replace                        │
    └───────────────┬───────────────────────────┘
                    │
                    ▼
              facts.jsonl                       THE store (sorted, byte-stable)
                    │
                    ▼  (git / S3 sync)
    ┌───────────────────────────────────────────┐
    │        SERVE + QUERY (runtime, per device) │
    │  facts.jsonl ──load──► GraphStore          │
    │  (networkx DiGraph, temporal)              │
    │       ──► ScopedGraph (ns filter)          │
    │            ──► MCP tools ──► agent          │
    │            ◄── MCP write tools (journal)    │
    └───────────────────────────────────────────┘

                    AUTONOMOUS AGENT (daemon)
                    ═════════════════════════════════════════

    lorekeep agent watch:
      ├── watch raw/ → auto-compile on change
      ├── nightly lint → semantic health check
      ├── periodic resolve → merge pending journals
      ├── periodic import → agent sessions → raw/
      └── suggest → propose improvements, gaps
```

The system has three write paths feeding into a single resolve step, then a read+write serve layer. The compile chain (`ingest → extract`), journals (`agent propose`), and import chain (`import → raw/`) all converge at `resolve`. The serve chain (`store → perm → mcp_server`) handles both read queries and write proposals.

## Key decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | **Append-and-resolve** | Three write paths converge into one resolve; no concurrent write conflicts because journals are append-only per-agent. |
| D2 | **Agent propose = zero LLM cost** | Coding agents already run LLM to synthesize answers. Proposing a fact is just formatting existing output — no additional LLM call. |
| D3 | **Resolve = pure logic** | Dedup, merge, sort, validate — all Python, zero LLM. Periodic batch (N writes or T minutes) so facts.jsonl is always current. |
| D4 | Per-namespace coarse permission | Derives from directory tree; file-native; maps to filesystem/git. |
| D5 | Python + FastMCP | Richest LLM/markdown/MCP ecosystem; compile-heavy logic favors Python. |
| D6 | Mid-org target (≈5k facts, 5–15 teams) | Karpathy sweet-spot; FTS/grep sufficient, no embeddings needed yet. |
| D7 | Temporal knowledge graph | Facts carry `valid_from`/`valid_to`; supports "what was true at T", history, diffs. |
| D8 | `facts.jsonl` as store + sync unit | Plain text, line-based git diffs, S3-streamable; no binary store committed. |
| D9 | Query via networkx in-memory; optional local FTS cache | Store is the sync unit; no rebuild-on-sync; cache is local-only, derived. |
| D10 | stdio-first transport | Every coding agent spawns the local server reading the repo's `facts.jsonl`; zero servers, max privacy. |
| D11 | Extract LLM pluggable, default API, ollama option | Quality by default; data leaves only at compile time; ollama for strict privacy. |

## Background

**Karpathy "LLM Knowledge Bases"** frames raw research/docs as **source code** and a compiled, structured wiki as the **executable**. Knowledge is processed at compile time, not re-processed per query. The key insight extension: the coding agent itself is already an LLM — its conversation output is knowledge that should accumulate, not disappear into chat history. Lorekeep captures this by letting agents propose facts at zero marginal cost, merging them into the graph via periodic resolve.

**OSS gap:**

| Requirement | file-based | temporal KG | agent-driven write | team permission | MCP |
|---|---|---|---|---|---|
| Obsidian + MCP | ✅ | ❌ | ❌ | ❌ | ✅ |
| mcp-knowledge-graph | ✅ | ❌ | ❌ | ❌ (local) | ✅ |
| mem0 / cognee | ❌ (DB) | partial | ❌ | partial (DB) | ✅ |

No existing project covers *strictly-file + temporal graph + agent-driven write + namespace permission + MCP*. Lorekeep fills that intersection.

## Tech stack

Python 3.11+ · FastMCP · networkx · pydantic (fact/schema models) · pyyaml (config) · mistune (markdown) · sqlite3 FTS5 (stdlib) · litellm (provider abstraction: OpenAI / Anthropic / DashScope/Qwen / Ollama) · uv for packaging/publish (`uvx lorekeep`).

## Open questions / risks

- **Extraction quality vs privacy (D11):** the default API provider sees raw docs at compile time. Acceptable because compile is curator-run and the stored artifact is pure-file; strict-privacy deployments switch to Ollama (lower quality, GPU cost).
- **Entity resolution correctness:** alias → canonical id merging is the riskiest compile logic; backed by strong fixtures and a quarantine/review path.
- **Cross-namespace edge UX:** the strict endpoint rule hides cross-team edges unless both sides are allowed; the `public` namespace mitigates.
- **Determinism vs LLM non-determinism:** LLM extraction is variable; determinism comes from per-chunk hash caching of extraction output, not re-running the LLM. See [pipeline](pipeline.md).
- **Agent trust:** agent-proposed facts enter `pending/` not `facts.jsonl` directly; confidence scoring + resolve priority + quarantine path limit risk. See [journal](journal.md).

## References

- Karpathy "LLM Knowledge Bases" — compiler analogy (source code vs executable).
- `mcp-knowledge-graph` (Anthropic reference), `mem0`, `cognee`, **Zep (temporal KG)** — landscape comparison.
- MCP specification — tool/resource model, stdio and streamable-HTTP transports.

## Next

- [Data model](data-model.md) — `facts.jsonl` format, journal format, schema, repository layout.
- [Pipeline](pipeline.md) — three write paths → resolve → facts.jsonl.
- [Journal](journal.md) — agent-driven knowledge accumulation (append-only, pending, resolve).
- [Agent](agent.md) — autonomous agent trigger model, operations, and scheduling.
- [Permission model](permission.md) — namespace visibility rules.
- [Temporal model](temporal.md) — time-aware queries.
- [Serve & MCP](serve-mcp.md) — the read + write query layer.
- [Testing & evaluation](evaluation.md) — the three-tier eval strategy.
