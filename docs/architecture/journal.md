# Journal: agent-driven knowledge accumulation

The journal is the mechanism by which coding agents contribute knowledge to Lorekeep **at runtime, at zero marginal LLM cost**. Agents propose facts during conversation; the journal captures them as append-only JSONL; a periodic resolve pass merges validated facts into `facts.jsonl`.

## Why journals?

Karpathy's LLM Wiki insight: the agent's conversation output IS knowledge, and it shouldn't disappear into chat history. But writing directly to `facts.jsonl` from multiple concurrent agents would create write conflicts, and low-quality facts would pollute the graph.

The journal pattern solves this:

| Problem | Journal solution |
|---|---|
| Write conflicts (multi-agent) | Append-only, per-namespace or per-agent JSONL files — no contention |
| Low-quality facts | Confidence-gated at resolve time; low-confidence → quarantine, never enters graph |
| LLM cost per write | Zero — agent already ran LLM; propose is just formatting |
| Audit trail | Every entry has `agent`, `proposed_at`, `confidence` |
| Read path isolation | Facts only visible after resolve; read path serves only validated facts |

## Journal layout

Journals use **two partitioning schemes simultaneously** — namespace-scoped for routing and agent-scoped for attribution:

```
pending/
├── backend/journal.jsonl       # facts proposed in "backend" namespace
├── frontend/journal.jsonl      # facts proposed in "frontend" namespace
├── claude/journal.jsonl         # facts proposed by Claude Code agent (any ns)
└── codex/journal.jsonl         # facts proposed by Codex agent (any ns)
```

**Write routing**: an agent in `LOREKEEP_NS=backend` calling `propose_fact` writes to `pending/backend/journal.jsonl`. The entry's `agent` field records attribution (`"claude"`).

**Resolve loading**: resolve loads ALL journals (`pending/**/journal.jsonl`) — not selectively by namespace. This ensures agent-scoped journals (`pending/claude/`) are not missed and entries from all namespaces are merged together. Selectivity happens at the read path (ScopedGraph filters by ns), not at resolve.

**Why dual partitioning?** Namespace partitions prevent write conflicts between agents in different scopes. Agent partitions provide an alternative write target when an agent needs to propose facts outside its primary namespace (curator review required). Both coexist; resolve loads them all.

Journals are git-committable — line-based diffs merge cleanly across both partition schemes.

## Journal entry format

```jsonl
{"fact":{...},"agent":"claude","ns":"backend","confidence":0.85,"proposed_at":"2026-06-20T10:30:00Z","status":"pending"}
```

See [data model](data-model.md#pending-journal-format) for the full schema.

## Lifecycle of a proposed fact

```
┌─────────────────────────────────────────────────────────────┐
│                    AGENT SESSION                             │
│  Agent discovers: "service checkout is written in Rust"      │
│  Agent calls: propose_fact({id:"svc:checkout", ...}, 0.85)  │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                    JOURNAL APPEND                            │
│  → pending/backend/journal.jsonl                            │
│  Status: "pending"                                           │
│  Atomic: write line + fsync                                  │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼  (trigger: N writes or T minutes)
┌─────────────────────────────────────────────────────────────┐
│                    RESOLVE                                   │
│  1. Load facts.jsonl + all pending journals                  │
│  2. Merge by priority (raw/ > import > agent-propose)       │
│  3. Gate by confidence:                                      │
│     ≥0.8 → auto-merge                                       │
│     0.5 to <0.8 → merge + flag for review                      │
│     <0.5 → quarantine (do not merge)                        │
│  4. Dedup, validate, sort                                    │
│  5. Write facts.jsonl (atomic os.replace)                   │
│  6. Update journal entry status: "merged" or "quarantined"  │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                    GRAPH VISIBLE                              │
│  MCP server lazy-reloads facts.jsonl                         │
│  Next search/get_node/neighbors returns the new fact         │
│  Provenance: "agent:claude:session-abc123"                   │
└─────────────────────────────────────────────────────────────┘
```

## Confidence model

Confidence is agent-estimated, not algorithmically computed. This is intentional: the agent has full conversation context and is best positioned to judge certainty. Resolve applies a simple threshold model:

### Auto-merge (confidence ≥ 0.8)

Agent made explicit claim with source citation. Examples:
- "The codebase shows `svc:checkout` is written in Rust and listens on port 8080"
- "Based on `docker-compose.yml`, checkout depends on payments and auth"

Behavior: merged into `facts.jsonl`. If `id` conflicts with existing fact from higher-priority source, agent props are merged (union), not replaced.

### Flag for review (0.5 ≤ confidence < 0.8)

Agent mentioned or implied without explicit source. Examples:
- "Based on the architecture discussion, checkout likely depends on inventory too"
- "I think the auth service was migrated from Go to Rust in Q1 2025"

Behavior: merged into `facts.jsonl`, but added to `manifest.review` for curator attention. The fact is visible to queries but flagged as potentially inaccurate.

### Quarantine (confidence < 0.5)

Agent speculation or hedging. Examples:
- "It might be the case that payments uses PostgreSQL"
- "I'm not sure, but I think..."

Behavior: **not merged** into `facts.jsonl`. Added to `manifest.quarantine` with reason. Curator can manually promote by re-submitting with higher confidence or via raw/ compile.

## Idempotent propose

Re-proposing the same fact (same agent, same id within a session) is idempotent: the journal entry is deduplicated by `(agent, fact_id, proposed_at)` within a time window. This prevents agents from flooding the journal with repeated proposals during multi-turn conversations.

## Journal cleanup

After resolve marks entries as "merged" or "quarantined", the journal can be:
- **Truncated**: remove processed entries, keep only pending
- **Archived**: move to `pending/.archive/` with timestamp
- **Left in place**: resolve skips non-pending entries on subsequent runs

Default: truncate. Archive mode available via `lorekeep resolve --archive`.

## Write tool cost analysis

| Operation | LLM calls | Why |
|---|---|---|
| Agent conversation (existing) | 1 (already running) | Agent runs LLM to answer user |
| `propose_fact` | 0 | Formats existing output into JSON |
| `link_facts` | 0 | Creates edge between known nodes |
| `flag_contradiction` | 0 | Metadata-only flag |
| Resolve (periodic) | 0 | Pure Python: dedup, merge, sort |
| **Total marginal cost** | **0** | |

The key insight: the agent **already paid** for the LLM call. Proposing a fact captures that output before it disappears. Every conversation becomes a knowledge source at no extra cost.

## Security model

### Namespace enforcement

Write tools do not accept a caller-provided `ns` parameter. The namespace is server-enforced from `LOREKEEP_NS` — stripped from `fact.ns` and replaced at journal-append time. An agent scoped to `backend` cannot inject facts into `frontend` regardless of what it puts in the fact payload. This is enforced at the MCP server layer before any journal write.

### Confidence gate hardening

Self-estimated confidence is the primary gate, but not the only one. Additional controls planned:

1. **Cross-namespace edge gate**: any `link_facts` connecting nodes in different namespaces requires curator review regardless of confidence — cross-ns edges are opt-in.
2. **New entity type gate**: introducing a fact with a `type` not yet present in the graph requires confidence ≥ 0.9 AND curator review.
3. **Contradiction flag escalation**: if `flag_contradiction` is called on a fact that was itself agent-proposed, both facts are quarantined until curator resolves.
4. **Audit trail**: every auto-merged fact carries full provenance (`agent`, `proposed_at`, `src`) for forensic review. A `lorekeep agent status` dashboard shows auto-merge rate per agent.

### Rate limiting

Per-agent write caps prevent journal flooding:

| Limit | Value | Rationale |
|---|---|---|
| Per session | 200 entries | One conversation shouldn't dominate the journal |
| Per minute | 30 entries | Burst protection |
| Per fact id | 3 re-proposals per session | Idempotent dedup checks `(agent, fact_id)` within the same session. Beyond 3 re-proposals of the same fact id in one session, the entry is quarantined and escalated to curator. Re-proposals across different sessions are allowed (reset on session boundary). |
| Batch resolve threshold | 50 entries | Trigger (not a limit — resolve runs at threshold OR time interval) |

### Journal storage

`pending/` journals are committed to git for sync, but quarantined entries (confidence < 0.5) have their `fact` content redacted to only `id`, `type`, `agent`, and `proposed_at`. The full fact payload is written to a gitignored `pending/.quarantine/` directory. This prevents quarantined fact content from leaking to anyone with repository access while preserving the audit trail.

Alternatively, `pending/` can be fully gitignored and synced via a separate channel (S3, shared filesystem) — configurable per deployment.

## Related

- [Pipeline](pipeline.md) — how resolve merges journals into the graph.
- [Agent](agent.md) — autonomous agent that triggers resolve, lint, and compile.
- [Serve & MCP](serve-mcp.md) — the write tools agents call.
