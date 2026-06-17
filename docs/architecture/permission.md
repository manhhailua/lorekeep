# Permission model

> Adapted from the original design spec.

Permission is **deny-by-default** and enforced at a single chokepoint: `ScopedGraph` (`src/lorekeep/perm/ns.py`), which wraps the pure `GraphStore` and filters every query. There is no raw bypass path — any new query must go through `ScopedGraph`, not `GraphStore` directly.

## Namespace origin

Namespace is derived from directory structure: every fact extracted from `raw/<ns>/*` is tagged `ns: ["<ns>"]` (or multiple if shared). `["public"]` is globally visible.

## Identity → namespace

- `LOREKEEP_NS` env var (comma-separated), or `ns.default` in `.lorekeep/config.yaml`.
- The permission engine only needs the `allowed_ns` set; the source of that set is pluggable for future OIDC/team-sync.

## Visibility rules

Define the effective allowed set as `A' = A ∪ {"public"}` — every caller implicitly sees `public`.

- **Node** visible ⇔ `node.ns ∩ A' ≠ ∅`.
- **Edge** visible ⇔ **both** endpoint nodes are visible to `A'` **and** `edge.ns ∩ A' ≠ ∅`.
- Empty/unknown `A` ⇒ `A' = {"public"}` ⇒ sees only `public` facts.

The strict endpoint rule is what prevents leakage: an edge never reveals a cross-namespace neighbor's existence. If you can't see one endpoint, you can't see the edge — even if the edge itself is in your namespace.

## Where it applies

Permission filters compose with [temporal](temporal.md) filtering: a temporal query returns only facts the caller is allowed to see, within the requested time window. The MCP tools in [serve & MCP](serve-mcp.md) all route through `ScopedGraph`, so every result an agent receives is already scoped.
