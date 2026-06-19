# Importing agent sessions

`lorekeep import` pulls knowledge from a coding agent's sessions into `raw/`, where `lorekeep compile` turns it into the graph. It is a curator-side feeder — the agent itself never writes to the graph.

## Sources

| Source | Where it reads | Modes |
|---|---|---|
| `claude` | Claude Code's per-project session dir (`~/.claude/projects/<slug>/`) | `--quick` (memory files, no LLM) + deep (LLM-summarized transcript) |
| `cursor` | Cursor's **global** composer DB (`globalStorage/state.vscdb`) | deep-only (`--quick` rejected) |

## Claude Code

```bash
uv run lorekeep import --from claude                # deep: memory + LLM-summarized transcript
uv run lorekeep import --from claude --quick        # quick: copy memory/*.md only, no LLM
```

- **quick** copies curated `memory/*.md` verbatim — fast, zero LLM cost.
- **deep** (default) additionally parses the session transcript and LLM-summarizes it into structured markdown.
- Writes to `raw/claude-memory/` and `raw/claude-session/` (override with `--memory-ns` / `--session-ns`).

## Cursor

```bash
uv run lorekeep import --from cursor                          # uses the global state.vscdb
CURSOR_STATE_DB=/path/to/state.vscdb uv run lorekeep import --from cursor
uv run lorekeep import --from cursor --session-path /path/to/globalStorage
```

Cursor conversations live **globally** (not per-project), so the importer reads all of them from `globalStorage/state.vscdb` and writes summaries to `raw/cursor-session/`. Each conversation is chunked and LLM-summarized (deep mode).

**Limitation.** Cursor frequently persists only conversation *headers* locally and lazy-loads the full transcript from the cloud. On such installs, conversations with no local content are skipped — `import` reports fewer (or zero) files rather than failing. If you see "0 session files", that means Cursor has no locally-persisted transcript to import.

## Idempotent re-import

Both sources record a content hash per imported item under `raw/<ns>/.import-manifest.json`. Re-running `import` skips unchanged content, so it's safe to run repeatedly before `compile`.

## Next

```bash
uv run lorekeep compile      # raw/ -> graph/facts.jsonl
```

See [Compiling the graph](compile.md). Path resolution (`LOREKEEP_HOME`, dev mode, XDG) is in [data-home.md](data-home.md).
