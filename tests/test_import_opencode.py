"""Tests for opencode session importer."""
import json
import sqlite3
from pathlib import Path

import pytest

from lorekeep.compile.providers import FakeProvider
from lorekeep.importer.opencode import (
    find_current_session,
    import_opencode,
    import_session_deep,
    parse_session,
)


def _build_db(db_path: Path, cwd: str, session_id: str = "ses_test001") -> str:
    """Build a synthetic opencode SQLite DB with one session."""
    con = sqlite3.connect(str(db_path))
    con.executescript("""
        CREATE TABLE project (id TEXT PRIMARY KEY, worktree TEXT);
        CREATE TABLE session (
            id TEXT PRIMARY KEY, project_id TEXT,
            time_created INTEGER, time_updated INTEGER, data TEXT
        );
        CREATE TABLE message (
            id TEXT PRIMARY KEY, session_id TEXT,
            time_created INTEGER, data TEXT
        );
        CREATE TABLE part (
            id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT,
            time_created INTEGER, data TEXT
        );
    """)

    project_id = "proj_test"
    con.execute("INSERT INTO project VALUES (?, ?)", (project_id, cwd))
    con.execute("INSERT INTO session VALUES (?, ?, ?, ?, ?)",
                (session_id, project_id, 1000, 2000,
                 json.dumps({"role": "system", "agent": "general"})))

    # User message + parts
    user_msg_id = "msg_user1"
    con.execute("INSERT INTO message VALUES (?, ?, ?, ?)",
                (user_msg_id, session_id, 1100,
                 json.dumps({"role": "user"})))
    con.execute("INSERT INTO part VALUES (?, ?, ?, ?, ?)",
                ("part_u1", user_msg_id, session_id, 1100,
                 json.dumps({"type": "text", "text": "What is the auth flow?"})))

    # Assistant message + parts
    asst_msg_id = "msg_asst1"
    con.execute("INSERT INTO message VALUES (?, ?, ?, ?)",
                (asst_msg_id, session_id, 1200,
                 json.dumps({"role": "assistant", "finish": "stop"})))
    con.execute("INSERT INTO part VALUES (?, ?, ?, ?, ?)",
                ("part_a1", asst_msg_id, session_id, 1200,
                 json.dumps({"type": "text", "text": "Auth uses JWT tokens."})))
    con.execute("INSERT INTO part VALUES (?, ?, ?, ?, ?)",
                ("part_a2", asst_msg_id, session_id, 1201,
                 json.dumps({"type": "tool", "tool": "bash", "callID": "call_1",
                             "state": {"status": "completed", "input": {"command": "ls"}}})))

    con.commit()
    con.close()
    return session_id


# ── find_current_session ─────────────────────────────────────────────────


def test_find_current_session_matches_cwd(tmp_path: Path, monkeypatch):
    db = tmp_path / "opencode.db"
    _build_db(db, str(tmp_path))

    monkeypatch.setattr("lorekeep.importer.opencode._opencode_db", lambda: db)
    result = find_current_session(cwd=tmp_path)
    assert result == "ses_test001"


def test_find_current_session_no_match(tmp_path: Path, monkeypatch):
    db = tmp_path / "opencode.db"
    _build_db(db, "/other/project")

    monkeypatch.setattr("lorekeep.importer.opencode._opencode_db", lambda: db)
    assert find_current_session(cwd=tmp_path) is None


def test_find_current_session_no_db(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("lorekeep.importer.opencode._opencode_db", lambda: tmp_path / "noexist.db")
    assert find_current_session() is None


# ── parse_session ────────────────────────────────────────────────────────


def test_parse_session_extracts_turns(tmp_path: Path, monkeypatch):
    db = tmp_path / "opencode.db"
    sid = _build_db(db, str(tmp_path))

    monkeypatch.setattr("lorekeep.importer.opencode._opencode_db", lambda: db)
    turns = parse_session(sid, db_path=db)
    assert len(turns) == 1
    assert "auth flow" in turns[0].user_content.lower()
    assert "JWT" in turns[0].assistant_text
    assert "bash" in turns[0].tool_calls


def test_parse_session_empty(tmp_path: Path):
    db = tmp_path / "opencode.db"
    _build_db(db, str(tmp_path))

    turns = parse_session("nonexistent", db_path=db)
    assert turns == []


# ── import_session_deep ──────────────────────────────────────────────────


def test_import_session_deep_writes_files(tmp_path: Path, monkeypatch):
    db = tmp_path / "opencode.db"
    sid = _build_db(db, str(tmp_path))

    raw_root = tmp_path / "raw"
    provider = FakeProvider(responses=["# Summary\n\n## Architecture\n- JWT auth\n"] * 50)

    result = import_session_deep(sid, raw_root, provider=provider, db_path=db)
    assert len(result) >= 1
    files = list((raw_root / "opencode-session").glob("session-*.md"))
    assert len(files) >= 1


def test_import_session_deep_idempotent(tmp_path: Path):
    db = tmp_path / "opencode.db"
    sid = _build_db(db, str(tmp_path))

    raw_root = tmp_path / "raw"
    provider = FakeProvider(responses=["# Summary\n"] * 50)

    first = import_session_deep(sid, raw_root, provider=provider, db_path=db)
    call_before = len(provider.calls)
    second = import_session_deep(sid, raw_root, provider=provider, db_path=db)
    assert len(first) >= 1
    assert second == []
    assert len(provider.calls) == call_before


# ── orchestrator ─────────────────────────────────────────────────────────


def test_import_opencode_deep(tmp_path: Path, monkeypatch):
    db = tmp_path / "opencode.db"
    sid = _build_db(db, str(tmp_path))

    monkeypatch.setattr("lorekeep.importer.opencode._opencode_db", lambda: db)
    monkeypatch.setattr("lorekeep.importer.opencode.find_current_session", lambda cwd=None: sid)

    raw_root = tmp_path / "raw"
    provider = FakeProvider(responses=["# Summary\n\n## Decisions\n- JWT\n"] * 50)

    result = import_opencode(raw_root, provider=provider)
    assert result["memory"] == []
    assert len(result["session"]) >= 1


def test_import_opencode_no_session(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("lorekeep.importer.opencode._opencode_db", lambda: tmp_path / "noexist.db")
    result = import_opencode(tmp_path / "raw")
    assert result["memory"] == []
    assert result["session"] == []
