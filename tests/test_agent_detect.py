"""Tests for agent auto-detection (env vars + filesystem markers)."""
from pathlib import Path
from lorekeep.integrations.detect import detect_active_agent, detect_installed_agents, detect_agents


def _isolate(monkeypatch, tmp_path):
    """Block all real env vars + filesystem markers + PATH lookups."""
    for v in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "OPENCODE", "CURSOR_DEBUG"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("lorekeep.integrations.detect.shutil.which", lambda _: None)


def test_detect_active_opencode(monkeypatch):
    _isolate(monkeypatch, Path("/tmp"))
    monkeypatch.setenv("OPENCODE", "1")
    assert detect_active_agent() == "opencode"


def test_detect_active_claude(monkeypatch):
    _isolate(monkeypatch, Path("/tmp"))
    monkeypatch.setenv("CLAUDECODE", "1")
    assert detect_active_agent() == "claude"


def test_detect_active_none(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    assert detect_active_agent() is None


def test_detect_active_falsy_env_ignored(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENCODE", "0")
    assert detect_active_agent() is None


def test_detect_installed_finds_markers(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".config" / "opencode").mkdir(parents=True)
    found = detect_installed_agents()
    assert "claude" in found
    assert "opencode" in found
    assert "codex" not in found


def test_detect_installed_empty(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    assert detect_installed_agents() == []


def test_detect_agents_active_overrides_installed(monkeypatch, tmp_path):
    """When inside an agent session, return only that agent."""
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENCODE", "1")
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".config" / "opencode").mkdir(parents=True)
    result = detect_agents()
    assert result == ["opencode"]


def test_detect_agents_no_active_returns_all_installed(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".cursor").mkdir()
    result = detect_agents()
    assert set(result) == {"claude", "cursor"}


from pathlib import Path
