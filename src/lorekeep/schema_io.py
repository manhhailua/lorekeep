"""Load the graph schema from a JSON file."""
from __future__ import annotations

import json
from pathlib import Path

from lorekeep.defaults import DEFAULT_SCHEMA, DEFAULT_SCHEMA_V2, DEFAULT_SCHEMA_V3
from lorekeep.models import Schema


def load_schema(path: Path) -> Schema:
    data = json.loads(path.read_text(encoding="utf-8"))
    return Schema.load(data)


def upgrade_schema(
    path: Path,
    *,
    dry_run: bool = False,
    force: bool = False,
) -> dict:
    """Upgrade a stock historical schema without overwriting custom schemas."""
    data = json.loads(path.read_text(encoding="utf-8"))
    version = int(data.get("version", 0))
    if version >= DEFAULT_SCHEMA["version"]:
        return {"changed": False, "from": version, "to": version, "custom": False}

    custom = data not in (DEFAULT_SCHEMA_V2, DEFAULT_SCHEMA_V3)
    if custom and not force:
        return {
            "changed": False,
            "from": version,
            "to": DEFAULT_SCHEMA["version"],
            "custom": True,
        }

    backup = path.with_name(f"{path.stem}.v{version}.backup{path.suffix}")
    if not dry_run:
        if not backup.exists():
            backup.write_text(
                json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        path.write_text(
            json.dumps(DEFAULT_SCHEMA, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    return {
        "changed": True,
        "from": version,
        "to": DEFAULT_SCHEMA["version"],
        "custom": custom,
        "backup": str(backup),
        "dry_run": dry_run,
    }
