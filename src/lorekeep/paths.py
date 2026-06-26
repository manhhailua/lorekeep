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
