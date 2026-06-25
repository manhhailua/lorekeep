# .lorekeep Data-Home Consolidation + Backup CLI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consolidate all dev-mode data under `.lorekeep/` (mirroring `LOREKEEP_HOME`) and add a manual `lorekeep backup` command that pushes the data home to a private backup git repo.

**Architecture:** `paths.py` dev branch rewrites raw/graph/schema/cache/pending to live under `cwd/.lorekeep/` and exposes a new `home` key. A new `lorekeep.backup` module wraps git subprocess calls (`init`/`add`/`commit`/`push`) operating on that home. The main lorekeep repo keeps `.lorekeep/*` gitignored; the backup repo is a separate `git init` inside `home` with its own `.gitignore` that excludes the secret `config.yaml` and regenerable artifacts.

**Tech Stack:** Python 3.11+, Typer, stdlib `subprocess` + `datetime`, pytest, git CLI (required at runtime for backup).

## Global Constraints

- Determinism is a hard requirement (CLAUDE.md) — do not alter writer/extract sort or cache behavior.
- Conventional Commits enforced (`feat|fix|refactor|docs|...`); merge commits exempt.
- API keys never in committed files — `config.yaml` (which may hold an inline key) must stay gitignored in both the main repo and the backup repo.
- Tests run offline: set `LOREKEEP_PROVIDER=fake` and `LOREKEEP_*` path envs; never hit a real LLM.
- Git workflow is PR-only — work on branch `feat/dotdir-backup` (already created).

---

### Task 1: Rewrite `paths.py` dev block + add `home` key (TDD)

**Files:**
- Modify: `src/lorekeep/paths.py:16-66`
- Test: `tests/test_paths.py` (rewrite)

**Interfaces:**
- Produces: `resolve_paths()` now returns a dict including `"home": Path` — the directory holding `raw`/`graph`/`cache`. In dev = `cwd/.lorekeep/`; `LOREKEEP_HOME` = the home path; XDG = `user_data_dir("lorekeep")`.

- [ ] **Step 1: Rewrite the failing tests**

Replace the entire contents of `tests/test_paths.py` with:

```python
from pathlib import Path
from lorekeep.paths import resolve_paths


def test_dev_mode_via_lorekeep_marker(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".lorekeep").mkdir()
    p = resolve_paths()
    assert p["home"] == tmp_path / ".lorekeep"
    assert p["config"] == tmp_path / ".lorekeep" / "config.yaml"
    assert p["cache"] == tmp_path / ".lorekeep" / "cache.json"
    assert p["raw"] == tmp_path / ".lorekeep" / "raw"
    assert p["out"] == tmp_path / ".lorekeep" / "graph"
    assert p["schema"] == tmp_path / ".lorekeep" / "schema.json"
    assert p["pending"] == tmp_path / ".lorekeep" / "pending"


def test_lorekeep_home_overrides_dev(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".lorekeep").mkdir()
    home = tmp_path / "myhome"
    monkeypatch.setenv("LOREKEEP_HOME", str(home))
    p = resolve_paths()
    assert p["home"] == home
    assert p["config"] == home / "config.yaml"
    assert p["raw"] == home / "raw"
    assert p["schema"] == home / "schema.json"


def test_xdg_default(tmp_path: Path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)
    monkeypatch.delenv("LOREKEEP_HOME", raising=False)
    monkeypatch.delenv("LOREKEEP_DEV", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    p = resolve_paths()
    assert p["home"] == tmp_path / "xdg-data" / "lorekeep"
    assert p["config"] == tmp_path / "xdg-config" / "lorekeep" / "config.yaml"
    assert p["raw"] == tmp_path / "xdg-data" / "lorekeep" / "raw"
    assert p["schema"] == tmp_path / "xdg-data" / "lorekeep" / "schema.json"


def test_explicit_env_overrides_everything(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".lorekeep").mkdir()                 # dev mode active
    monkeypatch.setenv("LOREKEEP_RAW", "/custom/raw")
    monkeypatch.setenv("LOREKEEP_OUT", "/custom/graph")
    monkeypatch.setenv("LOREKEEP_CONFIG", "/custom/config.yaml")
    p = resolve_paths()
    assert p["home"] == tmp_path / ".lorekeep"
    assert p["raw"] == Path("/custom/raw")
    assert p["out"] == Path("/custom/graph")
    assert p["config"] == Path("/custom/config.yaml")
    assert p["schema"] == tmp_path / ".lorekeep" / "schema.json"
```

Note: the old `test_dev_mode_via_raw_marker` is intentionally removed — a bare `raw/` at cwd no longer triggers dev mode (only `.lorekeep/` or `LOREKEEP_DEV=1` do).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_paths.py -v`
Expected: FAIL — `raw` resolves to `tmp_path/raw` (old), not `tmp_path/.lorekeep/raw`; `home` key missing.

- [ ] **Step 3: Rewrite `paths.py`**

Replace the whole file body (the `_dev_marker` helper + `resolve_paths`) with:

```python
"""Path resolution with 4-tier precedence (high -> low).

1. explicit per-path env (LOREKEEP_RAW/OUT/CACHE/SCHEMA/CONFIG) - tests + power users
2. LOREKEEP_HOME -> unified <home>/{config.yaml,schema.json,raw,graph,cache.json}
3. dev mode (.lorekeep/ in CWD, or LOREKEEP_DEV=1) -> <cwd>/.lorekeep/{...}
4. default -> XDG (platformdirs): config + data dirs

Pure: no I/O, no side effects. Fully testable.
"""
from __future__ import annotations

import os
from pathlib import Path


def _dev_marker(cwd: Path) -> bool:
    return (cwd / ".lorekeep").is_dir()


def resolve_paths() -> dict[str, Path]:
    cwd = Path.cwd()
    home_env = os.environ.get("LOREKEEP_HOME")
    dev = os.environ.get("LOREKEEP_DEV") == "1" or _dev_marker(cwd)

    if home_env:
        home = Path(home_env).expanduser()
        config = home / "config.yaml"
        cache = home / "cache.json"
        raw = home / "raw"
        out = home / "graph"
        schema = home / "schema.json"
        pending = home / "pending"
    elif dev:
        home = cwd / ".lorekeep"
        config = home / "config.yaml"
        cache = home / "cache.json"
        raw = home / "raw"
        out = home / "graph"
        schema = home / "schema.json"
        pending = home / "pending"
    else:
        from platformdirs import user_config_dir, user_data_dir
        home = Path(user_data_dir("lorekeep"))
        config = Path(user_config_dir("lorekeep")) / "config.yaml"
        cache = home / "cache.json"
        raw = home / "raw"
        out = home / "graph"
        schema = home / "schema.json"
        pending = home / "pending"

    def override(env_name: str, current: Path) -> Path:
        v = os.environ.get(env_name)
        return Path(v).expanduser() if v else current

    return {
        "home": home,
        "raw": override("LOREKEEP_RAW", raw),
        "out": override("LOREKEEP_OUT", out),
        "cache": override("LOREKEEP_CACHE", cache),
        "schema": override("LOREKEEP_SCHEMA", schema),
        "config": override("LOREKEEP_CONFIG", config),
        "pending": override("LOREKEEP_PENDING", pending),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_paths.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Run full suite to catch regressions**

Run: `uv run pytest -q`
Expected: PASS (~140 tests). If a test outside `test_paths.py` fails because it assumed root `raw/`/`graph/` without pinning env, fix it to set `LOREKEEP_HOME` or `LOREKEEP_RAW/OUT/SCHEMA` (follow the pattern in `tests/test_compile_cli.py:9-15`).

- [ ] **Step 6: Commit**

```bash
git add src/lorekeep/paths.py tests/test_paths.py
git commit -m "refactor(paths): consolidate dev data under .lorekeep and expose home"
```

---

### Task 2: `lorekeep.backup` module (TDD)

**Files:**
- Create: `src/lorekeep/backup.py`
- Test: `tests/test_backup.py`

**Interfaces:**
- Produces:
  - `BACKUP_GITIGNORE: str` — the `.gitignore` body written into the data home.
  - `BackupError(RuntimeError)` — raised on any git failure.
  - `init_backup(home: Path, remote: str) -> None` — init git repo in `home`, write `.gitignore`, configure `origin` remote, commit, `push -u origin HEAD`.
  - `backup(home: Path) -> bool` — stage all, commit if anything staged, push; return `True` if a commit was made, `False` if nothing to commit. Raise `BackupError` if `home` is not a git repo.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_backup.py`:

```python
import subprocess
from pathlib import Path

import pytest

from lorekeep.backup import BACKUP_GITIGNORE, BackupError, backup, init_backup


def _bare_remote(tmp_path: Path) -> str:
    """A local bare repo usable as a push remote (file:// not required for path remote)."""
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True)
    return str(bare)


def _tracked(home: Path) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=home, capture_output=True, text=True, check=True
    ).stdout
    return out.split()


def _log(home: Path) -> str:
    return subprocess.run(
        ["git", "log", "--oneline"], cwd=home, capture_output=True, text=True, check=True
    ).stdout


def test_init_backup_creates_repo_gitignore_and_remote(tmp_path: Path):
    home = tmp_path / "home"
    (home / "raw" / "ns").mkdir(parents=True)
    (home / "raw" / "ns" / "a.md").write_text("# a")
    remote = _bare_remote(tmp_path)
    init_backup(home, remote)
    assert (home / ".git").is_dir()
    assert (home / ".gitignore").read_text() == BACKUP_GITIGNORE
    refs = subprocess.run(
        ["git", "ls-remote", remote], capture_output=True, text=True, check=True
    ).stdout
    assert refs.strip() != ""  # initial commit landed on the remote


def test_backup_commits_and_pushes_new_changes(tmp_path: Path):
    home = tmp_path / "home"
    remote = _bare_remote(tmp_path)
    init_backup(home, remote)
    (home / "raw" / "ns").mkdir(parents=True)
    (home / "raw" / "ns" / "b.md").write_text("# b")
    made = backup(home)
    assert made is True
    assert "backup " in _log(home)


def test_backup_skips_when_nothing_staged(tmp_path: Path):
    home = tmp_path / "home"
    remote = _bare_remote(tmp_path)
    init_backup(home, remote)
    made = backup(home)
    assert made is False


def test_backup_raises_when_not_a_repo(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    with pytest.raises(BackupError):
        backup(home)


def test_backup_never_tracks_secret_or_regenerable(tmp_path: Path):
    home = tmp_path / "home"
    remote = _bare_remote(tmp_path)
    init_backup(home, remote)
    (home / "config.yaml").write_text("api_key: sk-leaked\n")
    (home / "graph").mkdir()
    (home / "graph" / "facts.jsonl").write_text("{}")
    (home / "graph" / "manifest.json").write_text("{}")
    (home / "cache.json").write_text("{}")
    backup(home)
    tracked = _tracked(home)
    assert "config.yaml" not in tracked
    assert "graph/facts.jsonl" not in tracked
    assert "graph/manifest.json" not in tracked
    assert "cache.json" not in tracked
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_backup.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lorekeep.backup'`.

- [ ] **Step 3: Implement `src/lorekeep/backup.py`**

```python
"""Manual backup of the lorekeep data home to a private git repo.

The backup repo lives inside the data home (`.lorekeep/` in dev mode). It
tracks the curated source (`raw/`) and the schema; it ignores the secret
`config.yaml` and the regenerable compile outputs.
"""
from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

BACKUP_GITIGNORE = """\
config.yaml
graph/facts.jsonl
graph/manifest.json
cache.json
"""


class BackupError(RuntimeError):
    """Raised when a git operation during backup fails."""


def _git(args: list[str], cwd: Path) -> str:
    """Run git in `cwd`, returning stdout. Raise BackupError on non-zero exit.

    Inline user.email/user.name so commits work without a global git identity
    (important in CI and fresh machines).
    """
    proc = subprocess.run(
        [
            "git",
            "-c", "user.email=lorekeep@backup.local",
            "-c", "user.name=lorekeep backup",
            *args,
        ],
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise BackupError(
            f"git {' '.join(args)} failed (exit {proc.returncode}): {proc.stderr.strip()}"
        )
    return proc.stdout.strip()


def _commit(home: Path, prefix: str) -> bool:
    """Stage all and commit with an ISO-8601 UTC message. Return True if committed."""
    _git(["add", "-A"], home)
    staged = _git(["diff", "--cached", "--name-only"], home)
    if not staged:
        return False
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _git(["commit", "-q", "-m", f"{prefix} {ts}"], home)
    return True


def init_backup(home: Path, remote: str) -> None:
    """Init a git repo in `home`, write .gitignore, set origin, commit, push.

    Idempotent: safe to re-run; rewrites the .gitignore and points origin at `remote`.
    """
    home.mkdir(parents=True, exist_ok=True)
    if not (home / ".git").is_dir():
        _git(["init", "-q"], home)
    (home / ".gitignore").write_text(BACKUP_GITIGNORE)
    remotes = _git(["remote"], home).split()
    if "origin" in remotes:
        _git(["remote", "set-url", "origin", remote], home)
    else:
        _git(["remote", "add", "origin", remote], home)
    _commit(home, "backup init")
    _git(["push", "-u", "origin", "HEAD"], home)


def backup(home: Path) -> bool:
    """Commit + push pending changes. Raise BackupError if not a backup repo."""
    if not (home / ".git").is_dir():
        raise BackupError(
            f"not a backup repo at {home} — run `lorekeep backup --init <remote>` first"
        )
    committed = _commit(home, "backup")
    if committed:
        _git(["push"], home)
    return committed
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_backup.py -v`
Expected: PASS (5 tests). Requires `git` on PATH (CI and dev both have it).

- [ ] **Step 5: Commit**

```bash
git add src/lorekeep/backup.py tests/test_backup.py
git commit -m "feat(backup): add data-home git backup module"
```

---

### Task 3: Wire `lorekeep backup` CLI command (TDD)

**Files:**
- Modify: `src/lorekeep/cli.py` (add `backup` command after `init`)
- Test: `tests/test_backup_cli.py`

**Interfaces:**
- Consumes: `resolve_paths()["home"]` (Task 1), `lorekeep.backup.{init_backup, backup, BackupError}` (Task 2).
- Produces: CLI command `lorekeep backup [--init <remote-url>]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_backup_cli.py`:

```python
import subprocess
from pathlib import Path

from typer.testing import CliRunner

from lorekeep.cli import app

runner = CliRunner()


def _bare_remote(tmp_path: Path) -> str:
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True)
    return str(bare)


def test_backup_init_then_backup_round_trip(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("LOREKEEP_HOME", str(home))
    remote = _bare_remote(tmp_path)

    init_result = runner.invoke(app, ["backup", "--init", remote])
    assert init_result.exit_code == 0, init_result.stdout
    assert (home / ".git").is_dir()

    (home / "raw" / "ns").mkdir(parents=True)
    (home / "raw" / "ns" / "x.md").write_text("# x")
    result = runner.invoke(app, ["backup"])
    assert result.exit_code == 0, result.stdout
    assert "pushed" in result.stdout


def test_backup_when_nothing_to_commit(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("LOREKEEP_HOME", str(home))
    remote = _bare_remote(tmp_path)
    runner.invoke(app, ["backup", "--init", remote])

    result = runner.invoke(app, ["backup"])
    assert result.exit_code == 0, result.stdout
    assert "nothing to commit" in result.stdout


def test_backup_without_init_fails_cleanly(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("LOREKEEP_HOME", str(home))
    result = runner.invoke(app, ["backup"])
    assert result.exit_code == 1, result.stdout
    assert "backup failed" in result.stdout
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_backup_cli.py -v`
Expected: FAIL — `Error: No such command 'backup'`.

- [ ] **Step 3: Add the `backup` command to `cli.py`**

Insert immediately after the `init` command (after its final `typer.echo("  (existing config/schema preserved)")` line and before `@app.command("import")`):

```python
@app.command()
def backup(
    init_remote: str = typer.Option(
        None, "--init", help="remote URL; sets up the backup repo + initial push"
    ),
) -> None:
    """Commit + push the data home to your private backup repo."""
    from lorekeep.backup import BackupError, backup as backup_home, init_backup

    home = resolve_paths()["home"]
    try:
        if init_remote:
            init_backup(home, init_remote)
            typer.echo(f"backup: repo ready at {home} -> {init_remote}")
        else:
            committed = backup_home(home)
            if committed:
                typer.echo(f"backup: pushed changes from {home}")
            else:
                typer.echo(f"backup: nothing to commit in {home}")
    except BackupError as exc:
        typer.echo(f"backup failed: {exc}")
        raise typer.Exit(code=1)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_backup_cli.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/lorekeep/cli.py tests/test_backup_cli.py
git commit -m "feat(cli): add lorekeep backup command"
```

---

### Task 4: Migrate working-tree data + update main-repo `.gitignore`

**Files:**
- Modify: `.gitignore`
- Move: `raw/backend/` → `.lorekeep/raw/backend/`
- Move: `graph/{facts.jsonl,manifest.json}` → `.lorekeep/graph/`
- Move: `graph/schema.json` → `.lorekeep/schema.json`
- Remove: now-empty root `raw/`, `graph/`

This task has no unit tests — it is a one-time data migration of the working tree. Verify by running the CLI end-to-end.

- [ ] **Step 1: Move existing data into `.lorekeep/`**

```bash
mkdir -p .lorekeep/raw .lorekeep/graph
[ -f raw/backend/payments.md ] && mv raw/backend/payments.md .lorekeep/raw/backend/ 2>/dev/null; mkdir -p .lorekeep/raw/backend
[ -d raw/backend ] && cp -n raw/backend/* .lorekeep/raw/backend/ 2>/dev/null || true
[ -f graph/facts.jsonl ] && mv graph/facts.jsonl .lorekeep/graph/
[ -f graph/manifest.json ] && mv graph/manifest.json .lorekeep/graph/
[ -f graph/schema.json ] && mv graph/schema.json .lorekeep/schema.json
rm -rf raw graph
```

If any source path is absent (e.g. `graph/facts.jsonl` was not regenerated), the `[ -f ... ]` guard skips it. After this, only `.lorekeep/` should hold data.

- [ ] **Step 2: Untrack the old committed `graph/schema.json`**

```bash
git rm --cached -r graph 2>/dev/null || true
git rm --cached graph/schema.json 2>/dev/null || true
```

(`graph/schema.json` is regenerable from `DEFAULT_SCHEMA` via `lorekeep init`; the working copy already moved to `.lorekeep/schema.json`.)

- [ ] **Step 3: Update `.gitignore`**

Remove these two lines (root `graph/` no longer exists):
```
graph/facts.jsonl
graph/manifest.json
```
The existing `.lorekeep/*` + `!.lorekeep/config.yaml.example` rules already cover the new layout — no other change needed. Confirm with:

```bash
git check-ignore -v .lorekeep/config.yaml .lorekeep/raw/backend/payments.md 2>/dev/null
```
Expected: `.lorekeep/config.yaml` matches a `.gitignore` rule; `.lorekeep/raw/...` is ignored too (whole `.lorekeep/*` ignored in the main repo — that's intended; tracking happens in the backup repo).

- [ ] **Step 4: Verify end-to-end (offline)**

```bash
rm -f .lorekeep/cache.json
LOREKEEP_PROVIDER=fake uv run lorekeep compile
LOREKEEP_PROVIDER=fake uv run lorekeep check
LOREKEEP_PROVIDER=fake uv run lorekeep doctor
```
Expected: `compile` reports N nodes/edges; `check` ok; `doctor` `all checks passed`.

- [ ] **Step 5: Run full suite**

Run: `uv run pytest -q`
Expected: PASS (~140 tests).

- [ ] **Step 6: Commit**

```bash
git add .gitignore
git commit -m "refactor: retire root raw/graph, data now lives under .lorekeep"
```

---

### Task 5: Update docs (`CLAUDE.md`)

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update the commands table**

Add a row to the CLI commands table (after the `doctor` row):

```
| `backup [--init <remote-url>]` | Commit + push `.lorekeep/` to your private backup git repo |
```

- [ ] **Step 2: Update the Path resolution section**

Replace the dev-mode description. The bullet currently reads roughly:
> **dev mode** (`.lorekeep/` or `raw/` present in CWD, or `LOREKEEP_DEV=1`; auto-detected in a source checkout) → XDG

Change to:
> **dev mode** (`.lorekeep/` present in CWD, or `LOREKEEP_DEV=1`; auto-detected in a source checkout) — all data lives under `cwd/.lorekeep/` (`config.yaml`, `schema.json`, `raw/`, `graph/`, `cache.json`), mirroring the `LOREKEEP_HOME` layout

- [ ] **Step 3: Add a short Backup subsection**

Under the Configuration & keys section, add:

```markdown
### Backup

`lorekeep backup --init <remote-url>` initializes a **separate** git repo inside the data home (`.lorekeep/` in dev) and pushes it to your private `<remote-url>`. Subsequent `lorekeep backup` calls commit and push changes. The backup repo tracks `raw/` and `schema.json`; it ignores `config.yaml` (may hold an API key) and the regenerable `graph/facts.jsonl`, `graph/manifest.json`, `cache.json`. This is independent of the lorekeep tool repository.
```

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: document .lorekeep dev layout and backup command"
```

---

## Self-Review

- **Spec coverage:** spec §1 (paths) → Task 1. §2 (main .gitignore + untrack schema) → Task 4. §3 (backup repo) → Task 2 + Task 3. §4 (`lorekeep backup`) → Task 3. §5 (migration) → Task 4. §6 (docs) → Task 5. All covered.
- **Placeholder scan:** none; every code step shows full code.
- **Type consistency:** `home: Path` consistent across Task 1 (produced), Task 2/3 (consumed). `init_backup(home, remote) -> None`, `backup(home) -> bool`, `BackupError` names match between Task 2 and Task 3.
