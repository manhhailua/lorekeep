from pathlib import Path
from typer.testing import CliRunner
from lorekeep.cli import app

runner = CliRunner()


def test_mcp_add_claude_project(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LOREKEEP_CONFIG", str(tmp_path / "config.yaml"))
    (tmp_path / "config.yaml").write_text("install_source: local\n")
    result = runner.invoke(app, ["mcp", "add", "--agent", "claude", "--ns", "teams/backend"])
    assert result.exit_code == 0, result.stdout
    import json
    data = json.loads((tmp_path / ".mcp.json").read_text())
    assert data["mcpServers"]["lorekeep"]["command"] == "lorekeep"
    assert "lorekeep knowledge base" in result.stdout.lower()   # snippet printed


def test_mcp_add_codex_user(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)        # user scope -> tmp_path
    monkeypatch.setenv("LOREKEEP_CONFIG", str(tmp_path / "config.yaml"))
    (tmp_path / "config.yaml").write_text("install_source: git+https://github.com/x/lorekeep.git\n")
    result = runner.invoke(app, ["mcp", "add", "--agent", "codex", "--scope", "user"])
    assert result.exit_code == 0, result.stdout
    text = (tmp_path / "config.toml").read_text()
    assert "--from" in text and "git+https://github.com/x/lorekeep.git" in text


def test_mcp_add_opencode_project(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LOREKEEP_CONFIG", str(tmp_path / "config.yaml"))
    (tmp_path / "config.yaml").write_text("install_source: local\n")
    result = runner.invoke(app, ["mcp", "add", "--agent", "opencode", "--ns", "teams/backend"])
    assert result.exit_code == 0, result.stdout
    import json
    data = json.loads((tmp_path / "opencode.json").read_text())
    entry = data["mcp"]["lorekeep"]
    assert entry["type"] == "local"
    assert entry["command"] == ["lorekeep", "serve", "--transport", "stdio"]
    assert entry["environment"]["LOREKEEP_NS"] == "teams/backend"
