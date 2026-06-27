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


def test_init_creates_about_template(tmp_path: Path, monkeypatch):
    """Non-TTY mode: uses defaults + writes about.md (profile template) under raw/public/."""
    home = tmp_path / "home"
    monkeypatch.setenv("LOREKEEP_HOME", str(home))
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.stdout
    about = home / "raw" / "public" / "about.md"
    assert about.exists()
    assert "(your name)" in about.read_text()


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
    """Interactive: DashScope, default model, empty key, ns=myteam, name, bio."""
    home = tmp_path / "home"
    monkeypatch.setenv("LOREKEEP_HOME", str(home))
    monkeypatch.setattr("lorekeep.cli._is_interactive", lambda: True)

    # provider=3 (DashScope), model=default, key=empty, ns=myteam, name=Alice, bio=...
    result = runner.invoke(app, ["init"], input="3\n\n\nmyteam\nAlice\nBuilds backend infra\n")
    assert result.exit_code == 0, result.stdout
    cfg = yaml.safe_load((home / "config.yaml").read_text())
    assert cfg["provider"]["model"] == "openai/qwen-plus"
    assert cfg["provider"]["api_key"] is None
    assert cfg["provider"]["api_key_env"] is None
    assert cfg["ns"]["default"] == ["myteam"]
    about = home / "raw" / "myteam" / "about.md"
    assert about.exists()
    content = about.read_text()
    assert "Alice" in content
    assert "Builds backend infra" in content


def test_init_interactive_stores_inline_key(tmp_path: Path, monkeypatch):
    """Interactive: OpenAI preset with a typed key is stored inline in config.yaml."""
    home = tmp_path / "home"
    monkeypatch.setenv("LOREKEEP_HOME", str(home))
    monkeypatch.setattr("lorekeep.cli._is_interactive", lambda: True)

    # provider=1 (OpenAI), model=default, key=sk-testKEY, ns=me, name, bio
    result = runner.invoke(app, ["init"], input="1\n\nsk-testKEY\nme\nBob\nlocal dev\n")
    assert result.exit_code == 0, result.stdout
    cfg = yaml.safe_load((home / "config.yaml").read_text())
    assert cfg["provider"]["api_key"] == "sk-testKEY"
    assert cfg["provider"]["api_key_env"] is None
    assert cfg["ns"]["default"] == ["me"]
    about = home / "raw" / "me" / "about.md"
    assert about.exists()
    assert "Bob" in about.read_text()


def test_init_interactive_ollama_no_key(tmp_path: Path, monkeypatch):
    """Ollama preset: no API key prompt."""
    home = tmp_path / "home"
    monkeypatch.setenv("LOREKEEP_HOME", str(home))
    monkeypatch.setattr("lorekeep.cli._is_interactive", lambda: True)

    # Ollama (4), default model, ns=myproject, name, bio
    result = runner.invoke(app, ["init"], input="4\n\nmyproject\nCJ\ndemo\n")
    assert result.exit_code == 0, result.stdout
    cfg = yaml.safe_load((home / "config.yaml").read_text())
    assert cfg["provider"]["model"] == "ollama/llama3.2"
    assert cfg["provider"]["api_key"] is None
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


def test_init_no_about_when_raw_has_files(tmp_path: Path, monkeypatch):
    """If raw/ already has .md files, don't write about.md."""
    home = tmp_path / "home"
    monkeypatch.setenv("LOREKEEP_HOME", str(home))
    (home / "raw" / "existing").mkdir(parents=True)
    (home / "raw" / "existing" / "doc.md").write_text("# existing")
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.stdout
    assert not (home / "raw" / "public" / "about.md").exists()
