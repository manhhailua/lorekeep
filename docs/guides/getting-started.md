# Getting started

A 10-minute walkthrough from install to a working, agent-readable knowledge
graph — including the backup setup. For the terse 5-step version, see the
[Quickstart](../../README.md#quickstart) in the project README.

## What you'll have at the end

- A **data home** (`.lorekeep/` in a source checkout, or an XDG dir for an
  installed copy) holding your config, schema, raw docs, and compiled graph.
- A compiled `facts.jsonl` graph built from your markdown.
- A coding agent (Claude Code / Cursor / Codex / opencode) reading the graph over MCP,
  scoped to a namespace.
- A private git backup of the data home, so the graph syncs to your other
  machines.

## 1. Install

Lorekeep needs Python 3.11+. Run it without installing:

```bash
uvx lorekeep version      # prints the version
```

Or, in a source checkout (development):

```bash
git clone https://github.com/manhailua/lorekeep.git
cd lorekeep
uv sync
uv run lorekeep version
```

## 2. Bootstrap the data home

```bash
uvx lorekeep init
```

`init` is idempotent and **interactive on first run** — it asks:

1. **LLM provider** — OpenAI / Anthropic / DashScope-Qwen / Ollama (local) /
   Skip (offline). Pick one; model and API key env are pre-filled from the
   preset. Override either if you want. The **API key** you type is saved
   inline into the gitignored `config.yaml` (not an env var).
2. **Default namespace** — defaults to `me`. This is the permission unit agents
   are scoped to (e.g. `me`, `backend`, `myproject`).
3. **Name + bio** — a one-line profile. It becomes the first file,
   `raw/<ns>/about.md`, so the compiled graph starts with a fact about you.

It then writes `config.yaml` + `schema.json`, creates `raw/` + `graph/` dirs,
writes `raw/<ns>/about.md`, and **auto-detects coding agents** to wire:

- If you're running `init` **inside** a coding agent (e.g. from opencode's or
  Claude Code's shell), it wires only that agent.
- If you're in a **plain shell**, it scans for all installed agents
  (`~/.claude`, `~/.cursor`, `~/.codex`, `~/.config/opencode`) and wires each.

Each detected agent gets its MCP config written automatically (`.mcp.json`,
`.cursor/mcp.json`, `config.toml`, or `opencode.json` — depending on the agent).
Restart the agent to pick up the new tools.

Non-interactive (CI, scripts): `uvx lorekeep init --yes`. From a source
checkout it uses the repo's own `.lorekeep/`; for an installed copy it uses
XDG (`~/.config/lorekeep` for config, `~/.local/share/lorekeep` for data). See
[Data home & path resolution](data-home.md) for the full precedence table.

Verify the install:

```bash
uvx lorekeep doctor       # graph loads, schema valid, a tool responds
```

## 3. Add your raw docs

Drop markdown under `<data-home>/raw/<namespace>/`. A namespace is the
permission unit an agent is later scoped to — e.g. `backend`, `frontend`,
`team-alpha`.

```bash
# installed copy (XDG)
mkdir -p ~/.local/share/lorekeep/raw/backend
cp your-service.md ~/.local/share/lorekeep/raw/backend/

# source checkout
mkdir -p .lorekeep/raw/backend
cp your-service.md .lorekeep/raw/backend/
```

Raw markdown is the **source code**; the graph is the **executable**. Each doc
becomes one or more facts with `path:line` provenance back to the source.

## 4. Configure a provider (only for real compiles)

`init` already wrote a provider into `config.yaml` during onboarding, with your
key inline. Edit it here if you want a different one. The key lives in
`config.yaml`, which is gitignored — so it never gets committed.

```yaml
provider:
  model: deepseek/deepseek-chat            # {provider}/{model} — litellm routes by the prefix
  api_base: null                           # native deepseek provider — no api_base needed
  api_key: sk-...                          # inline; config.yaml is gitignored
  temperature: 0.0
```

The model string must be `{provider}/{model}` (e.g. `openai/gpt-4o-mini`,
`anthropic/claude-sonnet-4-20250514`, `deepseek/deepseek-chat`,
`dashscope/qwen-plus`, `ollama/llama3`). Native providers (`openai`,
`anthropic`, `deepseek`, `dashscope`, `gemini`, …) need **no** `api_base` —
litellm already knows their endpoint. Set `api_base` only for a custom
OpenAI-compatible endpoint (vllm, lm_studio, a proxy/gateway, or Ollama on a
non-default host). A bare name like `deepseek-chat` is rejected with a
suggestion — `lorekeep doctor` will tell you exactly what's wrong.

Prefer an env var instead? Set `api_key_env: DEEPSEEK_API_KEY` (and
`api_key: null`), then `export DEEPSEEK_API_KEY=sk-...`. Both work.

> **No provider yet?** `init` wires agents and imports memory files
> regardless. Compile is skipped until you add an API key — the graph
> will be empty but the MCP tools are wired and ready.

## 5. Compile

```bash
uvx lorekeep compile      # raw/*.md -> graph/facts.jsonl + manifest.json
uvx lorekeep check        # loads, no dangling edges (exit 1 on failure)
```

Recompiling unchanged input is **byte-identical** (determinism is a hard
requirement) — unchanged chunks return cached LLM output, so only edited docs
cost tokens on recompile.

## 6. Wire additional coding agents

`init` already wired agents it detected. To add more, or to re-scope one:

```bash
uvx lorekeep mcp add --agent opencode --ns backend
```

Supported agents: `claude`, `cursor`, `codex`, `opencode`.

Restart the agent → the 14 Lorekeep tools (9 read: `search`, `get_node`,
`neighbors`, `at_time`, `history`, `changes`, `list_namespaces`, `schema`, `meta`;
5 write: `propose_fact`, `link_facts`, `flag_contradiction`, `update_fact`,
`suggest_improvement`) are
available, scoped to `backend` (+ `public`). See
[Serving the graph](serve.md).

## 7. Keep the graph current (daemon)

The agent daemon watches for changes and keeps the graph up-to-date automatically:

```bash
uvx lorekeep agent watch &
```

It monitors three things:

| Watch | Action | Cost |
|---|---|---|
| `raw/<ns>/*.md` changed | Auto-compile (only changed chunks hit the LLM cache) | Chunk-cache hit rate > 90% |
| `pending/*/journal.jsonl` written | Auto-resolve (merge + dedup pending facts) | **Zero LLM** — pure Python |
| Claude session `memory/*.md` changed | Delta quick-import into `raw/claude-memory/` → triggers compile | **Zero LLM** — file copy |

The MCP server **lazy-reloads** `facts.jsonl` on every query, so graph updates
are visible immediately — no reconnect needed. Run `agent watch` in the
background (or under a process manager) and the graph stays current as you edit
docs or use the coding agent.

To disable session watching: `uvx lorekeep agent watch --no-watch-sessions`.

## 8. Back up

Push the data home to a **separate private git repo** so it syncs across
machines (this is independent of the lorekeep tool repo):

```bash
git init --bare ~/backups/lorekeep.git     # one-time, anywhere private
uvx lorekeep backup --init ~/backups/lorekeep.git
```

Then after any `compile` or raw change:

```bash
uvx lorekeep backup
```

The backup tracks `raw/` + `schema.json`; it ignores `config.yaml` (may hold a
key), the regenerable `graph/facts.jsonl` / `manifest.json`, `cache.json`, and
`pending/`. Full lifecycle — restore on a second device, push-conflict
recovery — is in [Backing up the data home](backup.md).

## Troubleshooting

- **`lorekeep serve` hangs** — it blocks on stdio waiting for an MCP client.
  To just confirm it boots, run it under a timeout with stdin closed:
  `timeout 3 uvx lorekeep serve --transport stdio </dev/null`.
- **Compile gave 0 nodes** — the provider wasn't reached. Run
  `lorekeep doctor` first: it pings the provider and reports auth / model /
  endpoint failures directly. Then check `model` (must be
  `{provider}/{model}`), `api_base`, and `api_key` / `api_key_env` in
  `config.yaml`. (The cache key includes the model, so switching it
  re-extracts automatically — no need to delete `cache.json`.)
- **`check` reports dangling edges** — an edge points at a node that isn't in
  the graph. Re-open the source doc at the `path:line` in the edge's `src` and
  fix the reference, then recompile.
- **Agent can't see a namespace** — serve-time scope comes from `LOREKEEP_NS`
  (comma-separated) or `config.ns.default`; `mcp add --ns` writes the scoped
  `.mcp.json` but the running agent must be restarted to pick it up.

## Next steps

- [Compiling](compile.md) — chunking, extraction, the resolve pass.
- [Importing agent sessions](import.md) — turn Claude Code / Cursor history
  into raw docs.
- [Serving the graph](serve.md) — the daemon lifecycle, write tools roadmap,
  and how lazy-reload works.
- [Architecture overview](../architecture/overview.md) — the append-and-resolve
  model and why there's no runtime write path (yet).
