from pathlib import Path
from typer.testing import CliRunner
from lorekeep.cli import app

runner = CliRunner()


def test_init_creates_home(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("LOREKEEP_HOME", str(home))
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.stdout
    assert (home / "config.yaml").exists()
    assert (home / "schema.json").exists()
    assert (home / "raw").is_dir()
    assert (home / "graph").is_dir()
    import json
    schema = json.loads((home / "schema.json").read_text())
    assert schema["version"] == 2


def test_init_preserves_existing_config(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    (home).mkdir()
    (home / "config.yaml").write_text("install_source: local\n")
    monkeypatch.setenv("LOREKEEP_HOME", str(home))
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.stdout
    assert (home / "config.yaml").read_text() == "install_source: local\n"
    assert (home / "schema.json").exists()
    assert (home / "raw").is_dir()


def test_init_writes_me_profile_non_tty(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("LOREKEEP_HOME", str(home))
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.stdout
    assert (home / "raw" / "me" / "profile.md").exists()
    import yaml
    data = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    assert data["ns"]["default"] == ["me", "public"]


def test_init_no_onboard_skips_profile(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("LOREKEEP_HOME", str(home))
    result = runner.invoke(app, ["init", "--no-onboard"])
    assert result.exit_code == 0, result.stdout
    assert not (home / "raw" / "me" / "profile.md").exists()


def test_init_force_reonboards_profile_on_existing_config(tmp_path: Path, monkeypatch):
    """--force rewrites the profile on an existing config; ns.default preserved."""
    import yaml
    home = tmp_path / "home"
    home.mkdir()
    (home / "raw" / "me").mkdir(parents=True)
    # Seed an existing config + profile ("old"). CliRunner is non-tty, so the
    # rewritten profile is the empty template — assert content changed AND
    # ns.default (seeded non-default) is preserved.
    (home / "raw" / "me" / "profile.md").write_text("old", encoding="utf-8")
    (home / "config.yaml").write_text("ns:\n  default: [public]\n", encoding="utf-8")
    monkeypatch.setenv("LOREKEEP_HOME", str(home))
    result = runner.invoke(app, ["init", "--force"])
    assert result.exit_code == 0, result.stdout
    md = (home / "raw" / "me" / "profile.md").read_text(encoding="utf-8")
    assert md != "old"  # profile rewritten
    data = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    assert data["ns"]["default"] == ["public"]  # preserved, not clobbered to [me, public]
