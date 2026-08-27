# Compiling and resolving the knowledge graph

Lorekeep has two related write operations:

- `compile` rebuilds the graph from raw Markdown with cached LLM extraction,
  replays/merges journals, and regenerates the wiki;
- `resolve` merges journal changes into the existing graph without an LLM call.

Use `compile` after raw/schema changes and `resolve` when only agent proposals
changed.

## Add raw documents

Place Markdown under `raw/<namespace>/` in the resolved data home:

```text
raw/
├── me/profile.md
├── backend/payments.md
└── public/glossary.md
```

The first path segment becomes the extraction namespace. `public` is visible to
every scope; other namespaces are visible only when the MCP server was configured
for them.

## Configure a provider

```bash
lorekeep config set provider.model deepseek/deepseek-chat
lorekeep config set provider.api_key_env DEEPSEEK_API_KEY
export DEEPSEEK_API_KEY=...
```

Model values require a LiteLLM provider prefix. Native providers normally need
no `api_base`. For a strict-local setup:

```yaml
provider:
  model: ollama/llama3
  timeout_seconds: 120
  max_retries: 2
```

Use the [validated config example](../../.lorekeep/config.yaml.example) for more
providers and daemon settings.

## Choose the generated content language

LLM-extracted names, summaries, and descriptions default to English (`en`),
regardless of the source document's language. Set one language for the compiled
graph with a lowercase ISO 639-1 code:

```bash
lorekeep config set compile.language vi
lorekeep compile
```

Lorekeep translates generic labels and explanatory prose while preserving raw
Markdown, proper nouns, stable IDs, schema keys, code symbols, and product names.
This setting affects `compile` and `agent ingest`; the deterministic wiki simply
renders the resulting facts and does not translate them itself.

## Run the all-in-one compile

Installed tool:

```bash
lorekeep compile
```

Source checkout:

```bash
uv run lorekeep compile
```

By default `lorekeep compile` runs in the **background** when invoked from an
interactive terminal. It touches a `.compile-requested` sentinel file and
ensures the daemon is running; the daemon detects the sentinel on its next poll
(default 60&nbsp;s) and runs the full compile → resolve → self-heal → wiki →
backup chain. This lets you continue editing immediately — open the wiki in
Obsidian/Tolaria and watch new pages appear as the compile progresses.

For synchronous execution (CI, scripts, non-interactive shells), pass
`--foreground` (or `-f`):

```bash
lorekeep compile --foreground
```

Non-interactive mode always runs synchronously even without the flag.

The command performs these stages:

1. **Ingest** — parse each Markdown file into bounded `DocChunk` values carrying
   `path:line` provenance.
2. **Extract** — call the provider with a schema-constrained prompt for nodes,
   edges, aliases, temporal fields, grounded summaries, and relationship
   descriptions.
3. **Resolve candidates** — normalize aliases/ids, merge complementary props,
   validate schema/endpoints, and quarantine malformed or dangling facts.
4. **Publish graph** — atomically write sorted `graph/facts.jsonl` and
   `graph/manifest.json`.
5. **Replay journals** — merge pending and previously accepted journal entries
   so a raw rebuild does not erase agent-contributed facts.
6. **Generate wiki** — project the final graph once into the human-readable
   `wiki/` vault.

`facts.jsonl` is sorted and byte-identical for unchanged effective inputs.
`manifest.json` contains run diagnostics and a fresh `compiled_at`, while
`wiki/log.md` is append-only, so those files are not the determinism target.

## Incremental extraction cache

The extraction cache key includes normalized chunk content, the complete schema
contract, prompt version, configured output language, and model. An unchanged
chunk returns cached extraction output and makes no new provider request. Editing
one chunk or changing the schema, language, or model invalidates only the relevant
cache entries.

Do not delete `cache.json` merely to switch providers; the model fingerprint
already triggers the required extraction.

## Parallel extraction and streaming flush

Extraction runs in parallel across chunks via `ThreadPoolExecutor` with
`compile.max_workers` (default 4; set to 1 for sequential). Each chunk's
extraction call is independent, so parallelism scales linearly with the number of
distinct chunks up to the worker count.

Every `compile.flush_interval` completed chunks (default 10; 0 disables flush),
an intermediate resolve + atomic write produces a visible `facts.jsonl` so the
serve layer and MCP clients see live graph updates during a long compile. The
final resolve + write overwrites the intermediate graph with deterministic edge
IDs.

Configure both in `config.yaml`:

```yaml
compile:
  max_workers: 4      # 1 = sequential, up to 32
  flush_interval: 10   # 0 = no streaming flush (all-at-once at end)
```

## Human-readable content quality

Stock schema v5 asks the existing compile call for:

- node display name/title and concise summary;
- optional grounded description;
- a concrete description for each relationship; and
- human forward/inverse relation labels supplied by the schema.

Wiki generation itself is deterministic and does not call an LLM. It can only
render prose present in `facts.jsonl`, so old graphs need schema upgrade plus a
recompile—not just `lorekeep wiki`—to gain richer content.

The manifest records `content_quality` coverage for names, summaries,
descriptions, relationship explanations, generic relations, and duplicate
display labels. Low coverage produces CLI warnings but does not discard an
otherwise valid fact.

## Partial and total failures

Extraction is skip-and-log per chunk:

- a recoverable chunk failure is recorded in `manifest.errors`; other chunks
  continue and the partial graph remains valid;
- an authentication/network-style fatal provider error short-circuits remaining
  chunks to avoid repeated identical failures;
- if non-empty input produces no nodes because all chunks failed, the CLI exits
  non-zero;
- malformed facts and invalid/dangling edges are quarantined during resolve.

Inspect the CLI output, `manifest.json`, and runtime log. Use `lorekeep support`
when preparing a bug report; do not attach raw documents or configuration.

## Merge journals without recompiling raw docs

```bash
lorekeep resolve
```

Resolve loads the current graph plus every `pending/**/journal.jsonl`, orders
entries deterministically, applies schema validation and confidence gates, then
atomically writes the graph and updates journal statuses:

| Confidence | Behavior |
|---|---|
| `>= 0.8` | Merge |
| `>= 0.5` and `< 0.8` | Merge and flag in the manifest for review |
| `< 0.5` | Quarantine; do not add to the graph |

Accepted facts retain agent, device, confidence, and proposal-time provenance.
Wiki regeneration runs only when a merge/flag changes visible facts.

The watcher polls journals and invokes the same merge logic after their mtime
changes. Its current journal-status bookkeeping differs from the manual command
for medium/rejected entries; see [Pipeline: status updates and replay](../architecture/pipeline.md#status-updates-and-replay).
There is no separate 50-entry/five-minute scheduler.

## Conversational ingest

For one raw file that you want to review interactively before journaling:

```bash
lorekeep agent ingest raw/backend/payments.md
lorekeep resolve
```

`agent ingest` calls the configured LLM, shows extracted facts, asks for approval
(unless `--yes`), and writes approved facts to the namespace journal with
confidence `1.0`. It does not modify `facts.jsonl` directly.

## Upgrade an older stock schema

```bash
lorekeep schema upgrade --dry-run
lorekeep schema upgrade
lorekeep compile
```

The upgrade preserves a versioned schema backup and is idempotent. Stock
v2/v3/v4 schemas upgrade to schema v5; custom older schemas require explicit
`--force`. Review custom changes before forcing an upgrade.

## Validate

```bash
lorekeep doctor
lorekeep agent lint
```

`doctor` is the structural/install gate: graph, schema, MCP response, and optional
provider ping. `agent lint` is graph-health analysis: contradictions, orphans,
staleness, endpoint issues, and coverage gaps. `agent lint --auto-fix` applies
the currently supported deterministic self-heal operations and regenerates the
wiki when facts change.

Orphans that keep resurfacing every compile can be parked with
`lorekeep quarantine detect --apply`, then triaged later with
`lorekeep quarantine review` — see
[Agent: Quarantine](../architecture/agent.md#quarantine-266).

## Related

- [Pipeline architecture](../architecture/pipeline.md)
- [Journal architecture](../architecture/journal.md)
- [Serving over MCP](serve.md)
- [Browsing the wiki](wiki.md)
- [Data home and paths](data-home.md)
