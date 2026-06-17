# Temporal model

> Adapted from the original design spec.

Every node and edge carries `valid_from` (ISO date) and `valid_to` (ISO date or `null`). A fact is **active** at time `t` under the half-open interval `valid_from ≤ t < valid_to`, treating `null` as unbounded on that side (`valid_to: null` ⇒ still current).

This is implemented in `GraphStore._active` (`src/lorekeep/store/graph.py`).

## Queries

- **`at_time(t)`** — snapshot of all facts whose window contains `t`.
- **`history(id)`** — all versions of an entity plus every edge touching it, ordered by `valid_from` (`None` first).
- **`changes(t1, t2)`** — edges whose validity window **began** or **ended** within `[t1, t2)`.

History is modelled as multiple edges sharing the same endpoints (and type) with different validity windows — not as mutation. A dependency that ended `2025-03-01` and a new one that began the same day are two coexisting edges; `at_time` selects the right one.

## Composition with permission

Temporal filtering composes with [permission](permission.md) filtering: a temporal query returns only facts the caller is allowed to see. `ScopedGraph.snapshot`, `.history`, and `.changes` apply both layers.

## Why a temporal graph

Temporal KG-QA is the industry weak spot — specialized memory systems drop to ~20% on temporal reasoning. A structured temporal graph is Lorekeep's core bet, and [Tier-2 evaluation](evaluation.md) stresses it heavily.
