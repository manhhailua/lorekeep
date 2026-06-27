import subprocess
from pathlib import Path

from typer.testing import CliRunner

from lorekeep.cli import app

runner = CliRunner()


def _bare_remote(tmp_path: Path) -> str:
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True)
    return str(bare)


def test_backup_init_then_backup_round_trip(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("LOREKEEP_HOME", str(home))
    remote = _bare_remote(tmp_path)

    init_result = runner.invoke(app, ["backup", "--init", remote])
    assert init_result.exit_code == 0, init_result.stdout
    assert (home / ".git").is_dir()

    (home / "raw" / "ns").mkdir(parents=True)
    (home / "raw" / "ns" / "x.md").write_text("# x")
    result = runner.invoke(app, ["backup"])
    assert result.exit_code == 0, result.stdout
    assert "pushed" in result.stdout


def test_backup_when_up_to_date(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("LOREKEEP_HOME", str(home))
    remote = _bare_remote(tmp_path)
    runner.invoke(app, ["backup", "--init", remote])

    result = runner.invoke(app, ["backup"])
    assert result.exit_code == 0, result.stdout
    assert "up to date" in result.stdout


def test_backup_without_init_fails_cleanly(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("LOREKEEP_HOME", str(home))
    result = runner.invoke(app, ["backup"])
    assert result.exit_code == 1, result.stdout
    assert "backup failed" in result.stdout
