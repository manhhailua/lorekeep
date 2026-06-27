# Autonomous agent

> **Status: partially implemented.** The daemon (`lorekeep agent watch`), `agent ingest`, `agent lint`, `agent suggest`, and `agent status` are shipped. The daemon auto-compiles on `raw/` change, auto-resolves pending journals, and delta-imports Claude session memory. Scheduled nightly lint / weekly suggest, schema evolve (`agent evolve`), and `--auto-fix` are planned. MCP write tools (runtime fact proposals) are tracked in [#15](https://github.com/manhhailua/lorekeep/issues/15).

The autonomous agent (`lorekeep agent`) is the engine that keeps the knowledge graph continuously up-to-date. It watches for changes, triggers compiles and resolves, runs health checks, and suggests improvements — all without manual curator intervention for routine operations.

## Trigger model

Inspired by the LLM Wiki ecosystem (see references below), Lorekeep uses a **hybrid trigger model**: some operations are fully autonomous, some are gated by confidence, and some require explicit human approval.

```
TRIGGER SPECTRUM
═══════════════════════════════════════════════════════════════

Fully Autonomous           Confidence-Gated           Human-Only
───────────────────────────────────────────────────────────────
import sessions            compile (raw/ changed)       ingest (conversational)
lint report                lint --auto-fix              schema evolve
resolve (periodic)         self-heal                    raw/ file edit
suggest improvements
watch raw/ → detect change
```

## Operations

### Daemon mode (`lorekeep agent watch`)

Runs continuously, polling or watching the filesystem. Handles all routine maintenance.

```
lorekeep agent watch
  ├── Watch raw/ for new or changed .md files
  │   └── On change → hash check → auto-compile → resolve
  ├── Watch agent session dirs for new sessions
  │   └── On new session → auto-import (quick mode)
  ├── Periodic lint (nightly, configurable)
  │   └── Generate report + auto-fix high-confidence issues
  ├── Periodic resolve (every 5 min if pending)
  │   └── Merge pending journals into facts.jsonl
  └── Periodic suggest (weekly)
      └── Analyze graph structure → report gaps, orphans, stale
```

### Compile trigger

| Trigger | Behavior | Gate |
|---|---|---|
| **raw/ file added** | Hash check → if new, auto-compile | None (safe: new file, no cache bust) |
| **raw/ file modified** | Hash check → if changed, auto-compile | None (cache handles unchanged chunks) |
| **raw/ file deleted** | Re-compile to remove facts from that source | None (source gone → facts become stale) |
| **Schema changed** | Full re-compile (cache invalid) | Manual only (costly, curator must approve) |

### Lint trigger

| Trigger | Behavior | Gate |
|---|---|---|
| **Nightly** | Full semantic lint pass → `manifest.lint_report` | Auto-fix confidence ≥ 0.85 |
| **Post-compile** | Incremental lint (only new/changed facts) | Report only |
| **On-demand** | `lorekeep agent lint` | As requested |

Lint checks:
- **Contradictions**: facts that conflict (same entity, contradictory props)
- **Orphan facts**: nodes with zero inbound or outbound edges
- **Stale facts**: `valid_to` expired, no superseding edge
- **Missing entities**: nodes referenced in edges but not in the node set
- **Coverage gaps**: namespaces or types with few facts relative to others

### Resolve trigger

| Trigger | Behavior |
|---|---|
| **Batch threshold** | After N pending journal entries (default 50) |
| **Time interval** | Every T minutes if pending entries exist (default 5) |
| **Post-compile** | After compile, immediately resolve pending |
| **Session end detected** | Agent session dir modified → resolve |
| **Manual** | `lorekeep resolve` |

### Import trigger

| Trigger | Behavior |
|---|---|
| **Session end** | Agent session dir modified → auto-import (quick mode) |
| **Hourly** | Import all unimported sessions |
| **Manual** | `lorekeep import --from claude` |

### Suggest trigger

| Trigger | Behavior |
|---|---|
| **Weekly** | Analyze graph for gaps, orphans, under-sourced areas → report |
| **On query** | Agent queries expose knowledge gaps → suggest improvements |

## CLI interface

```bash
# Daemon (autonomous)
lorekeep agent watch                    # start watching filesystem

# One-shot operations
lorekeep agent lint                     # full semantic health check
lorekeep agent lint --auto-fix          # auto-apply high-confidence fixes
lorekeep agent lint --focus <id>        # lint a specific entity
lorekeep agent suggest                  # generate improvement suggestions
lorekeep agent status                   # graph health dashboard

# Conversational (human in the loop)
lorekeep agent ingest <source>          # interactive ingest with guidance

# Schema evolution (human in the loop)
lorekeep agent evolve                   # suggest schema improvements
lorekeep agent evolve --apply <change>  # apply approved schema change
```

## Confidence gates

All autonomous mutations are gated. The agent never silently corrupts the graph.

Two distinct confidence thresholds exist — these are intentionally different gates:

| Threshold | Applies to | Value |
|---|---|---|
| **Fact merge** | Agent-proposed facts entering `facts.jsonl` via resolve | ≥ 0.8 |
| **Lint auto-fix** | Autonomous lint correction (e.g., marking stale facts) | ≥ 0.85 |

Lint auto-fix has a higher bar because it modifies *existing* facts that may already be relied upon. Fact merge introduces *new* facts at lower risk.

```
Autonomous action proposed
  │
  ├── Confidence ≥ 0.85 (high) — lint auto-fix threshold
  │   └── Auto-apply → facts.jsonl + log
  │
  ├── 0.8 ≤ Confidence < 0.85 — fact merge only, no lint auto-fix
  │   └── Merge fact OR flag lint finding for review
  │
  ├── 0.5 ≤ Confidence < 0.8 (medium)
  │   └── Apply + flag → manifest.review
  │
  └── Confidence < 0.5 (low)
      └── Reject → manifest.quarantine + notification
```

Confidence is compound: for auto-fix, both the lint finding confidence AND the fix confidence must meet their respective thresholds for auto-apply.

## Daemon lifecycle

```
┌──────────────────────────────────────────────────────┐
│                 lorekeep agent watch                 │
│                                                      │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────┐   │
│  │ Watcher  │  │ Scheduler│  │  Resolver         │   │
│  │          │  │          │  │                   │   │
│  │ raw/     │  │ nightly  │  │ poll pending/     │   │
│  │ sessions │  │ hourly   │  │ → resolve when    │   │
│  │ schema   │  │ weekly   │  │   threshold met   │   │
│  └────┬─────┘  └────┬─────┘  └────────┬──────────┘   │
│       │             │                 │               │
│       ▼             ▼                 ▼               │
│  ┌────────────────────────────────────────────────┐   │
│  │              Action Queue                      │   │
│  │  compile → resolve → lint → suggest → import   │   │
│  └────────────────────┬───────────────────────────┘   │
│                       │                               │
│                       ▼                               │
│  ┌────────────────────────────────────────────────┐   │
│  │              State Machine                      │   │
│  │  IDLE → COMPILING → RESOLVING → LINTING → IDLE │   │
│  │  (actions serialized; no concurrent mutations)  │   │
│  └────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────┘
```

Actions are serialized through a state machine: only one mutation (compile, resolve, lint with auto-fix) runs at a time. This preserves determinism and avoids race conditions on `facts.jsonl`.

## Cost profile

| Operation | Frequency | LLM cost | Notes |
|---|---|---|---|
| Watch + compile | On raw/ change | Chunk cache hit rate > 90% after first compile | Only new/changed chunks cost. In team setups with git-synced raw/, a teammate's push → your pull → new files → your daemon triggers LLM extract. Gate with `auto_compile: false` in config if you want manual-only compile on shared repos. |
| Resolve | Every 5 min / 50 writes | **Zero** | Pure Python |
| Lint | Nightly | **Zero** | Pure graph analysis (no LLM needed for structural lint) |
| Lint (semantic) | Weekly | Low | Optional: LLM for semantic contradiction detection |
| Import (quick) | Session end | **Zero** | File copy only |
| Import (deep) | On demand | 1 LLM per session | LLM-summarizes transcript |
| Suggest | Weekly | Low | LLM analyzes graph structure |

**Total daily LLM cost:** near-zero for routine operations. The only LLM calls are raw/ extraction (cache-hit dominant) and optional deep import / semantic lint.

## References

- Karpathy LLM Wiki gist: https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f — original three-operation model (ingest, query, lint)
- LLM Wiki v2: https://gist.github.com/rohitg00/2067ab416f7bbe447c1977edaaa681e2 — confidence scoring, supersession, consolidation tiers
- LLM Wiki v3: https://github.com/vvvvvivekkk/LLM-Wiki-v3 — gated autonomous writes, provenance chain, audit trail
- Synthadoc: https://github.com/axoviq-ai/synthadoc — scheduled ingest/lint, auto-detecting health mode
- AutoSci: https://github.com/skyllwt/AutoSci — autonomous sleep phase, cross-model review

## Related

- [Journal](journal.md) — how agent-proposed facts enter the system.
- [Pipeline](pipeline.md) — how resolve merges journals into the graph.
- [Serve & MCP](serve-mcp.md) — the write tools agents call.
