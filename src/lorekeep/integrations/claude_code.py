"""Claude Code MCP config writer (.mcp.json) + SessionEnd hook writer."""
from __future__ import annotations

import json
from pathlib import Path


def write_config(target_dir: Path, command: str, args: list[str], ns: str | None) -> Path:
    entry = {"command": command, "args": args}
    if ns:
        entry["env"] = {"LOREKEEP_NS": ns}
    path = Path(target_dir) / ".mcp.json"
    existing = {}
    if path.exists():
        existing = json.loads(path.read_text())
    servers = existing.get("mcpServers", {})
    servers["lorekeep"] = entry
    path.write_text(json.dumps({"mcpServers": servers}, indent=2))
    return path


def write_hook(target_dir: Path, command: str, args: list[str]) -> Path:
    """Write a SessionEnd hook to .claude/settings.json.

    The hook calls ``lorekeep hook`` which quick-imports Claude memory
    files into raw/ on session end. The daemon (if running) picks up the
    raw/ change and compiles automatically.
    """
    settings_path = Path(target_dir) / ".claude" / "settings.json"
    existing = {}
    if settings_path.exists():
        try:
            existing = json.loads(settings_path.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    hooks = existing.get("hooks", {})
    hooks["SessionEnd"] = [{
        "hooks": [{
            "type": "command",
            "command": command,
            "args": args,
            "timeout": 30,
        }]
    }]
    existing["hooks"] = hooks

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(existing, indent=2))
    return settings_path
