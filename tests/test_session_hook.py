"""Tests for SessionEnd hook: lorekeep hook command + .claude/settings.json wiring."""
import json
import shutil
import tempfile
from pathlib import Path

from typer.testing import CliRunner

import lorekeep.mcp_server as ms
from lorekeep.cli import app
from lorekeep.integrations.claude_code import write_config, write_hook

runner = CliRunner()


# ── lorekeep hook command ─────────────────────────────────────────────────


def test_hook_imports_memory_from_stdin(tmp_path: Path, monkeypatch):
    """lorekeep hook reads transcript_path from stdin, imports memory files."""
    home = tmp_path / "home"
    home.mkdir()
    (home / "raw").mkdir()
    (home / "graph").mkdir()
    (home / "pending").mkdir()
    (home / "cache.json").write_text("{}")

    session_dir = tmp_path / "session"
    (session_dir / "memory").mkdir(parents=True)
    (session_dir / "memory" / "note.md").write_text("# Important\nKnowledge.\n")
    transcript = session_dir / "transcript.jsonl"
    transcript.write_text("{}")

    monkeypatch.setenv("LOREKEEP_HOME", str(home))

    hook_input = json.dumps({"transcript_path": str(transcript)})
    result = runner.invoke(app, ["hook"], input=hook_input)
    assert result.exit_code == 0, result.stdout

    imported = home / "raw" / "claude-memory" / "note.md"
    assert imported.exists()
    assert "imported" in result.stdout.lower()


def test_hook_no_session_silent_exit(tmp_path: Path, monkeypatch):
    """lorekeep hook exits silently when no session found."""
    home = tmp_path / "home"
    home.mkdir()
    (home / "raw").mkdir()
    (home / "graph").mkdir()
    monkeypatch.setenv("LOREKEEP_HOME", str(home))
    monkeypatch.setattr(
        "lorekeep.importer.claude.find_current_session", lambda: None
    )

    result = runner.invoke(app, ["hook"], input="")
    assert result.exit_code == 0


def test_hook_no_memory_dir_silent_exit(tmp_path: Path, monkeypatch):
    """lorekeep hook exits silently when session has no memory/ dir."""
    home = tmp_path / "home"
    home.mkdir()
    (home / "raw").mkdir()
    monkeypatch.setenv("LOREKEEP_HOME", str(home))

    session_dir = tmp_path / "session"
    session_dir.mkdir()
    transcript = session_dir / "transcript.jsonl"
    transcript.write_text("{}")

    hook_input = json.dumps({"transcript_path": str(transcript)})
    result = runner.invoke(app, ["hook"], input=hook_input)
    assert result.exit_code == 0


def test_hook_idempotent(tmp_path: Path, monkeypatch):
    """Running hook twice doesn't re-import unchanged files."""
    home = tmp_path / "home"
    home.mkdir()
    (home / "raw").mkdir()
    (home / "graph").mkdir()
    (home / "pending").mkdir()
    (home / "cache.json").write_text("{}")

    session_dir = tmp_path / "session"
    (session_dir / "memory").mkdir(parents=True)
    (session_dir / "memory" / "note.md").write_text("# Knowledge\n")
    transcript = session_dir / "transcript.jsonl"
    transcript.write_text("{}")

    monkeypatch.setenv("LOREKEEP_HOME", str(home))

    hook_input = json.dumps({"transcript_path": str(transcript)})
    runner.invoke(app, ["hook"], input=hook_input)
    result2 = runner.invoke(app, ["hook"], input=hook_input)
    assert result2.exit_code == 0
    assert "imported" not in result2.stdout.lower()


# ── write_hook (settings.json) ────────────────────────────────────────────


def test_write_hook_creates_settings(tmp_path: Path):
    """write_hook creates .claude/settings.json with SessionEnd hook."""
    cmd = "uvx"
    args = ["lorekeep", "hook"]

    path = write_hook(tmp_path, cmd, args)
    assert path == tmp_path / ".claude" / "settings.json"
    assert path.exists()

    settings = json.loads(path.read_text())
    hooks = settings["hooks"]["SessionEnd"]
    assert len(hooks) == 1
    handler = hooks[0]["hooks"][0]
    assert handler["type"] == "command"
    assert handler["command"] == "uvx"
    assert handler["args"] == ["lorekeep", "hook"]
    assert handler["timeout"] == 30


def test_write_hook_preserves_existing_settings(tmp_path: Path):
    """write_hook preserves existing settings keys."""
    settings_path = tmp_path / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(json.dumps({
        "permissions": {"allow": ["Read"]},
        "hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": "echo hi"}]}]},
    }))

    write_hook(tmp_path, "uvx", ["lorekeep", "hook"])

    settings = json.loads(settings_path.read_text())
    assert settings["permissions"]["allow"] == ["Read"]
    assert "PreToolUse" in settings["hooks"]
    assert "SessionEnd" in settings["hooks"]


# ── Init wires hook for Claude ─────────────────────────────────────────────


def test_init_writes_claude_hook(tmp_path: Path, monkeypatch):
    """init should write SessionEnd hook when Claude is detected."""
    home = tmp_path / "home"
    project = tmp_path / "project"
    fake_home = tmp_path / "fakehome"
    project.mkdir()
    fake_home.mkdir()
    (fake_home / ".claude").mkdir(parents=True)

    monkeypatch.setenv("LOREKEEP_HOME", str(home))
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("LOREKEEP_DEV", "0")
    monkeypatch.chdir(project)
    monkeypatch.setattr("pathlib.Path.home", lambda: fake_home)
    monkeypatch.setattr("lorekeep.integrations.detect.shutil.which", lambda _: None)
    monkeypatch.delenv("OPENCODE", raising=False)
    monkeypatch.delenv("CLAUDECODE", raising=False)

    result = runner.invoke(app, ["init", "--yes", "--no-watch"])
    assert result.exit_code == 0, result.stdout

    settings = project / ".claude" / "settings.json"
    assert settings.exists(), f"settings.json not written: {result.stdout}"
    data = json.loads(settings.read_text())
    assert "SessionEnd" in data["hooks"]


# ── mcp add wires hook for Claude ──────────────────────────────────────────


def test_mcp_add_writes_claude_hook(tmp_path: Path, monkeypatch):
    """mcp add --agent claude should also write the SessionEnd hook."""
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    home.mkdir()
    (home / "config.yaml").write_text("install_source: pypi\n")

    monkeypatch.setenv("LOREKEEP_HOME", str(home))
    monkeypatch.chdir(project)

    result = runner.invoke(app, ["mcp", "add", "--agent", "claude", "--ns", "backend"])
    assert result.exit_code == 0, result.stdout

    settings = project / ".claude" / "settings.json"
    assert settings.exists()
    data = json.loads(settings.read_text())
    assert "SessionEnd" in data["hooks"]


def test_mcp_add_opencode_no_hook(tmp_path: Path, monkeypatch):
    """mcp add --agent opencode should NOT write a hook (no hook mechanism)."""
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    home.mkdir()
    (home / "config.yaml").write_text("install_source: pypi\n")

    monkeypatch.setenv("LOREKEEP_HOME", str(home))
    monkeypatch.chdir(project)

    result = runner.invoke(app, ["mcp", "add", "--agent", "opencode", "--ns", "backend"])
    assert result.exit_code == 0, result.stdout

    assert not (project / ".claude" / "settings.json").exists()


# ── Cursor hook ───────────────────────────────────────────────────────────


def test_write_cursor_hook(tmp_path: Path):
    """write_hook for Cursor creates .cursor/hooks.json with sessionEnd."""
    from lorekeep.integrations.cursor import write_hook as write_cursor_hook

    path = write_cursor_hook(tmp_path, "uvx", ["lorekeep", "hook"])
    assert path == tmp_path / ".cursor" / "hooks.json"
    assert path.exists()

    data = json.loads(path.read_text())
    assert data["version"] == 1
    hooks = data["hooks"]["sessionEnd"]
    assert len(hooks) == 1
    assert hooks[0]["command"] == "uvx lorekeep hook"
    assert hooks[0]["timeout"] == 30


def test_mcp_add_cursor_hook(tmp_path: Path, monkeypatch):
    """mcp add --agent cursor should write sessionEnd hook."""
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    home.mkdir()
    (home / "config.yaml").write_text("install_source: pypi\n")

    monkeypatch.setenv("LOREKEEP_HOME", str(home))
    monkeypatch.chdir(project)

    result = runner.invoke(app, ["mcp", "add", "--agent", "cursor", "--ns", "backend"])
    assert result.exit_code == 0, result.stdout

    hooks_path = project / ".cursor" / "hooks.json"
    assert hooks_path.exists()
    data = json.loads(hooks_path.read_text())
    assert "sessionEnd" in data["hooks"]
