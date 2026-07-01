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
    """Interactive: deepseek (3), default model, empty key + env var, ns, name, bio."""
    home = tmp_path / "home"
    monkeypatch.setenv("LOREKEEP_HOME", str(home))
    monkeypatch.setattr("lorekeep.cli._is_interactive", lambda: True)

    # Mock list_models to avoid slow litellm calls
    from lorekeep.providers import ModelInfo
    monkeypatch.setattr(
        "lorekeep.providers.list_models",
        lambda p: [ModelInfo("deepseek-chat", p, "chat", 0.28e-6, 0.42e-6, 131000, True)]
    )

    # provider=3 (deepseek), model=1, key=empty, env=DEEPSEEK_API_KEY, ns=myteam, name, bio
    result = runner.invoke(app, ["init"], input="3\n1\n\n\nmyteam\nAlice\nBuilds backend infra\n")
    assert result.exit_code == 0, result.stdout
    cfg = yaml.safe_load((home / "config.yaml").read_text())
    assert cfg["provider"]["model"] == "deepseek-chat"
    assert cfg["provider"]["api_key"] is None
    assert cfg["provider"]["api_key_env"] == "DEEPSEEK_API_KEY"
    assert cfg["ns"]["default"] == ["myteam"]
    about = home / "raw" / "myteam" / "about.md"
    assert about.exists()
    content = about.read_text()
    assert "Alice" in content
    assert "Builds backend infra" in content


def test_init_interactive_stores_inline_key(tmp_path: Path, monkeypatch):
    """Interactive: OpenAI (1) with inline key stored in config.yaml."""
    home = tmp_path / "home"
    monkeypatch.setenv("LOREKEEP_HOME", str(home))
    monkeypatch.setattr("lorekeep.cli._is_interactive", lambda: True)

    from lorekeep.providers import ModelInfo
    monkeypatch.setattr(
        "lorekeep.providers.list_models",
        lambda p: [ModelInfo("gpt-4o-mini", p, "chat", 0.15e-6, 0.6e-6, 128000, True)]
    )

    # provider=1 (openai), model=1, key=sk-testKEY, ns=me, name, bio
    result = runner.invoke(app, ["init"], input="1\n1\nsk-testKEY\nme\nBob\nlocal dev\n")
    assert result.exit_code == 0, result.stdout
    cfg = yaml.safe_load((home / "config.yaml").read_text())
    assert cfg["provider"]["model"] == "gpt-4o-mini"
    assert cfg["provider"]["api_key"] == "sk-testKEY"
    assert cfg["provider"]["api_key_env"] is None
    assert cfg["ns"]["default"] == ["me"]
    about = home / "raw" / "me" / "about.md"
    assert about.exists()
    assert "Bob" in about.read_text()


def test_init_interactive_ollama_no_key(tmp_path: Path, monkeypatch):
    """Ollama: free-text model, api_base prompt, no API key needed."""
    home = tmp_path / "home"
    monkeypatch.setenv("LOREKEEP_HOME", str(home))
    monkeypatch.setattr("lorekeep.cli._is_interactive", lambda: True)

    # Ollama is now in POPULAR list but is dynamic — free-text model + api_base
    # Find ollama's index in POPULAR list
    from lorekeep.providers import POPULAR
    ollama_idx = POPULAR.index("ollama") + 1 if "ollama" in POPULAR else None

    if ollama_idx is None:
        # ollama not in POPULAR, use search
        pytest.skip("ollama not in POPULAR list")

    # provider=ollama_idx, model=llama3.2, api_base=default, ns=myproject, name, bio
    inp = f"{ollama_idx}\nllama3.2\n\nmyproject\nCJ\ndemo\n"
    result = runner.invoke(app, ["init"], input=inp)
    assert result.exit_code == 0, result.stdout
    cfg = yaml.safe_load((home / "config.yaml").read_text())
    assert cfg["provider"]["model"] == "llama3.2"
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


def test_init_auto_wires_detected_agent(tmp_path: Path, monkeypatch):
    """init detects active agent from env and writes its MCP config."""
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    fake_home = tmp_path / "fakehome"
    fake_home.mkdir()
    monkeypatch.setenv("LOREKEEP_HOME", str(home))
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.chdir(project)
    monkeypatch.setenv("OPENCODE", "1")
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.setattr("pathlib.Path.home", lambda: fake_home)
    monkeypatch.setattr("lorekeep.integrations.detect.shutil.which", lambda _: None)
    result = runner.invoke(app, ["init", "--yes"])
    assert result.exit_code == 0, result.stdout
    import json
    mcp_path = project / "opencode.json"
    assert mcp_path.exists(), f"opencode.json not written: {result.stdout}"
    data = json.loads(mcp_path.read_text())
    assert data["mcp"]["lorekeep"]["type"] == "local"


def test_init_auto_wires_installed_agents(tmp_path: Path, monkeypatch):
    """init in shell mode scans filesystem and wires all installed agents."""
    home = tmp_path / "home"
    fake_home = tmp_path / "fakehome"
    project = tmp_path / "project"
    project.mkdir()
    (fake_home / ".claude").mkdir(parents=True)
    (fake_home / ".config" / "opencode").mkdir(parents=True)

    monkeypatch.setenv("LOREKEEP_HOME", str(home))
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.chdir(project)
    monkeypatch.delenv("OPENCODE", raising=False)
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.setattr("pathlib.Path.home", lambda: fake_home)
    monkeypatch.setattr("lorekeep.integrations.detect.shutil.which", lambda _: None)

    result = runner.invoke(app, ["init", "--yes"])
    assert result.exit_code == 0, result.stdout
    assert (project / ".mcp.json").exists()
    assert (project / "opencode.json").exists()


def test_init_no_agents_detected_message(tmp_path: Path, monkeypatch):
    """init reports when no agents are detected."""
    home = tmp_path / "home"
    fake_home = tmp_path / "fakehome"
    fake_home.mkdir()
    monkeypatch.setenv("LOREKEEP_HOME", str(home))
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENCODE", raising=False)
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.setattr("pathlib.Path.home", lambda: fake_home)
    monkeypatch.setattr("lorekeep.integrations.detect.shutil.which", lambda _: None)

    result = runner.invoke(app, ["init", "--yes"])
    assert result.exit_code == 0, result.stdout
    assert "no coding agents detected" in result.stdout.lower()
