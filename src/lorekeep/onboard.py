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
