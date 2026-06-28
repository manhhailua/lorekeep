"""opencode MCP config writer (opencode.json) + session.idle plugin writer."""
from __future__ import annotations

import json
from pathlib import Path


def write_config(target_dir: Path, command: str, args: list[str], ns: str | None) -> Path:
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / "opencode.json"

    existing: dict = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text())
        except (json.JSONDecodeError, ValueError):
            existing = {}

    entry: dict = {
        "type": "local",
        "command": [command, *args],
        "enabled": True,
    }
    if ns:
        entry["environment"] = {"LOREKEEP_NS": ns}

    mcp = existing.get("mcp", {})
    mcp["lorekeep"] = entry
    existing["mcp"] = mcp

    path.write_text(json.dumps(existing, indent=2) + "\n")
    return path


_PLUGIN_TS = """\
import type {{ Plugin }} from "@opencode-ai/plugin"

export default {{
  event: async ({{ $, event }}) => {{
    if (event.type === "session.idle") {{
      await $`{cmd}`
    }}
  }},
}} satisfies Plugin
"""


def write_hook(target_dir: Path, command: str, args: list[str]) -> Path:
    """Write a session.idle plugin to .opencode/plugins/lorekeep.ts.

    opencode has no declarative hooks — this TS plugin subscribes to
    session.idle and runs the lorekeep hook command.
    """
    cmd = " ".join([command, *args])
    plugin_dir = Path(target_dir) / ".opencode" / "plugins"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    path = plugin_dir / "lorekeep.ts"
    path.write_text(_PLUGIN_TS.format(cmd=cmd))
    return path
