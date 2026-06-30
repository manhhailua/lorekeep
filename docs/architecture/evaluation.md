# Testing & evaluation

> Adapted from the original design spec.

## Testing strategy

- **Unit per component:** ingest parsing, extract with a mock LLM (`FakeProvider`, canned responses), resolve dedup logic, store temporal/traversal queries, permission filtering.
- **Property tests:**
  - *Determinism* — same input ⇒ byte-identical `facts.jsonl`.
  - *ns-closure* — no query ever returns a fact outside the caller's `allowed_ns` (including edge-endpoint leakage).
  - *temporal-validity* — `at_time` only returns facts whose window contains `t`.
- **Integration:** small raw fixture → compile → serve → MCP tool calls assert filtered results and provenance.
- **Golden tests:** snapshot `facts.jsonl` for a fixture corpus; diffs catch regressions.
- Compile-only ⇒ no concurrency to test.

All compile/serve/import tests inject `FakeProvider` via monkeypatch to avoid a real LLM — no API key required.

## Evaluation

**North star = systematic thinking with complete information.** Memory-recall benchmarks are parity checks, not the objective. Evaluation measures five properties, with *reasoning support* as the headline metric:

| Metric family | What it measures | Tier |
|---|---|---|
| **Completeness** | Salient facts captured from raw (coverage); nothing missing in scope | 1 |
| **Coherence** | No contradictions, no duplicate-entity leaks, consistent graph | 1 |
| **Temporal correctness** | `at_time` / `history` / `changes` return correct facts | 1+2 |
| **Retrievability** | Agent finds the facts a question needs (multi-hop, temporal QA) | 2 |
| **Reasoning support** ⭐ | Systematic-reasoning answer quality vs baseline RAG / no-KB | 3 |

### Tier 1 — Construction quality (CI, every commit)

Evaluates the **compiler**, not the agent. `lorekeep eval` / `lorekeep check`.

- Extraction P/R/F1 vs a **gold-annotated corpus** (human-authored `facts.jsonl` reference), per node/edge type.
- Entity-resolution F1 + false-merge rate.
- Graph structure: coverage of salient facts, average degree, dangling-edge rate, contradiction rate.
- Determinism: byte-identical re-compile of unchanged input.
- Datasets: a small in-repo **gold corpus** of team-doc-style fixtures (`tests/fixtures/gold/`).

### Tier 2 — Retrieval + temporal QA (per release)

Evaluates whether the **query path** returns correct facts.

- **LoCoMo** (shipped): 10 very long-term conversations (300 turns, 35 sessions).
  Converts conversations → `raw/*.md` → compile → graph → programmatic retrieval QA.
  5 categories: single-hop, temporal, multi-hop, descriptive, adversarial.
  Token-level F1 scoring (HotpotQA-style). Adversarial scored on abstention
  (system should NOT find supporting evidence for plausible-but-wrong answer).
  Run: `lorekeep eval-locomo --data locomo10.json --compile`.
- HotpotQA / 2WikiMultihopQA / MuSiQue (planned): multi-hop QA with agent-LLM loop.
- CronQuestions / TimeQuestions (planned): temporal KG QA — measures `at_time` /
  `history` / `changes`. Industry weak spot (specialized memory systems drop to
  ~20% on temporal reasoning per Atlas). Lorekeep's core bet.
- LongMemEval (planned): 5 abilities — extraction, multi-session reasoning,
  temporal reasoning, knowledge updates, abstention.

### Tier 3 — Systematic-thinking reasoning eval (north star)

The actual goal. No off-the-shelf benchmark fits team-doc systematic reasoning, so it is built bespoke.

- **Lorekeep-Reason**: curated team-doc corpora + multi-step reasoning tasks (e.g. "trace the blast radius of deprecating service X across teams and time", "reconstruct the decision history and current state of ADR-Y") + reference answers + rubric.
- Method: a coding agent solves each task under three conditions — (a) with Lorekeep, (b) with raw-doc RAG baseline, (c) with no knowledge base.
- Metrics: **LLM-judge rubric** (completeness, correctness, temporal accuracy, provenance use, reasoning coherence) + objective sub-questions; multiple judges to control variance.
- v1 ships a minimal harness + seed tasks; the dataset grows incrementally.

### Notes

- Memory benchmarks are recall-oriented; chasing their scores mis-optimizes away from the north star. They stay parity checks.
- Bespoke gold corpus + Lorekeep-Reason cost real annotation effort — start small, grow incrementally.
- LLM-judge variance → rubric + multi-judge + calibration against human labels on a subset.

## Scope

### v1 IN

ingest + extract + resolve + writer + `facts.jsonl`/manifest/schema + store (networkx, temporal) + permission + optional FTS cache + MCP (read + write tools, stdio) + `mcp add` for Claude Code/Cursor/Codex + `doctor` + `init` + `import` + journal (append-only pending) + agent daemon (watch + compile + resolve + lint) + docs + tests + **Tier-1 construction eval (CI)** + **Tier-2 retrieval/temporal-QA smoke**.

### v1 OUT (phase 2+)

`wiki.md` views, Parquet/DuckDB at scale, streamable-HTTP team server, OIDC/SSO, ingest connectors (Confluence/PDF/URL), embeddings/hybrid search, **full Lorekeep-Reason dataset (Tier-3 scaling)**.
