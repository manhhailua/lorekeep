# Autonomous agent and graph operations

Lorekeep's “agent” surface combines an event-driven watcher with explicit
one-shot graph/source operations. It is not a planner or periodic job scheduler.

## Public commands

| Command | Current role | LLM use |
|---|---|---|
| `agent detect` | report installed, active, session-data, wiring/hook state | none |
| `agent wire` | idempotently write MCP config + supported hooks | none |
| `agent watch` | poll sources/journals/agents and coordinate maintenance | compile only when triggered |
| `agent service install/uninstall/status` | OS persistence wrapper around `agent watch` | none |
| `agent ingest` | extract/review one raw file and journal approved facts | yes |
| `agent lint` | structural/semantic graph heuristics | none |
| `agent lint --auto-fix` | remove dangling/duplicate edges and republish | none |
| `agent suggest` | list missing dates/sources, sparse namespaces/edges | none |
| `agent status` | graph counts, namespaces, lint/pending counts | none |
| `agent profile` | print/open editable personal raw source | none |
| `agent contribution` | find personal-only shareable entities | none |

There is no `agent evolve` command.

## Detection and wiring

The registry defines eight clients: Claude Code, Codex, Cursor, opencode, Grok Build, Qoder, GitHub Copilot, and Command Code.
Each `AgentSpec` owns detection markers, active-shell env vars, config/hook
targets, and memory/session importer functions.

`detect_active_agent` examines known environment markers. Installed detection
checks client directories and binaries. The active client is listed first but
does not exclude other installed clients; Lorekeep is designed to aggregate all
of them.

`agent wire` uses registry writers and preserves unrelated native config. It can:

- wire all detected/enabled agents;
- target one explicit `--agent`;
- use project or user `--scope`;
- set one MCP namespace;
- preview with `--dry-run`; and
- include undetected agents with `--force`.

Writers report unchanged without rewriting. The watcher can therefore re-run
detection periodically without causing config mtime churn. Failed clients enter
a one-hour per-process backoff.

### Lifecycle capture contracts

Lorekeep uses the closest lifecycle boundary each client actually exposes. An
exact end event is consumed immediately; a turn/idle fallback is coalesced by
session and consumed only after `agents.session_end_idle_seconds` (default 300)
without another event.

| Client | Event | Fidelity | Lorekeep hook scope |
|---|---|---|---|
| [Claude Code](https://code.claude.com/docs/en/hooks) | `SessionEnd` | exact | project + user |
| [Codex](https://learn.chatgpt.com/docs/hooks) | `SessionEnd` (main thread, 3 s maximum) | exact | project + user |
| [Cursor](https://cursor.com/docs/hooks) | `sessionEnd` | exact | project + user |
| [opencode](https://opencode.ai/docs/plugins/) | `session.idle` | idle fallback | project + user |
| [Grok Build](https://docs.x.ai/build/features/hooks) | `SessionEnd` | exact | project + user |
| [Qoder](https://docs.qoder.com/cli/hooks) | `SessionEnd` | exact | project + user |
| [GitHub Copilot CLI](https://docs.github.com/en/copilot/reference/hooks-reference) | `sessionEnd` | exact | user/local only |
| [Command Code](https://commandcode.ai/docs/hooks) | `Stop` | end-of-turn fallback | project + user |

Copilot repository hooks also execute in ephemeral cloud jobs, including
`sessionEnd`, where the local Lorekeep interpreter and data home do not exist.
Its capture hook is therefore intentionally available only with user-scope
wiring; project-scope Copilot wiring reports that capture was skipped. Cursor
cloud also loads project hook files, but does not fire the IDE-lifetime
`sessionEnd` event, so both Cursor scopes are safe. `agent detect` reports the
real event name and whether it is native or fallback.

Two clients gate hooks behind an explicit trust step, so freshly written hooks
stay silent until the user approves them once:

- **Codex** records trust against the hook command's hash. Review via `/hooks`
  inside Codex; any later change to the command string (different interpreter
  or `--home`) requires re-trust.
- **Grok Build** requires `/hooks-trust` (or a `--trust` launch) before
  project-scope hooks run; user-scope hooks need no trust.

Codex also fires `SessionEnd` only when a conversation closes, is archived, or
has been idle for 30 minutes — never on conversation switch — which is why the
watcher's startup recovery pass exists.

Native handlers run the exact Python interpreter that wired Lorekeep; they do
not cold-start `uvx`. A handler reads at most 256 KiB of JSON stdin, normalizes
only session id/transcript path/cwd/reason, atomically writes a mode-0600 event
inside mode-0700 `hook-events/<agent>/` directories, and exits. Repeated fallback events for one
session replace the same file and restart its idle window. Transcript I/O and
compilation never run inside the hook process. Because the interpreter path is
machine-local, project-scope hook files (which clients encourage committing)
should be wired per machine; rewiring on another machine simply rewrites the
path. Claude Code and Command Code share `.mcp.json` as their project MCP
location — wiring both at project scope makes them alternate the
`mcpServers.lorekeep` entry, so prefer user scope when running both.

The queue is device-local and excluded from backup because its transcript paths
belong to that device. The daemon validates paths against the owning client's
data roots, imports only the named session, removes successful events, and
retains failures with bounded exponential retry backoff (dropped after ten
attempts). `doctor` shows queued, idle-waiting, retrying, or invalid events
for troubleshooting.

## Watcher startup

`agent watch`:

1. prints resolved raw/pending paths and mode;
2. refuses to start when `.daemon.pid` belongs to a live process;
3. writes its PID and installs SIGTERM cleanup;
4. snapshots the installed package version;
5. loads `agents` config;
6. synchronizes the private backup remote when configured; and
7. replays pending/accepted journals after sync before entering the loop.

Startup synchronization precedes replay so remote journal events are available
on this device.

## Poll loop

The loop sleeps `--interval` seconds (default 60) and performs these checks in
order.

### Package upgrade

The running process compares its startup distribution version to current
on-disk metadata. When an external `uv tool upgrade`/install changes it, the
watcher removes the PID file and replaces itself with `os.execv`, picking up new
code without waiting for systemd/launchd restart.

### Agent wiring

On first pass and every `agents.wire_interval_seconds` (default 900), it detects
installed clients and idempotently wires enabled ones according to
`agents.wire_scope`.

### Lifecycle event drain

Before taking the raw-file snapshot, the watcher drains ready lifecycle events.
Parsed turns become bounded deterministic Markdown under
`raw/<agent>-session/`. A successful event import forces compile in the same
poll cycle, including the daemon's first cycle. When an exact end hook was
missed while Lorekeep was stopped, one startup recovery pass imports the latest
locally discoverable session for each client; live transcript polling is not a
primary capture path.

### Raw/schema compile

The watcher tracks both Markdown file count and maximum mtime, plus schema mtime.
After the initial baseline, any count change, newer raw mtime, or newer schema
mtime runs the same compile pipeline as the CLI. File count catches new files
even on filesystems where mtimes share a coarse tick.

Before checking file changes, the watcher checks for a `.compile-requested`
sentinel file — written by `lorekeep compile` in interactive mode. If present,
it triggers a compile and unlinks the sentinel. This is how background compile
works: the `compile` command touches the sentinel and ensures the daemon is
running; the daemon detects it on the next poll and runs the full pipeline.

Compile errors are reported/logged but do not kill the daemon. Total provider
failure does not terminate the loop.

After successful compile it:

1. synchronizes backup if `agents.auto_backup` is true (default);
2. replays/merges journals;
3. runs deterministic self-heal when `agents.self_heal` is true (default);
4. synchronizes backup again if self-heal changed facts; and
5. generates the wiki once from final facts.

### External compile detection

The watcher monitors `manifest.json` mtime each cycle. When another process
(CLI, serve, another daemon) writes a new graph, the mtime advance triggers an
immediate backup so externally-compiled changes are not lost. The `not compiled`
guard prevents double-backup when the daemon itself compiled that cycle. On the
first cycle, `last_manifest_mtime` is initialized to the current value so no
spurious backup fires at startup.

### Journal resolve

The watcher tracks maximum mtime across `pending/**/journal.jsonl`. A later mtime
triggers immediate auto-resolve in that poll cycle. After a successful resolve,
the daemon synchronizes backup if `agents.auto_backup` is true. There is no
batch-size or five-minute threshold.

### Memory quick import

Every cycle re-discovers registry agents with a curated memory source (currently
Claude and Codex). First sight or newer memory mtime triggers content-hash-based
quick import, rate-limited to once per 30 seconds per source. State advances only
after success, so a failed import can retry.

### Transcript retention

When `agents.watch_transcripts` is true, lifecycle events and startup recovery
use registry parsers for all eight clients. Output is capped by
`transcript_max_chars` and `transcript_max_batches`; only
`transcript_retain_sessions` recent sessions remain per generated session
namespace. Content hashes avoid rewriting unchanged batches. No LLM is called.

## Self-heal

`agents.self_heal` (default `true`) is deliberately narrow:

- remove edges whose real endpoint node is missing;
- deduplicate edges with identical type/endpoints/validity;
- report circular dependencies; and
- report orphan nodes without deleting them (see Quarantine below for parking
  them for review instead of letting them resurface every compile).

The pure function returns a new `GraphStore` plus `HealReport`; callers decide
whether to publish. The daemon runs it only after a successful compile. The CLI
also previews fixability during `agent lint` and persists it with
`agent lint --auto-fix`.

Self-heal does not synthesize facts, resolve semantic contradictions, fill
descriptions, or alter raw sources.

## Quarantine (#266)

Orphan nodes (`lint`/self-heal report them but never remove them) accumulate
across compiles because the LLM re-extracts the same low-signal facts every
time. `lorekeep quarantine detect [--apply]` reuses `agent.lint(store).orphans`
to find them, then `--apply` stamps `props.quarantined_at` /
`props.quarantined_reason` onto each — a props flag, not a schema field, so no
migration is needed. `lorekeep quarantine review` walks every currently
quarantined node and asks `[r]estore` / `[k]eep` / `[s]kip`; restore clears
both props, keep leaves the flag, skip revisits it next time.

The flag must survive a full `compile` the same way `props.merged_ids` does
(`_load_prev_aliases`): `compile_graph()` rebuilds every node fresh from
`raw/*.md`, so `cli._load_prev_quarantine()` reads the flag back from the
previous `facts.jsonl` and `pipeline._apply_prev_quarantine()` restamps it
after `resolve()`. `lint`/self-heal skip already-quarantined nodes so a parked
node stops being reported as noise. `wiki.py` excludes a node from wiki output
only while it is **both** quarantined and still degree-0 — re-checked at wiki
generation, not just the persisted flag, so a node that gains an edge (e.g. via
an agent's `propose_change`) reappears automatically without a manual restore.

Quarantine never touches `raw/*.md` or deletes anything — the node and its
`src` provenance stay in `facts.jsonl` for as long as it's parked.

## Lint, suggest, and status

`lint` currently detects:

- orphan nodes (excluding those already quarantined — see above);
- missing edge endpoints;
- expired edges;
- very sparse namespaces relative to the graph; and
- duplicate node ids with conflicting props when such duplicates reach the
  loaded store.

`suggest` lists nodes with one/fewer sources, missing `valid_from` on such nodes,
single-namespace graphs, and graphs with no edges. It is deterministic graph
analysis despite the command name; no provider call occurs.

`status` reports global (unscoped local) node/edge/namespace counts, total lint
issues, and pending-journal count. For namespace-filtered agent-facing status,
use the MCP `context(section="status")` tool.

None of these one-shot commands runs nightly/weekly from the watcher.

## Conversational ingest

`agent ingest <raw-file>` requires the file to resolve beneath `raw/`. It chunks
and extracts using the configured provider, displays candidates, and lets the
user approve all or individual facts. Approved facts are appended with
`agent="cli-ingest"` and confidence 1.0. Resolve/compile is still required before
they are served.

## Persistent service

`agent service install` generates a platform wrapper:

| Platform | Mechanism | Restarts on failure | Starts at |
|---|---|---|---|
| Linux | systemd user unit | yes (`Restart=on-failure`) | boot (with `loginctl enable-linger`) |
| macOS | launchd LaunchAgent | yes (`KeepAlive`) | login |
| Windows | Startup-folder VBS script | no | login |

Each wrapper runs `lorekeep agent watch` and pins `LOREKEEP_HOME` to the home
resolved at install time, quoting it so paths with spaces survive shell
tokenisation. `init` installs this wrapper by default (`--no-watch` skips it).
Status delegates to the platform service manager or checks the script file.
Re-run `agent service install` when the desired home or command changes.

**Install from outside the repo** to avoid dev-mode resolving `LOREKEEP_HOME`
to `.lorekeep/` — run `cd ~ && lorekeep agent service install` or set
`LOREKEEP_HOME` explicitly. On Linux, enable lingering (`loginctl enable-linger
<user>`) so the service starts at boot without requiring an interactive login.

## Error resilience

Each loop body has a broad error boundary: unexpected errors are logged, shown,
and followed by the normal interval sleep. Individual wiring, import, backup,
compile, transcript, resolve, and wiki operations also isolate expected failure
so one integration does not stop all maintenance.

## Not implemented

The following belong in the roadmap, not the current daemon description:

- nightly semantic reconcile/lint;
- weekly suggestions/digests;
- hourly batch import schedule;
- autonomous schema evolution;
- provider-backed gap filling without user review; and
- a distributed action queue across devices.

## Related

- [Pipeline](pipeline.md)
- [Journal](journal.md)
- [Import guide](../guides/import.md)
- [Runtime logging](../guides/runtime-logging.md)
- [Roadmap](../ROADMAP.md)
