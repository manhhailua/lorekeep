"""Tests for Codex CLI session importer."""
import json
from pathlib import Path

import pytest

from lorekeep.compile.providers import FakeProvider
from lorekeep.importer.codex import (
    _strip_codex_prefix,
    find_current_session,
    import_codex,
    import_memories,
    import_session_deep,
    parse_rollout,
)


def _write_rollout(path: Path, cwd: str, turns: list[dict]) -> None:
    """Write a synthetic Codex rollout JSONL file."""
    lines = [json.dumps({
        "timestamp": "2026-06-28T10:00:00.000Z",
        "type": "session_meta",
        "payload": {
            "session_id": "test-session-001",
            "cwd": cwd,
            "originator": "codex-cli",
            "model_provider": "openai",
        },
    })]
    for turn in turns:
        lines.append(json.dumps(turn))
    path.write_text("\n".join(lines) + "\n")


def _user_msg(text: str) -> dict:
    return {
        "timestamp": "2026-06-28T10:00:01.000Z",
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": f"## My request for Codex:\n{text}"}],
        },
    }


def _assistant_msg(text: str) -> dict:
    return {
        "timestamp": "2026-06-28T10:00:02.000Z",
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": text}],
        },
    }


# ── parse_rollout ─────────────────────────────────────────────────────────


def test_parse_rollout_extracts_turns(tmp_path: Path):
    rollout = tmp_path / "rollout-test.jsonl"
    _write_rollout(rollout, str(tmp_path), [
        _user_msg("What is FastAPI?"),
        _assistant_msg("FastAPI is a web framework."),
    ])
    turns = parse_rollout(rollout)
    assert len(turns) == 1
    assert turns[0].user_content == "What is FastAPI?"
    assert "web framework" in turns[0].assistant_text


def test_parse_rollout_strips_codex_prefix(tmp_path: Path):
    rollout = tmp_path / "rollout-test.jsonl"
    _write_rollout(rollout, str(tmp_path), [
        _user_msg("Fix the bug"),
        _assistant_msg("Done."),
    ])
    turns = parse_rollout(rollout)
    assert "## My request for Codex:" not in turns[0].user_content


def test_parse_rollout_empty_file(tmp_path: Path):
    rollout = tmp_path / "rollout-empty.jsonl"
    rollout.write_text("")
    assert parse_rollout(rollout) == []


def test_strip_codex_prefix():
    assert _strip_codex_prefix("## My request for Codex:\nhello") == "hello"
    assert _strip_codex_prefix("no prefix") == "no prefix"


# ── find_current_session ─────────────────────────────────────────────────


def test_find_current_session_matches_cwd(tmp_path: Path, monkeypatch):
    codex_home = tmp_path / "codex"
    sessions_dir = codex_home / "sessions" / "2026" / "06" / "28"
    sessions_dir.mkdir(parents=True)

    rollout = sessions_dir / "rollout-test.jsonl"
    _write_rollout(rollout, str(tmp_path), [])

    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    result = find_current_session(cwd=tmp_path)
    assert result == rollout


def test_find_current_session_no_match(tmp_path: Path, monkeypatch):
    codex_home = tmp_path / "codex"
    sessions_dir = codex_home / "sessions" / "2026" / "06" / "28"
    sessions_dir.mkdir(parents=True)

    rollout = sessions_dir / "rollout-test.jsonl"
    _write_rollout(rollout, "/other/project", [])

    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    assert find_current_session(cwd=tmp_path) is None


def test_find_current_session_no_dir(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "noexist"))
    assert find_current_session() is None


# ── import_memories ──────────────────────────────────────────────────────


def test_import_memories_copies_files(tmp_path: Path, monkeypatch):
    codex_home = tmp_path / "codex"
    (codex_home / "memories").mkdir(parents=True)
    (codex_home / "memories" / "note.md").write_text("# Knowledge\nImportant fact.")

    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    raw_root = tmp_path / "raw"

    written = import_memories(raw_root)
    assert len(written) == 1
    assert (raw_root / "codex-memory" / "note.md").exists()


def test_import_memories_idempotent(tmp_path: Path, monkeypatch):
    codex_home = tmp_path / "codex"
    (codex_home / "memories").mkdir(parents=True)
    (codex_home / "memories" / "note.md").write_text("# Knowledge\n")

    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    raw_root = tmp_path / "raw"

    first = import_memories(raw_root)
    second = import_memories(raw_root)
    assert len(first) == 1
    assert len(second) == 0


def test_import_memories_no_dir(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "empty"))
    assert import_memories(tmp_path / "raw") == []


# ── import_session_deep ──────────────────────────────────────────────────


def test_import_session_deep_writes_files(tmp_path: Path):
    rollout = tmp_path / "rollout-test.jsonl"
    _write_rollout(rollout, str(tmp_path), [
        _user_msg("What is FastAPI?"),
        _assistant_msg("A web framework for Python."),
    ])
    raw_root = tmp_path / "raw"
    provider = FakeProvider(responses=["# Summary\n\n## Decisions\n- Use FastAPI\n"] * 50)

    result = import_session_deep(rollout, raw_root, provider=provider)
    assert len(result) >= 1
    files = list((raw_root / "codex-session").glob("session-*.md"))
    assert len(files) >= 1


def test_import_session_deep_idempotent(tmp_path: Path):
    rollout = tmp_path / "rollout-test.jsonl"
    _write_rollout(rollout, str(tmp_path), [
        _user_msg("Test"),
        _assistant_msg("Response"),
    ])
    raw_root = tmp_path / "raw"
    provider = FakeProvider(responses=["# Summary\n"] * 50)

    first = import_session_deep(rollout, raw_root, provider=provider)
    call_before = len(provider.calls)
    second = import_session_deep(rollout, raw_root, provider=provider)
    assert len(first) >= 1
    assert second == []
    assert len(provider.calls) == call_before


# ── orchestrator ─────────────────────────────────────────────────────────


def test_import_codex_deep(tmp_path: Path, monkeypatch):
    codex_home = tmp_path / "codex"
    sessions_dir = codex_home / "sessions" / "2026" / "06" / "28"
    sessions_dir.mkdir(parents=True)
    (codex_home / "memories").mkdir(parents=True)
    (codex_home / "memories" / "fact.md").write_text("# Fact\n")

    rollout = sessions_dir / "rollout-test.jsonl"
    _write_rollout(rollout, str(tmp_path), [
        _user_msg("Build an API"),
        _assistant_msg("Use FastAPI."),
    ])

    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    raw_root = tmp_path / "raw"
    provider = FakeProvider(responses=["# Summary\n\n## Architecture\n- FastAPI\n"] * 50)

    result = import_codex(raw_root, rollout_path=rollout, provider=provider)
    assert len(result["memory"]) == 1
    assert len(result["session"]) >= 1


def test_import_codex_quick_only(tmp_path: Path, monkeypatch):
    codex_home = tmp_path / "codex"
    (codex_home / "memories").mkdir(parents=True)
    (codex_home / "memories" / "fact.md").write_text("# Fact\n")

    rollout = codex_home / "sessions" / "2026" / "06" / "28" / "rollout-test.jsonl"
    rollout.parent.mkdir(parents=True)
    _write_rollout(rollout, str(tmp_path), [
        _user_msg("Test"),
        _assistant_msg("Response"),
    ])

    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    result = import_codex(tmp_path / "raw", rollout_path=rollout, quick=True)
    assert len(result["memory"]) == 1
    assert result["session"] == []
