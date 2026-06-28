import json
from pathlib import Path
from typer.testing import CliRunner
from lorekeep.cli import app

runner = CliRunner()


def test_compile_command_uses_config_provider(patch_make_provider, monkeypatch, tmp_path: Path, fixtures: Path):
    # point the CLI at temp dirs via env
    monkeypatch.setenv("LOREKEEP_RAW", str(tmp_path / "raw"))
    monkeypatch.setenv("LOREKEEP_OUT", str(tmp_path / "graph"))
    monkeypatch.setenv("LOREKEEP_CACHE", str(tmp_path / "cache.json"))
    monkeypatch.setenv("LOREKEEP_SCHEMA", str(fixtures / "schema.json"))

    raw = tmp_path / "raw/backend/payments.md"
    raw.parent.mkdir(parents=True)
    raw.write_text((fixtures / "raw/backend/payments.md").read_text())

    result = runner.invoke(app, ["compile"])
    assert result.exit_code == 0, result.stdout
    assert (tmp_path / "graph/facts.jsonl").exists()
