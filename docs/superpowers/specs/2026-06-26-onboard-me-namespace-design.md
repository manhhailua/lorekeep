# Design: Onboarding flow with a `me` namespace + user profile

**Date:** 2026-06-26
**Status:** Approved (approach A)
**Author:** manhpt1

## Goal

Make `lorekeep init` immediately personal: on a fresh data home it creates a
`me` namespace, asks a few questions, and writes a structured profile
(`raw/me/profile.md`) so the compiled graph starts with facts about the user —
instead of an empty `raw/` that the user must seed with an irrelevant example
namespace like `backend`.

## Non-goals

- Auto-running `compile` during `init` (compile needs a configured provider;
  `init` stays a bootstrap-only command that prints the next-step hint).
- A separate `lorekeep onboard` command (onboarding lives inside `init`).
- Changing the schema or the extract pipeline. The profile is ordinary raw
  markdown; the existing extractor turns it into a `person` node (+ context).

## Decisions

- **Integration:** interactive prompts inside `init`; non-tty/`--no-onboard`
  writes a template profile instead. `--force` overwrites an existing profile.
- **Questions (4):** name, role, "what I do" (skills/focus), timezone.
- **Profile format:** structured markdown (labels), human + LLM friendly.
- **`ns.default`:** updated to `[me, public]` so an agent scoped to the default
  sees the user's profile immediately.
- **Module shape:** new `src/lorekeep/onboard.py` with pure helpers + an
  orchestrator that takes an injected `prompt` callable (testable without tty).
- **Config write:** `update_ns_default` loads the Pydantic `Config`, sets
  `ns.default`, and rewrites `config.yaml` via `yaml.safe_dump`. **Known cost:**
  this drops comments from a hand-edited `config.yaml`. Accepted because the
  generated default config has no comments; users who keep comments accept the
  loss on the one onboarding run (or edit `ns.default` by hand).

## Design

### `src/lorekeep/onboard.py` (new)

```
PROFILE_NS = "me"

def profile_markdown(name: str, role: str, what: str, tz: str) -> str
def write_profile(raw_root: Path, md: str) -> Path          # writes raw_root/"me"/"profile.md"
def update_ns_default(config_path: Path, ns_list: list[str]) -> None
def run_onboarding(
    home: Path, config_path: Path, *,
    interactive: bool, prompt: Callable[[str], str] = typer.prompt,
    force: bool = False,
) -> bool
```

`run_onboarding` logic (takes `home`; derives `raw_root = home / "raw"`):
1. `profile = home / "raw" / "me" / "profile.md"`. If it exists and not `force` →
   return `False` (skip, idempotent).
2. If `interactive`: call `prompt` for each of the 4 fields. Else: all `""`.
3. `write_profile(home / "raw", profile_markdown(...))`.
4. `update_ns_default(config_path, [PROFILE_NS, "public"])`.
5. Return `True`.

`update_ns_default`:
```
from lorekeep.config import load_config
cfg = load_config(config_path)
cfg.ns.default = [PROFILE_NS, "public"]
config_path.write_text(yaml.safe_dump(cfg.model_dump(), sort_keys=False))
```

### `cli.py init` — integration

Two new options: `--no-onboard` (skip), `--force` (overwrite profile). After
the existing dir/config/schema bootstrap:
```
interactive = sys.stdin.isatty() and not no_onboard
ran = run_onboarding(p["home"], p["config"], interactive=interactive, force=force)
if ran:
    typer.echo("  wrote raw/me/profile.md; next: `lorekeep compile`, then `lorekeep mcp add --ns me`")
```

### Profile format

```markdown
# {name}

- **Role**: {role}
- **What I do**: {what}
- **Timezone**: {tz}

Personal namespace — facts about me live here (`raw/me/`).
```

The extractor turns this into a `person` node (`name` + `role` props) plus any
extra context the LLM infers. Fake-compile ignores the actual content (curated
source, not the compiled output).

### tty + flags

- tty and not `--no-onboard` → interactive prompts.
- non-tty (CI) OR `--no-onboard` → template profile with empty values, `ns.default` still updated.
- `--force` → overwrite even if `profile.md` exists.

## Components touched

| File | Change |
|---|---|
| `src/lorekeep/onboard.py` | new — pure helpers + orchestrator |
| `src/lorekeep/cli.py` | `init`: add `--no-onboard`/`--force`, call `run_onboarding` |
| `tests/test_onboard.py` | new — unit tests for helpers + orchestrator (injected prompt) |
| `tests/test_init_cli.py` | extend — `init` writes `me` profile (non-tty template path) |
| `docs/guides/getting-started.md` | update step 2 to mention the prompt + `me` namespace |
| `AGENTS.md` | update `init` row description |

## Testing

TDD. `run_onboarding`'s `prompt` callable is injected, so interactive logic is
tested without a real tty. Cases:
- `profile_markdown` — exact string for given inputs.
- `write_profile` — file exists at `raw/me/profile.md`, parents created.
- `update_ns_default` — rewritten `config.yaml` has `ns.default: [me, public]`,
  other fields preserved.
- `run_onboarding` interactive — injected fake prompt → profile + ns.default written.
- `run_onboarding` non-interactive — template with empty values written.
- Idempotent — existing profile → returns `False`, no write; `force=True` → overwrites.
- `init` CLI (non-tty, `LOREKEEP_HOME` tmp) — `profile.md` created, `ns.default` updated.

## Risks

- **Config comment loss** on `update_ns_default` for hand-edited configs —
  accepted (see Decisions); documented in the command's output hint.
- **Non-tty still writes a profile** — intentional (so installs/CI get the
  namespace scaffold), but the profile is empty; user fills it later.
- **`profile_markdown` with empty strings** (non-tty) still produces valid
  markdown the extractor can handle; no crash on empty fields.
