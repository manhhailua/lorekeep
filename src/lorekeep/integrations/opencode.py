"""opencode MCP config writer (opencode.json ``mcp`` section).

opencode uses a different MCP schema than Claude/Cursor:
  - Config file: ``opencode.json`` (project root) or ``~/.config/opencode/opencode.json`` (global)
  - Key: ``mcp`` (not ``mcpServers``)
  - Entry shape: ``{"type": "local", "command": [cmd, ...args], "enabled": true, "environment": {...}}``
  - ``command`` is a single array (command + args combined)

Idempotent: re-running merges into the existing ``mcp`` section, replacing only
the ``lorekeep`` entry.
"""
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
