from pathlib import Path
from lorekeep.paths import resolve_paths


def test_dev_mode_via_lorekeep_marker(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".lorekeep").mkdir()
    p = resolve_paths()
    assert p["home"] == tmp_path / ".lorekeep"
    assert p["config"] == tmp_path / ".lorekeep" / "config.yaml"
    assert p["cache"] == tmp_path / ".lorekeep" / "cache.json"
    assert p["raw"] == tmp_path / ".lorekeep" / "raw"
    assert p["out"] == tmp_path / ".lorekeep" / "graph"
    assert p["schema"] == tmp_path / ".lorekeep" / "schema.json"
    assert p["pending"] == tmp_path / ".lorekeep" / "pending"


def test_lorekeep_home_overrides_dev(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".lorekeep").mkdir()
    home = tmp_path / "myhome"
    monkeypatch.setenv("LOREKEEP_HOME", str(home))
    p = resolve_paths()
    assert p["home"] == home
    assert p["config"] == home / "config.yaml"
    assert p["raw"] == home / "raw"
    assert p["schema"] == home / "schema.json"


def test_xdg_default(tmp_path: Path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)
    monkeypatch.delenv("LOREKEEP_HOME", raising=False)
    monkeypatch.delenv("LOREKEEP_DEV", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    p = resolve_paths()
    assert p["home"] == tmp_path / "xdg-data" / "lorekeep"
    assert p["config"] == tmp_path / "xdg-config" / "lorekeep" / "config.yaml"
    assert p["raw"] == tmp_path / "xdg-data" / "lorekeep" / "raw"
    assert p["schema"] == tmp_path / "xdg-data" / "lorekeep" / "schema.json"


def test_explicit_env_overrides_everything(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".lorekeep").mkdir()                 # dev mode active
    monkeypatch.setenv("LOREKEEP_RAW", "/custom/raw")
    monkeypatch.setenv("LOREKEEP_OUT", "/custom/graph")
    monkeypatch.setenv("LOREKEEP_CONFIG", "/custom/config.yaml")
    p = resolve_paths()
    assert p["home"] == tmp_path / ".lorekeep"
    assert p["raw"] == Path("/custom/raw")
    assert p["out"] == Path("/custom/graph")
    assert p["config"] == Path("/custom/config.yaml")
    assert p["schema"] == tmp_path / ".lorekeep" / "schema.json"
