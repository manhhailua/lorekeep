# Design: Consolidate data home into `.lorekeep/` + manual backup CLI

**Date:** 2026-06-26
**Status:** Approved (approach A)
**Author:** manhpt1

## Goal

1. Production/dev parity: all runtime data lives under one data-home dir (mirrors `LOREKEEP_HOME` layout). In dev mode that dir is `.lorekeep/`.
2. Track `.lorekeep/` contents in a **separate private backup git repo** (not the main lorekeep repo) via a manual `lorekeep backup` command.
3. Keep API key (inline in `config.yaml`) out of git — `config.yaml` stays gitignored everywhere.

## Non-goals

- Automatic periodic push (user runs `lorekeep backup` manually).
- Changing `LOREKEEP_HOME` or XDG behavior (only dev-mode paths change).
- Tracking generated artifacts (`facts.jsonl`, `manifest.json`, `cache.json`) — regenerable via `compile`.

## Current state

- Dev mode (`paths.py`): `config`+`cache` under `cwd/.lorekeep/`, but `raw`/`out`/`schema`/`pending` at `cwd/` root. Split layout.
- Main repo `.gitignore`: `.lorekeep/*` (except `config.yaml.example`); `graph/facts.jsonl`, `graph/manifest.json` ignored; `graph/schema.json` committed.
- `graph/schema.json` == `DEFAULT_SCHEMA` (`defaults.py`) — regenerable, no custom edits.
- `config.yaml` holds inline API key.

## Design

### 1. `paths.py` — dev mode mirrors HOME layout

Add a `home` key to the returned dict = the directory holding `raw`/`graph`/`cache` (the data dir). In dev = `.lorekeep/`; `LOREKEEP_HOME` = base; XDG = `data_dir` (config may live in separate `cfg_dir`, irrelevant to backup). Dev block becomes:

```
home    = cwd / ".lorekeep"
config  = home / "config.yaml"
cache   = home / "cache.json"
raw     = home / "raw"
out     = home / "graph"
schema  = home / "schema.json"
pending = home / "pending"
```

(Matches the existing `LOREKEEP_HOME` branch exactly — `schema` sits at the home root, not under `graph/`.)

`LOREKEEP_HOME` and XDG branches already follow this shape (also expose their `home`). `_dev_marker` unchanged (`.lorekeep/` presence still triggers dev).

### 2. Main repo `.gitignore`

- Keep `.lorekeep/*` + `!.lorekeep/config.yaml.example`.
- Remove stale `graph/facts.jsonl`, `graph/manifest.json` lines (no more root `graph/`).
- `git rm --cached graph/schema.json` (regenerable; fresh clone runs `init`).

### 3. Backup repo inside data home

Separate `git init` inside `home` (`.lorekeep/` in dev). Private remote. Its own `.gitignore`:

```
config.yaml
graph/facts.jsonl
graph/manifest.json
cache.json
```

Tracked: `raw/` (all namespaces, including personal), `schema.json`, the backup `.gitignore` itself.

### 4. New CLI: `lorekeep backup`

- `lorekeep backup --init <remote-url>`:
  - `git init` in `home` (no-op if already a repo).
  - Write `home/.gitignore` (idempotent).
  - `git remote add origin <url>` (error if exists + differs).
  - Initial commit + `git push -u origin main` (or `master` per remote default).
- `lorekeep backup`:
  - `git add -A` in `home`.
  - Commit with message `backup <iso8601-utc>` (skip if nothing staged).
  - `git push`.
  - Surface git errors (no remote, auth, conflicts) as typer errors, exit 1.
- Implemented via `subprocess.run(["git", ...], cwd=home, ...)`; never shell=True.

Operates on `home` from `resolve_paths()`, so works identically in dev / `LOREKEEP_HOME` / XDG.

### 5. Migration of existing data

- Move `raw/backend/payments.md` → `.lorekeep/raw/backend/payments.md`.
- Move `graph/*` → `.lorekeep/graph/*`.
- Remove now-empty root `raw/`, `graph/`.

### 6. Docs

- Update `CLAUDE.md` path-resolution section + commands table (add `backup`).
- Note `lorekeep backup` pushes to user's private backup repo, not the lorekeep tool repo.

## Components touched

| File | Change |
|---|---|
| `src/lorekeep/paths.py` | dev block → `.lorekeep/`; add `home` to result |
| `src/lorekeep/cli.py` | new `backup` command (+ `--init`) |
| `.gitignore` | drop root `graph/` rules |
| `graph/schema.json` | `git rm --cached` (root graph/ retired) |
| `CLAUDE.md` | path section + backup command doc |
| `raw/`, `graph/` (root) | migrate → `.lorekeep/`, remove |

## Testing

- Update `tests/test_paths*.py` for new dev layout + `home` key.
- New `tests/test_backup_cli.py`: `--init` creates repo+remote+gitignore; `backup` commits+pushes (mock `subprocess` / use a temp bare remote); nothing-staged → skip commit; missing remote → exit 1.
- Existing dev-mode tests that assert `cwd/raw` or `cwd/graph` get updated.
- `LOREKEEP_PROVIDER=fake` end-to-end: `init → compile → check → backup --init <tmp-bare> → backup` round-trip.

## Risks

- **Inline key**: `config.yaml` ignored in both repos. If user later adds key elsewhere, backup could leak — `backup` should refuse if it detects `api_key:` with a non-null value in any tracked file (defensive guard, optional).
- **Existing clones** with root `graph/schema.json`: after pull, schema lives under `.lorekeep/graph/`; `init` regenerates if absent.
- **Backup remote default branch** (`main` vs `master`): detect via `git remote show` or let `git push -u` follow remote HEAD.
