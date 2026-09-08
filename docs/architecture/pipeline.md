# Pipeline

Lorekeep has one durable graph publication format and three ways knowledge can
arrive: raw compile, session capture/import into raw, and structured journal
proposals. The CLI composes these paths without allowing MCP writes to mutate
the served graph in place.

## Current flow

```text
PATH A — handwritten/imported/captured Markdown
  raw/<ns>/*.md
      → ingest chunks
      → extract candidates (LLM + cache)
      → normalize/validate/deduplicate
      → atomic facts.jsonl + manifest

PATH B — structured proposals
  MCP propose_change/review_note or agent ingest
      → locked pending/<ns>/journal.jsonl append
      → confidence/schema merge against current graph
      → normalize/validate/deduplicate
      → atomic facts.jsonl + manifest

PROJECTION
  final facts.jsonl → deterministic wiki Markdown
```

`lorekeep compile` runs Path A, then replays/merges Path B, then generates one
wiki from the final graph. `lorekeep resolve` runs only Path B against the
existing graph.

## Raw ingest

`compile/ingest.py` recursively reads real Markdown files under `raw/`. It skips
symlinks that resolve outside the raw root, derives namespace from the first
relative directory, and emits bounded chunks with one-based line ranges.

Each `DocChunk.src` is `path:start_line`. That value is attached to extracted
facts for audit and used to map chunk hashes to fact ids in the manifest.

## Extraction and cache

For each chunk, `compile/extract.py` sends:

- a stable system prefix containing stock/custom schema, human-readable content
  rules, the configured lowercase ISO 639-1 `compile.language` (default `en`),
  temporal rules, and the node-vs-attribute altitude rule; and
- only chunk text as the user message, maximizing provider prefix-cache reuse.

The provider must emit `nodes`, `edges`, and `aliases`. Parsing repairs common
fenced/trailing-comma/prose-wrapped JSON variants, then validates facts through
Pydantic and stamps namespace/source outside the model response.

Extraction-cache identity incorporates content and compiler contract, including
schema/prompt/language/model. A hit returns the prior candidate output without a
provider call. The cache is saved after the chunk loop even when some chunks
fail.

### Parallel extraction and streaming flush

Extraction runs in parallel via `ThreadPoolExecutor` with `compile.max_workers`
(default 4; set to 1 for sequential). When `max_workers > 1`, all chunks are
submitted and results are collected as they complete via `as_completed()`.
`ExtractionCache` and `FakeProvider` use `threading.Lock` for concurrent access.

Every `compile.flush_interval` completed chunks (default 10; 0 disables), an
intermediate resolve + atomic write produces a visible `facts.jsonl` so the serve
layer sees live graph updates during compile. Each flush applies `prev_aliases`
graph dedup so intermediate graphs have no duplicate entities. The final resolve
+ write overwrites with deterministic edge IDs.

## Candidate resolve

`compile.resolve.resolve` performs deterministic graph cleanup:

1. reject unknown node types when schema is present;
2. build aliases from extracted name variants, normalized ids, explicit
   mappings, **and graph-native `same_as` edges** — a union-find data structure
   collapses same-entity declarations into canonical entities, handling
   conflicting directions, multi-target chains, and cycles deterministically;
3. canonicalize ids by lowercase and separator normalization while preserving
   Unicode diacritics;
4. merge duplicate nodes in stable input order, unioning sources/namespaces and
   enriching summary/description prose deterministically;
5. rewrite edge endpoints to canonical ids;
6. immediately quarantine dangling endpoints, self-loops, unknown/invalid edge
   types/endpoints; and
7. coalesce logical edges by type + endpoints + validity window, then assign
   deterministic edge ids.

**Entity merge decisions persist across recompiles.** `compile_graph()` loads
`merged_ids` from the previous `facts.jsonl` (via `_load_prev_aliases()` in
`cli.py`) and passes them as `prev_aliases` to `resolve()`, so manual and
LLM-detected merges are not lost when rebuilding from `raw/`.

**Orphan-quarantine decisions persist the same way** — `_load_prev_quarantine()`
+ `_apply_prev_quarantine()` carry `props.quarantined_at`/`quarantined_reason`
forward across a full recompile. See
[Agent: Quarantine](agent.md#quarantine-266).

There is no pending retry queue for dangling extracted edges. Correct the source
or add the missing node and recompile.

## Graph and manifest publication

The writer sorts facts by `(kind, type, id)`, serializes compact JSON with sorted
keys, and stages sibling temp files before `os.replace`. A lazy-reloading MCP
reader therefore sees either the complete old graph or complete new graph.

Compile writes a provisional manifest to calculate the final facts hash, then
publishes the final manifest with:

- deterministic chunk-hash/schema-version run id and final facts hash;
- compile timestamp;
- chunk→fact map;
- chunk errors and candidate quarantine;
- fact/journal counts; and
- human-readable content-quality metrics.

Only `facts.jsonl` is required to be byte-identical for unchanged effective
inputs. Timestamped manifest metadata and append-only wiki log are operational.

## Journal append

MCP validates operations before writing:

- `create` checks node/edge type and edge endpoints;
- `link` checks required ids/type/props and converts to an edge fact;
- `update` loads a visible current fact and replaces its complete props map; and
- `review_note` creates a low-confidence review node.

The server strips caller namespace, stamps configured server scope, adds
agent/device/id/timestamp metadata, then appends one sorted JSON line under a
cross-process file lock and `fsync`.

## Journal merge

`merge_journals` orders entries deterministically with node entries before edges,
then applies:

1. Pydantic fact validation;
2. low-confidence rejection (`<0.5`);
3. schema node/edge endpoint validation;
4. agent/device/confidence/time provenance stamp;
5. node merge or queued edge merge; and
6. confidence result (`>=0.8` merged, otherwise flagged).

For a newly pending node matching an existing id, incoming ordinary props take
the current merge value; summary/description use deterministic richer/combined
prose rules, and source/namespace sets are unioned. On accepted-journal replay
after a new raw compile, `prefer_existing=True` prevents an old accepted entry
from overwriting the freshly compiled props. This replay rule—not a general
source-priority table—is the implemented protection for rebuilt raw facts.

Journal edges must reference nodes available at that merge point and satisfy
schema endpoint types. Invalid endpoints are quarantined immediately. Logical
duplicate edges are merged by endpoints/type/validity.

## Status updates and replay

After publication, journal status is rewritten atomically using `entry_id` (or
legacy proposal timestamp) under the same per-journal lock:

- accepted high-confidence entries become `merged`;
- medium-confidence entries become `flagged` in watcher auto-resolve (the manual
  command currently marks them merged while recording manifest review); and
- low/invalid entries become `quarantined` in the manual command, while watcher
  auto-resolve currently reports their quarantine count but leaves their journal
  status `pending`.

Startup/compile replay includes `merged` and `flagged` entries so an independently
rebuilt device retains accepted agent facts. Replay bypasses the original
confidence gate only for those accepted statuses and prefers the newly compiled
existing props.

## Error behavior

| Failure | Current behavior |
|---|---|
| Recoverable chunk/provider parse | record error, skip chunk, continue partial compile |
| Fatal provider class | record first error and stop remaining equivalent calls |
| All non-empty chunks fail | CLI reports per-chunk errors and exits non-zero |
| Invalid candidate | quarantine and exclude from facts |
| Invalid/dangling journal edge | quarantine in the current pass; no retry queue |
| Corrupt journal line | log warning, preserve/skip line as appropriate |
| Wiki generation | best-effort; graph publication remains successful |
| Atomic publication failure | old complete target remains available |

## Watcher triggers

The daemon compares raw count/mtime, schema mtime, and journal mtime on each poll.
There is no five-minute/50-entry scheduler and no nightly compile/resolve job.
Lifecycle hooks enqueue bounded device-local metadata. The daemon drains ready
events before its raw snapshot, writes transcript Markdown, and forces compile
in that same cycle; fallback boundaries first wait for their idle grace. See
[Lifecycle capture contracts](agent.md#lifecycle-capture-contracts). MCP facts
become visible after publication and lazy reload.

## Related

- [Data model](data-model.md)
- [Journal](journal.md)
- [Autonomous agent](agent.md)
- [Compile guide](../guides/compile.md)
