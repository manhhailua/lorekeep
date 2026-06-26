# me-Namespace Onboarding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `lorekeep init` create a personal `me` namespace with a profile (interactive prompts on a tty, template otherwise) and set `ns.default=[me, public]`.

**Architecture:** New `src/lorekeep/onboard.py` holds pure helpers (`profile_markdown`, `write_profile`, `update_ns_default`) + an orchestrator (`run_onboarding`) that takes an injected `prompt` callable so interactive logic is testable without a tty. `cli.py init` gains `--no-onboard` / `--force` flags and calls the orchestrator with `interactive = sys.stdin.isatty() and not no_onboard`.

**Tech Stack:** Python 3.11+, Typer, Pydantic (Config), PyYAML, pytest.

## Global Constraints

- Determinism is a hard requirement (n/a — no compile/extract changes).
- Conventional Commits enforced (`feat|fix|refactor|docs|...`); merge commits exempt.
- API keys never in committed files (n/a — onboarding writes only `raw/me/profile.md` + `config.yaml`'s `ns.default`).
- Tests run offline, no real LLM (n/a — onboarding writes raw source, does not compile).
- Git workflow is PR-only — work on branch `feat/onboard-me-namespace` (already created, stacked on `feat/dotdir-backup`).
- `ns.default` update uses `yaml.safe_dump`, which drops comments from a hand-edited `config.yaml` — accepted per spec.

---

### Task 1: `lorekeep.onboard` module (TDD)

**Files:**
- Create: `src/lorekeep/onboard.py`
- Test: `tests/test_onboard.py`

**Interfaces:**
- Produces:
  - `PROFILE_NS = "me"`
  - `profile_markdown(name: str, role: str, what: str, tz: str) -> str`
  - `write_profile(raw_root: Path, md: str) -> Path` — writes `raw_root/"me"/"profile.md"`, parents created.
  - `update_ns_default(config_path: Path, ns_list: list[str]) -> None` — loads Config, sets `ns.default`, rewrites via `yaml.safe_dump`.
  - `run_onboarding(home: Path, config_path: Path, *, interactive: bool, prompt=None, force: bool = False) -> bool` — orchestrator; returns True if profile written, False if skipped.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_onboard.py`:

```python
from pathlib import Path

import yaml

from lorekeep.onboard import (
    PROFILE_NS,
    profile_markdown,
    write_profile,
    update_ns_default,
    run_onboarding,
)


def test_profile_markdown_exact():
    md = profile_markdown("Alice", "Eng", "Backend", "UTC")
    assert md == (
        "# Alice\n\n"
        "- **Role**: Eng\n"
        "- **What I do**: Backend\n"
        "- **Timezone**: UTC\n"
        "\n"
        "Personal namespace — facts about me live here (`raw/me/`).\n"
    )


def test_write_profile_creates_file(tmp_path: Path):
    raw = tmp_path / "raw"
    p = write_profile(raw, "# Alice\n")
    assert p == raw / PROFILE_NS / "profile.md"
    assert p.read_text(encoding="utf-8") == "# Alice\n"


def test_update_ns_default_preserves_rest(tmp_path: Path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "provider:\n"
        "  model: openai/gpt-4o-mini\n"
        "  backend: openai\n"
        "ns:\n"
        "  default: [public]\n"
        "install_source: pypi\n",
        encoding="utf-8",
    )
    update_ns_default(cfg, [PROFILE_NS, "public"])
    data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert data["ns"]["default"] == ["me", "public"]
    assert data["provider"]["model"] == "openai/gpt-4o-mini"
    assert data["install_source"] == "pypi"


def test_run_onboarding_interactive_writes_profile_and_ns(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    cfg = home / "config.yaml"
    cfg.write_text("ns:\n  default: [public]\n", encoding="utf-8")
    answers = iter(["Alice", "Eng", "Backend", "UTC"])
    ran = run_onboarding(home, cfg, interactive=True, prompt=lambda _: next(answers))
    assert ran is True
    assert (home / "raw" / "me" / "profile.md").exists()
    assert yaml.safe_load(cfg.read_text(encoding="utf-8"))["ns"]["default"] == ["me", "public"]


def test_run_onboarding_non_interactive_template(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    cfg = home / "config.yaml"
    cfg.write_text("ns:\n  default: [public]\n", encoding="utf-8")
    ran = run_onboarding(home, cfg, interactive=False)
    assert ran is True
    md = (home / "raw" / "me" / "profile.md").read_text(encoding="utf-8")
    assert "**Role**: \n" in md  # empty value preserved


def test_run_onboarding_idempotent_skip(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "raw" / "me").mkdir(parents=True)
    (home / "raw" / "me" / "profile.md").write_text("existing", encoding="utf-8")
    cfg = home / "config.yaml"
    cfg.write_text("ns:\n  default: [public]\n", encoding="utf-8")
    # prompt must NOT be called when skipping
    ran = run_onboarding(home, cfg, interactive=True, prompt=lambda _: (_ for _ in ()).throw(AssertionError("prompt called")))
    assert ran is False
    assert (home / "raw" / "me" / "profile.md").read_text(encoding="utf-8") == "existing"


def test_run_onboarding_force_overwrites(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "raw" / "me").mkdir(parents=True)
    (home / "raw" / "me" / "profile.md").write_text("old", encoding="utf-8")
    cfg = home / "config.yaml"
    cfg.write_text("ns:\n  default: [public]\n", encoding="utf-8")
    answers = iter(["Alice", "Eng", "Backend", "UTC"])
    ran = run_onboarding(
        home, cfg, interactive=True, prompt=lambda _: next(answers), force=True
    )
    assert ran is True
    assert "Alice" in (home / "raw" / "me" / "profile.md").read_text(encoding="utf-8")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_onboard.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lorekeep.onboard'`.

- [ ] **Step 3: Implement `src/lorekeep/onboard.py`**

```python
"""First-run onboarding: create a personal `me` namespace + profile.

Called from `cli.init`. Interactive prompts run only on a tty; non-tty / CI
gets a template profile with empty values. `ns.default` is updated to
`[me, public]` so an agent scoped to the default sees the user's profile.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import yaml

PROFILE_NS = "me"


def profile_markdown(name: str, role: str, what: str, tz: str) -> str:
    return (
        f"# {name}\n\n"
        f"- **Role**: {role}\n"
        f"- **What I do**: {what}\n"
        f"- **Timezone**: {tz}\n"
        "\n"
        "Personal namespace — facts about me live here (`raw/me/`).\n"
    )


def write_profile(raw_root: Path, md: str) -> Path:
    ns_dir = raw_root / PROFILE_NS
    ns_dir.mkdir(parents=True, exist_ok=True)
    path = ns_dir / "profile.md"
    path.write_text(md, encoding="utf-8")
    return path


def update_ns_default(config_path: Path, ns_list: list[str]) -> None:
    from lorekeep.config import load_config

    cfg = load_config(config_path)
    cfg.ns.default = list(ns_list)
    config_path.write_text(
        yaml.safe_dump(cfg.model_dump(), sort_keys=False),
        encoding="utf-8",
    )


def run_onboarding(
    home: Path,
    config_path: Path,
    *,
    interactive: bool,
    prompt: Callable[[str], str] | None = None,
    force: bool = False,
) -> bool:
    profile = home / "raw" / PROFILE_NS / "profile.md"
    if profile.exists() and not force:
        return False

    if interactive:
        p = prompt or input
        name = p("Your name: ")
        role = p("Your role/title: ")
        what = p("What do you work on? ")
        tz = p("Your timezone (e.g. Asia/Ho_Chi_Minh): ")
    else:
        name = role = what = tz = ""

    write_profile(home / "raw", profile_markdown(name, role, what, tz))
    update_ns_default(config_path, [PROFILE_NS, "public"])
    return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_onboard.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add src/lorekeep/onboard.py tests/test_onboard.py
git commit -m "feat(onboard): add me-namespace onboarding module"
```

---

### Task 2: Wire onboarding into `lorekeep init` (TDD)

**Files:**
- Modify: `src/lorekeep/cli.py` (the `init` command)
- Test: `tests/test_init_cli.py` (extend)

**Interfaces:**
- Consumes: `resolve_paths()["home"]` + `["config"]` (from the dotdir-backup branch), `lorekeep.onboard.run_onboarding` (Task 1).
- Produces: `lorekeep init [--no-onboard] [--force]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_init_cli.py`:

```python
def test_init_writes_me_profile_non_tty(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("LOREKEEP_HOME", str(home))
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.stdout
    assert (home / "raw" / "me" / "profile.md").exists()
    import yaml
    data = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    assert data["ns"]["default"] == ["me", "public"]


def test_init_no_onboard_skips_profile(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("LOREKEEP_HOME", str(home))
    result = runner.invoke(app, ["init", "--no-onboard"])
    assert result.exit_code == 0, result.stdout
    assert not (home / "raw" / "me" / "profile.md").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_init_cli.py -v`
Expected: the two new tests FAIL — `raw/me/profile.md` not created; `--no-onboard` is an unknown option.

- [ ] **Step 3: Modify the `init` command in `src/lorekeep/cli.py`**

First ensure `sys` is imported near the top of the file (the existing imports include `json` and `os`; add `import sys` if absent). Check the current import block — if `import sys` is missing, add it alongside `import os`.

Change the `init` command signature and body. The current command is:

```python
@app.command()
def init() -> None:
    """Bootstrap the data home: config + schema + raw/graph dirs."""
    p = resolve_paths()
    created = []
    p["config"].parent.mkdir(parents=True, exist_ok=True)
    if not p["config"].exists():
        p["config"].write_text(DEFAULT_CONFIG_YAML)
        created.append(str(p["config"]))
    p["schema"].parent.mkdir(parents=True, exist_ok=True)
    if not p["schema"].exists():
        p["schema"].write_text(json.dumps(DEFAULT_SCHEMA, indent=2))
        created.append(str(p["schema"]))
    p["raw"].mkdir(parents=True, exist_ok=True)
    p["out"].mkdir(parents=True, exist_ok=True)
    typer.echo(f"home ready: config={p['config']}")
    typer.echo(f"  schema={p['schema']}  raw={p['raw']}  graph={p['out']}")
    if created:
        typer.echo(f"  wrote defaults: {created}")
    else:
        typer.echo("  (existing config/schema preserved)")
```

Replace it with:

```python
@app.command()
def init(
    no_onboard: bool = typer.Option(
        False, "--no-onboard", help="skip the me-namespace onboarding prompt"
    ),
    force: bool = typer.Option(
        False, "--force", help="overwrite an existing raw/me/profile.md"
    ),
) -> None:
    """Bootstrap the data home: config + schema + raw/graph dirs + me profile."""
    p = resolve_paths()
    created = []
    p["config"].parent.mkdir(parents=True, exist_ok=True)
    if not p["config"].exists():
        p["config"].write_text(DEFAULT_CONFIG_YAML)
        created.append(str(p["config"]))
    p["schema"].parent.mkdir(parents=True, exist_ok=True)
    if not p["schema"].exists():
        p["schema"].write_text(json.dumps(DEFAULT_SCHEMA, indent=2))
        created.append(str(p["schema"]))
    p["raw"].mkdir(parents=True, exist_ok=True)
    p["out"].mkdir(parents=True, exist_ok=True)

    from lorekeep.onboard import run_onboarding
    interactive = sys.stdin.isatty() and not no_onboard
    ran = run_onboarding(p["home"], p["config"], interactive=interactive, force=force)

    typer.echo(f"home ready: config={p['config']}")
    typer.echo(f"  schema={p['schema']}  raw={p['raw']}  graph={p['out']}")
    if created:
        typer.echo(f"  wrote defaults: {created}")
    else:
        typer.echo("  (existing config/schema preserved)")
    if ran:
        typer.echo("  wrote raw/me/profile.md; ns.default=[me, public]")
        typer.echo("  next: `lorekeep compile`, then `lorekeep mcp add --ns me`")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_init_cli.py -v`
Expected: PASS (existing tests + the 2 new ones). The existing `test_init_creates_home` and `test_init_preserves_existing_config` still pass (profile creation is additive; the existing assertions on `config.yaml`/`schema.json`/`raw`/`graph` remain true). Note: `test_init_creates_home` also asserts `ns.default` is the default — if it reads the config, it now sees `[me, public]`; if it asserts `[public]`, update that assertion to `[me, public]`.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS. (The known macOS `test_watch_boots_and_shuts_down_cleanly` flake may still fail — pre-existing, not a finding.) If any other test asserts `ns.default == [public]` after a fresh `init`, update it to `[me, public]`.

- [ ] **Step 6: Commit**

```bash
git add src/lorekeep/cli.py tests/test_init_cli.py
git commit -m "feat(init): onboard a me namespace on first run"
```

---

### Task 3: Update docs

**Files:**
- Modify: `docs/guides/getting-started.md`
- Modify: `AGENTS.md`

- [ ] **Step 1: Update `docs/guides/getting-started.md` step 2**

Find step 2 ("Bootstrap the data home"). After the `uvx lorekeep init` code block, add a note that on a tty `init` prompts for a personal profile (name, role, what you do, timezone) and creates `raw/me/profile.md`, setting `ns.default=[me, public]`; on non-tty it writes a template to fill in later; `--no-onboard` skips it, `--force` overwrites. Keep it to 3-4 lines, matching the surrounding tone.

Example insertion (place right after the `uvx lorekeep doctor` verify line of step 2):

```markdown
> **First run:** on a terminal, `init` prompts for your name, role, what you
> work on, and timezone, then writes `raw/me/profile.md` and scopes your
> default namespace to `me` + `public`. Non-interactive shells get a template
> to fill in later; `--no-onboard` skips it, `--force` overwrites.
```

- [ ] **Step 2: Update `AGENTS.md` `init` row**

In the CLI commands table, change the `init` row's Purpose from:

```
| `init` | Bootstrap data home (config + schema + raw/graph dirs) |
```

to:

```
| `init [--no-onboard] [--force]` | Bootstrap data home + onboard a `me` namespace (interactive profile on a tty) |
```

- [ ] **Step 3: Verify docs build / links**

Run: `grep -n "onboard\|me/profile\|ns.default" docs/guides/getting-started.md AGENTS.md`
Expected: matches in both files.

- [ ] **Step 4: Commit**

```bash
git add docs/guides/getting-started.md AGENTS.md
git commit -m "docs: document me-namespace onboarding in init"
```

---

## Self-Review

- **Spec coverage:** spec "onboard.py module" → Task 1. "cli.py init integration + flags" → Task 2. "profile format" → Task 1 (`profile_markdown`). "ns.default update" → Task 1 (`update_ns_default`). "tty + flags" → Task 2 (`interactive = sys.stdin.isatty() and not no_onboard`). "testing" → Tasks 1 + 2. "docs" → Task 3. All covered.
- **Placeholder scan:** none; every code step shows full code.
- **Type consistency:** `PROFILE_NS = "me"`, `run_onboarding(home, config_path, *, interactive, prompt=None, force=False) -> bool` consistent across Task 1 (produced) and Task 2 (consumed). `prompt` callable signature matches the injected lambdas in tests.
