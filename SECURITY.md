# Security policy — Lorekeep

Lorekeep compiles team documents into a temporal knowledge graph and serves it
to AI coding agents over MCP. This document describes the threat
model and the configuration decisions that keep a deployment safe.

## Trust model

- **Compile + journal-based writes.** The graph (`graph/facts.jsonl`) is produced by
  `lorekeep compile` and never mutated directly by the server. Agents read via MCP
  and propose facts through journal-based write tools that append to `pending/` —
  facts enter the graph only after a resolve pass (confidence-gated). No concurrency
  control is needed on the read path because `facts.jsonl` is replaced atomically.
- **Per-process namespace scope.** An MCP server's visible data is fixed at startup
  by `LOREKEEP_NS` (comma-separated namespaces). Visibility is enforced by a single
  chokepoint, `ScopedGraph` (`src/lorekeep/perm/ns.py`), applied to **every** query.
- **Deny-by-default.** `effective_ns = allowed ∪ {public}`. A node is visible iff
  `node.ns ∩ effective_ns ≠ ∅`; an edge is visible iff **both** endpoints are
  visible **and** `edge.ns ∩ effective_ns ≠ ∅`.
- **No information oracle.** `get_node` returns the same `"not found or out of
  scope"` whether a node is absent or merely outside scope. `list_namespaces`
  returns only the caller's own `effective_ns` — it does **not** enumerate
  namespace names that exist but are hidden.

## Compile-time data egress

At compile, **every file under `raw/` is sent to the configured LLM provider**
for extraction. This is by design (the documents *are* the knowledge graph's
source), but it has two consequences:

1. Treat `raw/` as trusted content. Do not point `LOREKEEP_RAW` at a directory
   that holds secrets.
2. **Symlink guard.** `compile/ingest.py` skips any file whose resolved target
   escapes `raw_root` and warns on stderr. This prevents a planted symlink
   (`raw/x/leak.md -> ~/.ssh/id_rsa`) from exfiltrating files outside `raw/` to
   the provider. Keep this guard; do not disable it.

For team/shared `raw/` directories (a stated target), the trust boundary is
"anyone who can write to `raw/`". Isolate raw/ per team and compile per team.

## API keys

- Prefer `api_key_env` (the name of an environment variable) over an inline
  `api_key`. The provider resolves the env var first and only falls back to the
  inline value if the env var is unset.
- `config.yaml` is gitignored by default (`.lorekeep/*` except the `.example`
  template). **Never commit a real `config.yaml`.** If an inline key is used,
  `lorekeep compile` prints a warning recommending `api_key_env`.
- Keys are passed only to `litellm.completion` at compile; the server never reads
  or transmits a key.

## No remote-code surface

- No `subprocess`, shell, `eval`, `exec`, `pickle`, or `marshal`. Config uses
  `yaml.safe_load`; all user-supplied data is parsed as JSON. SQLite FTS uses
  parameterized queries.
- Filesystem writes are confined to the data home (`raw/`, `graph/`, `.lorekeep/`)
  and, for `lorekeep mcp add`, the agent config file (`.mcp.json` /
  `.cursor/mcp.json` / `config.toml`), which is merged, not clobbered.
- `facts.jsonl` and `manifest.json` are written atomically (temp file +
  `os.replace`), so a concurrent read during compile never sees a partial file.

## Reporting a vulnerability

Please open a private security advisory on
[github.com/manhhailua/lorekeep](https://github.com/manhhailua/lorekeep/security/advisories/new)
rather than a public issue. Include the affected version, a reproduction, and
impact. Reports are acknowledged within 7 days. A fix and disclosure are
coordinated with the reporter.

## Residual accepted risks

- An inline `api_key` may live in a gitignored `config.yaml`. Owners are
  responsible for keeping that file local.
- Shared `raw/` directories trust everyone with write access (see egress above).
- The MCP client (the coding agent) is trusted within its `LOREKEEP_NS` scope: it
  can read everything in scope, which is the intended behavior. Scope assignment
  is an operational responsibility, not enforced by the server.
