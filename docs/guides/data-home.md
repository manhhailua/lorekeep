# Data home & path resolution

Lorekeep resolves its data home with a 4-tier precedence (high → low). All commands (`compile`, `serve`, `doctor`, …) use the same resolution, in `src/lorekeep/paths.py` — pure logic, no I/O.

```
1. explicit per-path env   LOREKEEP_RAW / LOREKEEP_OUT / LOREKEEP_CACHE / LOREKEEP_SCHEMA / LOREKEEP_CONFIG
2. LOREKEEP_HOME            → <home>/{config.yaml, schema.json, raw/, graph/, cache.json}
3. dev mode                 .lorekeep/ or raw/ present in CWD, or LOREKEEP_DEV=1 → repo layout
4. XDG (default)            ~/.config/lorekeep (config) + ~/.local/share/lorekeep (data)
```

`lorekeep init` bootstraps whichever home resolves, writing default `config.yaml` + `schema.json` and creating `raw/` + `graph/` (it preserves existing config/schema).

## Installed use (recommended)

```bash
uvx lorekeep init          # bootstrap ~/.config/lorekeep + ~/.local/share/lorekeep
# add docs under ~/.local/share/lorekeep/raw/<ns>/
uvx lorekeep compile
uvx lorekeep mcp add --agent claude --ns <ns>
uvx lorekeep doctor
```

## Local dev (repo co-located data)

From a Lorekeep source checkout (`.lorekeep/` present → auto dev mode):

```bash
uv run lorekeep compile      # reads repo raw/, writes repo graph/
uv run lorekeep serve
```

Force dev mode anywhere: `LOREKEEP_DEV=1 uv run lorekeep …`.

## Custom knowledge base

```bash
LOREKEEP_HOME=~/kb-work uvx lorekeep init
LOREKEEP_HOME=~/kb-work uvx lorekeep compile
```

## Per-path overrides (power users / tests)

Pin individual paths without changing the home:

```bash
LOREKEEP_RAW=./my-raw LOREKEEP_OUT=./my-graph uv run lorekeep compile
```

This is how the test suite isolates each run.

## Related

- [Compiling the graph](compile.md)
- [Serving the graph to agents](serve.md)
- Architecture: [overview](../architecture/overview.md)
