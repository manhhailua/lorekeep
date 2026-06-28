"""Cursor MCP config writer (.cursor/mcp.json) + sessionEnd hook writer."""
from __future__ import annotations

import json
from pathlib import Path


def write_config(target_dir: Path, command: str, args: list[str], ns: str | None) -> Path:
    entry = {"command": command, "args": args}
    if ns:
        entry["env"] = {"LOREKEEP_NS": ns}
    d = Path(target_dir) / ".cursor"
    d.mkdir(parents=True, exist_ok=True)
    path = d / "mcp.json"
    existing = {}
    if path.exists():
        existing = json.loads(path.read_text())
    servers = existing.get("mcpServers", {})
    servers["lorekeep"] = entry
    path.write_text(json.dumps({"mcpServers": servers}, indent=2))
    return path


def write_hook(target_dir: Path, command: str, args: list[str]) -> Path:
    """Write a sessionEnd hook to .cursor/hooks.json.

    Cursor's hook format uses a single command string (shell form).
    """
    cmd_str = " ".join([command, *args])
    path = Path(target_dir) / ".cursor" / "hooks.json"
    existing = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    hooks = existing.get("hooks", {})
    hooks["sessionEnd"] = [{"command": cmd_str, "timeout": 30}]
    existing["version"] = existing.get("version", 1)
    existing["hooks"] = hooks

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(existing, indent=2))
    return path
