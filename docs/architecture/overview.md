# Architecture overview

> Adapted from the original design spec.

Lorekeep compiles a team's raw documentation into a **temporal knowledge graph** and serves it, read-only, to AI agents over the **Model Context Protocol (MCP)**. It applies Andrej Karpathy's "LLM Knowledge Base" idea — treat raw docs as source code and the compiled graph as the executable — with two additions the open-source landscape does not provide together:

1. **Strictly file-based storage** (`facts.jsonl`), for privacy and portability.
2. **Namespace-scoped permission**, for team-level use rather than a single local user.

The system is **compile-only**: a curator (human + LLM) compiles raw docs into the graph as a build step. Agents only **read**. This removes the hardest problems of graph systems — concurrency, multi-writer transactions, conflict resolution — and is what makes a file-based, permissioned, temporal graph tractable.

## North star

Lorekeep exists to let an agent reason about a domain **systematically and with complete information** — not to maximize memory-recall benchmark scores. Memory benchmarks (LoCoMo, LongMemEval) are parity checks, not the objective. The real measures are completeness, coherence, temporal correctness, and reasoning support (see [evaluation](evaluation.md)).

## Architecture

```
                COMPILE (offline, curator)                         SYNC (git / S3)
raw/<ns>/*.md ──► ingest ──► extract(LLM) ──► resolve ──► writer ──► facts.jsonl + manifest + schema
                                                                                  │
                                                            ┌─────────────────────┴─────────────────────┐
                                                            ▼ (clone/pull)                              ▼
                                               every device / coding agent                         S3 object store

                SERVE + QUERY (runtime, per device)
facts.jsonl ──load──► store (networkx DiGraph, temporal) ──► perm guard (allowed_ns) ──► MCP tools ──► agent
```

The compile chain (`ingest → extract → resolve → writer`) and serve chain (`store → perm → mcp_server`) share only `facts.jsonl` + `schema.json`. They can be developed and tested independently. This seam is why the codebase has no runtime write path.

## Key decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | Compile-only (Karpathy) | Single writer removes graph concurrency hell; simplest permission model (read-scoping). |
| D2 | Per-namespace coarse permission | Derives from directory tree; file-native; maps to filesystem/git. |
| D3 | Python + FastMCP | Richest LLM/markdown/MCP ecosystem; compile-heavy logic favors Python. |
| D4 | Mid-org target (≈5k facts, 5–15 teams) | Karpathy sweet-spot; FTS/grep sufficient, no embeddings needed yet. |
| D5 | Temporal knowledge graph | Facts carry `valid_from`/`valid_to`; supports "what was true at T", history, diffs. |
| D6 | `facts.jsonl` as store + sync unit | Plain text, line-based git diffs, S3-streamable; no binary store committed. |
| D7 | Query via networkx in-memory; optional local FTS cache | Store is the sync unit; no rebuild-on-sync; cache is local-only, derived. |
| D8 | stdio-first transport | Every coding agent spawns the local server reading the repo's `facts.jsonl`; zero servers, max privacy. |
| D9 | Coding-agent integration is v1 priority | `lorekeep mcp add` writes Claude Code / Cursor / Codex configs. |
| D10 | Extract LLM pluggable, default API, ollama option | Quality by default; data leaves only at compile time; ollama for strict privacy. |

## Background

**Karpathy "LLM Knowledge Bases"** frames raw research/docs as **source code** and a compiled, structured wiki as the **executable**. Knowledge is processed **once** at compile time, not re-processed per query, which lets mid-sized datasets skip the complexity of vector databases and RAG. Lorekeep adopts the compile step but emits a **temporal knowledge graph**.

**OSS gap:**

| Requirement | file-based | temporal KG | compile step | team permission | MCP |
|---|---|---|---|---|---|
| Obsidian + MCP | ✅ | ❌ | ❌ | ❌ | ✅ |
| mcp-knowledge-graph | ✅ | ❌ | ❌ | ❌ (local) | ✅ |
| mem0 / cognee | ❌ (DB) | partial | ❌ | partial (DB) | ✅ |

No existing project covers *strictly-file + temporal graph + compile step + namespace permission + MCP*. Lorekeep fills that intersection.

## Tech stack

Python 3.11+ · FastMCP · networkx · pydantic (fact/schema models) · pyyaml (config) · mistune (markdown) · sqlite3 FTS5 (stdlib) · litellm (provider abstraction: OpenAI / Anthropic / DashScope/Qwen / Ollama) · uv for packaging/publish (`uvx lorekeep`).

## Open questions / risks

- **Extraction quality vs privacy (D10):** the default API provider sees raw docs at compile time. Acceptable because compile is curator-run and the stored artifact is pure-file; strict-privacy deployments switch to Ollama (lower quality, GPU cost).
- **Entity resolution correctness:** alias → canonical id merging is the riskiest compile logic; backed by strong fixtures and a quarantine/review path.
- **Cross-namespace edge UX:** the strict endpoint rule hides cross-team edges unless both sides are allowed; the `public` namespace mitigates.
- **Determinism vs LLM non-determinism:** LLM extraction is variable; determinism comes from per-chunk hash caching of extraction output, not re-running the LLM. See [pipeline](pipeline.md).

## References

- Karpathy "LLM Knowledge Bases" — compiler analogy (source code vs executable).
- `mcp-knowledge-graph` (Anthropic reference), `mem0`, `cognee`, **Zep (temporal KG)** — landscape comparison.
- MCP specification — tool/resource model, stdio and streamable-HTTP transports.

## Next

- [Data model](data-model.md) — `facts.jsonl` format, schema, repository layout.
- [Permission model](permission.md) — namespace visibility rules.
- [Temporal model](temporal.md) — time-aware queries.
- [Compile pipeline](pipeline.md) — ingest → extract → resolve → writer.
- [Serve & MCP](serve-mcp.md) — the read-only query layer.
- [Testing & evaluation](evaluation.md) — the three-tier eval strategy.
