"""Tests for daemon multi-agent session watch + file count tracking."""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from typer.testing import CliRunner

from lorekeep.cli import _discover_watchable_sessions, _quick_import_session

runner = CliRunner()


class TestDiscoverSessions:
    """Test multi-agent session discovery."""

    def test_returns_empty_when_no_agents(self, monkeypatch):
        monkeypatch.setattr(
            "lorekeep.importer.claude.find_current_session", lambda: None
        )
        monkeypatch.setattr(
            "lorekeep.importer.codex._codex_home", lambda: Path("/nonexistent")
        )
        result = _discover_watchable_sessions()
        assert result == []

    def test_finds_claude_session(self, tmp_path, monkeypatch):
        session_dir = tmp_path / "claude-session"
        (session_dir / "memory").mkdir(parents=True)
        (session_dir / "memory" / "note.md").write_text("# Note")

        monkeypatch.setattr(
            "lorekeep.importer.claude.find_current_session", lambda: session_dir
        )
        monkeypatch.setattr(
            "lorekeep.importer.codex._codex_home", lambda: Path("/nonexistent")
        )
        result = _discover_watchable_sessions()
        assert len(result) == 1
        assert result[0][0] == "claude"
        assert result[0][2] == session_dir / "memory"

    def test_finds_codex_session(self, tmp_path, monkeypatch):
        codex_home = tmp_path / "codex"
        (codex_home / "memories").mkdir(parents=True)
        (codex_home / "memories" / "note.md").write_text("# Note")

        monkeypatch.setattr(
            "lorekeep.importer.claude.find_current_session", lambda: None
        )
        monkeypatch.setattr(
            "lorekeep.importer.codex._codex_home", lambda: codex_home
        )
        result = _discover_watchable_sessions()
        assert len(result) == 1
        assert result[0][0] == "codex"

    def test_finds_both_agents(self, tmp_path, monkeypatch):
        claude_dir = tmp_path / "claude"
        (claude_dir / "memory").mkdir(parents=True)
        (claude_dir / "memory" / "note.md").write_text("# Claude")

        codex_home = tmp_path / "codex"
        (codex_home / "memories").mkdir(parents=True)
        (codex_home / "memories" / "note.md").write_text("# Codex")

        monkeypatch.setattr(
            "lorekeep.importer.claude.find_current_session", lambda: claude_dir
        )
        monkeypatch.setattr(
            "lorekeep.importer.codex._codex_home", lambda: codex_home
        )
        result = _discover_watchable_sessions()
        agents = [r[0] for r in result]
        assert "claude" in agents
        assert "codex" in agents

    def test_skips_empty_memory_dirs(self, tmp_path, monkeypatch):
        session_dir = tmp_path / "claude"
        (session_dir / "memory").mkdir(parents=True)
        # No .md files in memory/

        monkeypatch.setattr(
            "lorekeep.importer.claude.find_current_session", lambda: session_dir
        )
        monkeypatch.setattr(
            "lorekeep.importer.codex._codex_home", lambda: Path("/nonexistent")
        )
        result = _discover_watchable_sessions()
        assert result == []

    def test_re_discovers_on_each_call(self, tmp_path, monkeypatch):
        """Session discovery should find new sessions on subsequent calls."""
        call_count = [0]
        sessions = [None, tmp_path / "claude"]

        def mock_find():
            idx = min(call_count[0], len(sessions) - 1)
            result = sessions[idx]
            call_count[0] += 1
            return result

        monkeypatch.setattr(
            "lorekeep.importer.claude.find_current_session", mock_find
        )
        monkeypatch.setattr(
            "lorekeep.importer.codex._codex_home", lambda: Path("/nonexistent")
        )

        first = _discover_watchable_sessions()
        assert first == []

        (tmp_path / "claude" / "memory").mkdir(parents=True)
        (tmp_path / "claude" / "memory" / "note.md").write_text("# New")
        second = _discover_watchable_sessions()
        assert len(second) == 1
        assert second[0][0] == "claude"


class TestQuickImportSession:
    def test_claude_import(self, tmp_path):
        from lorekeep.cli import _quick_import_session

        session_dir = tmp_path / "session"
        mem_dir = session_dir / "memory"
        mem_dir.mkdir(parents=True)
        (mem_dir / "note.md").write_text("# Claude Note")

        raw_dir = tmp_path / "raw"
        raw_dir.mkdir()

        count = _quick_import_session("claude", session_dir, mem_dir, raw_dir)
        assert count == 1
        assert (raw_dir / "claude-memory" / "note.md").exists()

    def test_codex_import(self, tmp_path):
        from lorekeep.cli import _quick_import_session

        mem_dir = tmp_path / "memories"
        mem_dir.mkdir(parents=True)
        (mem_dir / "codex-note.md").write_text("# Codex Note")

        raw_dir = tmp_path / "raw"
        raw_dir.mkdir()

        codex_home = tmp_path / "codex"
        codex_home.mkdir()

        with patch("lorekeep.importer.codex._codex_home", return_value=codex_home):
            # Move memories under codex_home for the import to find
            (codex_home / "memories").mkdir()
            (codex_home / "memories" / "codex-note.md").write_text("# Codex Note")
            count = _quick_import_session("codex", codex_home, codex_home / "memories", raw_dir)
        assert count == 1
        assert (raw_dir / "codex-memory" / "codex-note.md").exists()

    def test_unknown_agent_returns_zero(self, tmp_path):
        count = _quick_import_session("unknown", tmp_path, tmp_path, tmp_path)
        assert count == 0


class TestRawFileCountTracking:
    """Test that new files in raw/ trigger compile (not just mtime)."""

    def test_new_file_detected_on_second_cycle(self, tmp_path, monkeypatch):
        """Simulate the daemon logic: add a file → detect change via count."""
        raw_dir = tmp_path / "raw"
        raw_dir.mkdir()
        (raw_dir / "a.md").write_text("# A")

        # Simulate first cycle: snapshot state
        files = sorted(raw_dir.rglob("*.md"))
        last_count = len(files)
        last_mtime = max(f.stat().st_mtime for f in files)
        assert last_count == 1

        # Add new file (same mtime tick possible on fast FS)
        time.sleep(0.01)
        (raw_dir / "b.md").write_text("# B")

        # Second cycle: detect change
        files = sorted(raw_dir.rglob("*.md"))
        new_count = len(files)
        new_mtime = max(f.stat().st_mtime for f in files)

        should_compile = False
        if last_count >= 0:
            if new_count != last_count:
                should_compile = True
            elif new_mtime > last_mtime:
                should_compile = True

        assert should_compile is True

    def test_no_change_no_compile(self, tmp_path):
        raw_dir = tmp_path / "raw"
        raw_dir.mkdir()
        (raw_dir / "a.md").write_text("# A")

        files = sorted(raw_dir.rglob("*.md"))
        last_count = len(files)
        last_mtime = max(f.stat().st_mtime for f in files)

        # No changes
        files2 = sorted(raw_dir.rglob("*.md"))
        new_count = len(files2)
        new_mtime = max(f.stat().st_mtime for f in files2)

        should_compile = new_count != last_count or new_mtime > last_mtime
        assert should_compile is False

    def test_file_modified_detected(self, tmp_path):
        raw_dir = tmp_path / "raw"
        raw_dir.mkdir()
        (raw_dir / "a.md").write_text("# A")

        files = sorted(raw_dir.rglob("*.md"))
        last_count = len(files)
        last_mtime = max(f.stat().st_mtime for f in files)

        # Modify file (same count, different mtime)
        time.sleep(0.01)
        (raw_dir / "a.md").write_text("# A updated")

        files2 = sorted(raw_dir.rglob("*.md"))
        new_count = len(files2)
        new_mtime = max(f.stat().st_mtime for f in files2)

        should_compile = new_count != last_count or new_mtime > last_mtime
        assert should_compile is True


class TestInitProducesGraph:
    """Test that init produces a graph when API key is provided."""

    def test_init_compiles_about_md(self, tmp_path, monkeypatch, patch_make_provider):
        """Init with API key should compile about.md → facts.jsonl + wiki."""
        from lorekeep.cli import app
        from lorekeep.providers import ModelInfo

        home = tmp_path / "home"
        monkeypatch.setenv("LOREKEEP_HOME", str(home))
        monkeypatch.setattr("lorekeep.cli._is_interactive", lambda: True)
        monkeypatch.setattr(
            "lorekeep.providers.list_models",
            lambda p: [ModelInfo("gpt-4o-mini", p, "chat", 0.15e-6, 0.6e-6, 128000, True)]
        )
        monkeypatch.setattr("lorekeep.cli._start_daemon", lambda p: None)

        # provider=1 (openai), model=1, key=sk-test, ns=myteam, name=Alice, bio=builder
        result = runner.invoke(app, ["init"], input="1\n1\nsk-testKEY\nmyteam\nAlice\nBuilds backend\n")
        assert result.exit_code == 0, result.stdout

        facts = home / "graph" / "facts.jsonl"
        assert facts.exists(), "facts.jsonl should exist after init compile"

        wiki = home / "wiki" / "index.md"
        assert wiki.exists(), "wiki/index.md should exist after init compile"

        about = home / "raw" / "myteam" / "about.md"
        assert about.exists()
        assert "Alice" in about.read_text()
