"""Tests for lorekeep.importer.cursor — global composer-conversation import.

The Cursor source is a SQLite DB (globalStorage/state.vscdb) we can't ship a
binary of, so each test builds a synthetic state.vscdb in tmp_path via stdlib
sqlite3 and exercises the real read path.
"""
import json
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lorekeep.cli import app
from lorekeep.compile.providers import FakeProvider
from lorekeep.importer.cursor import (
    find_cursor_state_db,
    import_cursor,
    load_composer_conversations,
    parse_composer_turns,
)

runner = CliRunner()


# ---------------------------------------------------------------------------
# synthetic state.vscdb builder
# ---------------------------------------------------------------------------


def _blob_populated() -> dict:
    """A composer with a real multi-turn conversationMap."""
    return {
        "composerId": "aaaa1111-2222-3333-4444-555566667777",
        "text": "how do I add auth to FastAPI?",
        "createdAt": 2000,
        "conversationMap": {
            "b1": {"type": "user", "text": "how do I add auth to FastAPI?", "createdAt": 1},
            "b2": {"type": "assistant", "text": "Use fastapi.security OAuth2.",
                   "createdAt": 2},
            "b3": {"type": "user", "text": "and store sessions in Redis?", "createdAt": 3},
            "b4": {"type": "assistant", "text": "Yes, redis-backed sessions work well.",
                   "createdAt": 4},
        },
    }


def _blob_headers_only() -> dict:
    """A headers-only composer (Cursor lazy-loads content; nothing local)."""
    return {
        "composerId": "bbbb0000-0000-0000-0000-000000000000",
        "text": "",
        "createdAt": 1000,
        "conversationMap": {},
        "fullConversationHeadersOnly": True,
    }


def build_state_db(path: Path, blobs: list[dict]) -> Path:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE cursorDiskKV (key TEXT PRIMARY KEY, value TEXT)")
    for blob in blobs:
        con.execute(
            "INSERT INTO cursorDiskKV (key, value) VALUES (?, ?)",
            (f"composerData:{blob['composerId']}", json.dumps(blob)),
        )
    con.commit()
    con.close()
    return path


@pytest.fixture
def state_db(tmp_path: Path) -> Path:
    return build_state_db(
        tmp_path / "state.vscdb", [_blob_populated(), _blob_headers_only()]
    )


@pytest.fixture
def empty_db(tmp_path: Path) -> Path:
    """A DB with the table but only headers-only blobs → nothing importable."""
    return build_state_db(tmp_path / "empty.vscdb", [_blob_headers_only()])


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------


def test_find_cursor_state_db_env_override(tmp_path: Path, monkeypatch):
    db = tmp_path / "state.vscdb"
    db.write_text("")  # exists
    monkeypatch.setenv("CURSOR_STATE_DB", str(db))
    assert find_cursor_state_db() == db


def test_find_cursor_state_db_missing(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("CURSOR_STATE_DB", str(tmp_path / "nope.vscdb"))
    assert find_cursor_state_db() is None


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------


def test_load_skips_headers_only_and_sorts_newest_first(state_db: Path):
    blobs = load_composer_conversations(state_db)
    assert len(blobs) == 1                       # headers-only blob dropped
    assert blobs[0]["composerId"].startswith("aaaa1111")


def test_load_missing_table_returns_empty(tmp_path: Path):
    db = tmp_path / "state.vscdb"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE other (k TEXT)")  # no cursorDiskKV
    con.commit(); con.close()
    assert load_composer_conversations(db) == []


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------


def test_parse_pairs_user_assistant():
    turns = parse_composer_turns(_blob_populated())
    assert len(turns) == 2
    assert "FastAPI" in turns[0].user_content
    assert "OAuth2" in turns[0].assistant_text
    assert "Redis" in turns[1].user_content
    assert "redis-backed" in turns[1].assistant_text


def test_parse_headers_only_with_text_emits_one_turn():
    blob = {"text": "a lone initial prompt", "conversationMap": {}}
    turns = parse_composer_turns(blob)
    assert len(turns) == 1
    assert turns[0].user_content == "a lone initial prompt"


def test_parse_empty_blob_returns_nothing():
    assert parse_composer_turns({"text": "", "conversationMap": {}}) == []


# ---------------------------------------------------------------------------
# orchestrator
# ---------------------------------------------------------------------------


def test_import_cursor_deep_writes_and_dedups(state_db: Path, tmp_path: Path):
    raw = tmp_path / "raw"
    provider = FakeProvider(responses=["# Summary\n- auth via OAuth2\n"] * 20)
    r1 = import_cursor(raw_root=raw, db_path=state_db, provider=provider)
    assert r1["session"], "expected batch files written"
    written = list((raw / "cursor-session").glob("*.md"))
    assert written, "batch md written under cursor-session/"

    # re-run is idempotent: nothing new written
    r2 = import_cursor(raw_root=raw, db_path=state_db, provider=provider)
    assert r2["session"] == []


def test_import_cursor_namespace(state_db: Path, tmp_path: Path):
    raw = tmp_path / "raw"
    provider = FakeProvider(responses=["# x\n"] * 20)
    import_cursor(raw_root=raw, db_path=state_db, namespace="my-cursor",
                  provider=provider)
    assert (raw / "my-cursor").is_dir()


def test_import_cursor_empty_db_returns_nothing(empty_db: Path, tmp_path: Path):
    raw = tmp_path / "raw"
    provider = FakeProvider(responses=["# x\n"] * 5)
    assert import_cursor(raw_root=raw, db_path=empty_db, provider=provider) == {"session": []}


def test_import_cursor_requires_provider(state_db: Path, tmp_path: Path):
    with pytest.raises(RuntimeError):
        import_cursor(raw_root=tmp_path / "raw", db_path=state_db, provider=None)


def test_import_cursor_missing_db_errors(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        import_cursor(raw_root=tmp_path / "raw", db_path=tmp_path / "nope.vscdb",
                      provider=FakeProvider(responses=[]))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_import_cursor_runs(patch_make_import_provider, monkeypatch, tmp_path: Path, state_db: Path):
    monkeypatch.setenv("LOREKEEP_RAW", str(tmp_path / "raw"))
    monkeypatch.setenv("LOREKEEP_OUT", str(tmp_path / "graph"))
    monkeypatch.setenv("LOREKEEP_CACHE", str(tmp_path / "cache.json"))
    monkeypatch.setenv("CURSOR_STATE_DB", str(state_db))

    result = runner.invoke(app, ["import", "--from", "cursor"])
    assert result.exit_code == 0, result.stdout
    assert "cursor-session" in result.stdout


def test_cli_import_cursor_rejects_quick(monkeypatch, tmp_path: Path, state_db: Path):
    monkeypatch.setenv("CURSOR_STATE_DB", str(state_db))
    result = runner.invoke(app, ["import", "--from", "cursor", "--quick"])
    assert result.exit_code == 1
    assert "deep-only" in result.stdout
