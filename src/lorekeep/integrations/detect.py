"""Detect coding agents: active session (env vars) + installed (filesystem markers).

Two-layer detection:
  1. ``detect_active_agent`` — which agent shell are we running inside right now?
     Uses well-known env vars.  Returns at most one agent name (or ``None``).
  2. ``detect_installed_agents`` — which agents are installed on this machine?
     Checks for config directories / binaries.  Returns a list (may be empty).

``detect_agents`` combines both: if an active session is detected, return just
that agent.  Otherwise, return all installed agents found on the filesystem.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

# Env var that each agent sets when it launches a subprocess shell.
# The value is checked for truthiness (any non-empty string counts).
_ACTIVE_ENV: dict[str, list[str]] = {
    "claude": ["CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"],
    "opencode": ["OPENCODE"],
    "cursor": ["CURSOR_DEBUG"],
}

# Filesystem markers that indicate an agent has been installed / used.
# Each entry is a list of candidate paths (``~`` expanded relative to home).
_INSTALLED_MARKERS: dict[str, list[str]] = {
    "claude": ["~/.claude"],
    "cursor": ["~/.cursor"],
    "codex": ["~/.codex"],
    "opencode": ["~/.config/opencode", "~/.opencode"],
}

# Binary names to look for in PATH (fallback when dir markers are missing).
_BINARIES: dict[str, list[str]] = {
    "claude": ["claude"],
    "cursor": ["cursor"],
    "codex": ["codex"],
    "opencode": ["opencode"],
}

SUPPORTED_AGENTS = sorted(_ACTIVE_ENV.keys() | _INSTALLED_MARKERS.keys())


def _env_truthy(name: str) -> bool:
    val = os.environ.get(name, "")
    return val not in ("", "0", "false", "False")


def detect_active_agent() -> str | None:
    """Return the agent whose shell we are running inside, or ``None``."""
    for agent, env_vars in _ACTIVE_ENV.items():
        if any(_env_truthy(v) for v in env_vars):
            return agent
    return None


def detect_installed_agents() -> list[str]:
    """Return all agents detected on this machine (filesystem + PATH)."""
    found: list[str] = []
    home = Path.home()
    for agent, markers in _INSTALLED_MARKERS.items():
        if any(Path(m).expanduser().exists() for m in markers):
            found.append(agent)
            continue
        if any(shutil.which(b) for b in _BINARIES.get(agent, [])):
            found.append(agent)
    return found


def detect_agents() -> list[str]:
    """Detect agents to wire during ``init``.

    - If running inside a coding agent, return **only** that agent (the user
      is already in it; wiring others is noise).
    - Otherwise, return all installed agents found on the filesystem.
    """
    active = detect_active_agent()
    if active:
        return [active]
    return detect_installed_agents()
