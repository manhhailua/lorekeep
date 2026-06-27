from pathlib import Path
from typer.testing import CliRunner
from lorekeep.cli import app
import yaml

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


def test_init_creates_sample_doc(tmp_path: Path, monkeypatch):
    """Non-TTY mode: uses defaults + creates sample doc under raw/public/."""
    home = tmp_path / "home"
    monkeypatch.setenv("LOREKEEP_HOME", str(home))
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.stdout
    sample = home / "raw" / "public" / "welcome.md"
    assert sample.exists()
    assert "api-gateway" in sample.read_text()


def test_init_yes_flag_skips_prompts(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("LOREKEEP_HOME", str(home))
    result = runner.invoke(app, ["init", "--yes"])
    assert result.exit_code == 0, result.stdout
    assert (home / "config.yaml").exists()
    cfg = yaml.safe_load((home / "config.yaml").read_text())
    assert cfg["ns"]["default"] == ["public"]
    assert cfg["provider"]["model"] == "openai/gpt-4o-mini"


def test_init_interactive(tmp_path: Path, monkeypatch):
    """Interactive mode: force TTY + provide answers via CliRunner input."""
    home = tmp_path / "home"
    monkeypatch.setenv("LOREKEEP_HOME", str(home))
    monkeypatch.setattr("lorekeep.cli._is_interactive", lambda: True)

    # DashScope (3), default model, default key, ns=myteam
    result = runner.invoke(app, ["init"], input="3\n\n\nmyteam\n")
    assert result.exit_code == 0, result.stdout
    cfg = yaml.safe_load((home / "config.yaml").read_text())
    assert cfg["provider"]["model"] == "openai/qwen-plus"
    assert cfg["provider"]["api_key_env"] == "DASHSCOPE_API_KEY"
    assert cfg["ns"]["default"] == ["myteam"]
    assert (home / "raw" / "myteam" / "welcome.md").exists()


def test_init_interactive_ollama_no_key(tmp_path: Path, monkeypatch):
    """Ollama preset: no API key env prompt."""
    home = tmp_path / "home"
    monkeypatch.setenv("LOREKEEP_HOME", str(home))
    monkeypatch.setattr("lorekeep.cli._is_interactive", lambda: True)

    # Ollama (4), default model, ns=myproject
    result = runner.invoke(app, ["init"], input="4\n\nmyproject\n")
    assert result.exit_code == 0, result.stdout
    cfg = yaml.safe_load((home / "config.yaml").read_text())
    assert cfg["provider"]["model"] == "ollama/llama3.2"
    assert cfg["provider"]["api_key_env"] is None
    assert cfg["ns"]["default"] == ["myproject"]


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


def test_init_no_sample_doc_when_raw_has_files(tmp_path: Path, monkeypatch):
    """If raw/ already has .md files, don't create the sample."""
    home = tmp_path / "home"
    monkeypatch.setenv("LOREKEEP_HOME", str(home))
    # Pre-create raw/ with a doc
    (home / "raw" / "existing").mkdir(parents=True)
    (home / "raw" / "existing" / "doc.md").write_text("# existing")
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.stdout
    assert not (home / "raw" / "public" / "welcome.md").exists()
