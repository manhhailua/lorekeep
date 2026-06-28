"""Import knowledge from opencode sessions into lorekeep raw/ tree.

opencode stores all sessions in a single SQLite database:
  ~/.local/share/opencode/opencode.db

Schema: session -> message -> part (JSON in data columns).
Sessions are linked to projects by worktree path (SHA-1 hashed as project_id).

opencode has no memory/*.md directory — deep-only (like cursor).
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path

from lorekeep.compile.providers import LLMProvider
from lorekeep.importer.claude import (
    ConversationTurn,
    chunk_turns,
    summarize_batch,
    load_import_manifest,
    save_import_manifest,
)


def _opencode_data_dir() -> Path:
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "opencode"
    return Path.home() / ".local" / "share" / "opencode"


def _opencode_db() -> Path:
    return _opencode_data_dir() / "opencode.db"


# ---------------------------------------------------------------------------
# Session discovery
# ---------------------------------------------------------------------------


def find_current_session(cwd: Path | None = None) -> str | None:
    """Find the most recent opencode session ID for the given cwd.

    Returns the session ID string, or None if no session found.
    """
    cwd = str((cwd or Path.cwd()).resolve())
    db = _opencode_db()
    if not db.is_file():
        return None

    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        row = con.execute(
            """SELECT s.id FROM session s
               JOIN project p ON p.id = s.project_id
               WHERE p.worktree = ?
               ORDER BY s.time_created DESC LIMIT 1""",
            (cwd,),
        ).fetchone()
        con.close()
        return row["id"] if row else None
    except sqlite3.Error:
        return None


# ---------------------------------------------------------------------------
# Transcript parsing
# ---------------------------------------------------------------------------


def _extract_turns(con: sqlite3.Connection, session_id: str) -> list[ConversationTurn]:
    """Extract conversation turns from opencode SQLite for a session."""
    messages = con.execute(
        "SELECT id, data FROM message WHERE session_id = ? ORDER BY time_created",
        (session_id,),
    ).fetchall()

    turns: list[ConversationTurn] = []
    current_user: str | None = None
    current_assistant: list[str] = []
    current_tools: list[str] = []

    for msg in messages:
        data = json.loads(msg["data"])
        role = data.get("role", "")

        parts = con.execute(
            "SELECT data FROM part WHERE message_id = ? ORDER BY time_created",
            (msg["id"],),
        ).fetchall()

        if role == "user":
            if current_user is not None:
                turns.append(ConversationTurn(
                    user_content=current_user,
                    assistant_text="\n\n".join(current_assistant),
                    tool_calls=list(current_tools),
                ))
            current_user = ""
            current_assistant = []
            current_tools = []
            for p in parts:
                pdata = json.loads(p["data"])
                if pdata.get("type") == "text":
                    current_user = (current_user + "\n" + pdata.get("text", "")).strip()

        elif role == "assistant":
            for p in parts:
                pdata = json.loads(p["data"])
                ptype = pdata.get("type", "")
                if ptype == "text":
                    current_assistant.append(pdata.get("text", ""))
                elif ptype == "tool":
                    tool_name = pdata.get("tool", "unknown")
                    if tool_name not in current_tools:
                        current_tools.append(tool_name)

    if current_user is not None:
        turns.append(ConversationTurn(
            user_content=current_user,
            assistant_text="\n\n".join(current_assistant),
            tool_calls=list(current_tools),
        ))

    return turns


def parse_session(session_id: str, db_path: Path | None = None) -> list[ConversationTurn]:
    """Parse an opencode session from SQLite into structured conversation turns."""
    db = db_path or _opencode_db()
    if not db.is_file():
        return []

    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        return _extract_turns(con, session_id)
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Deep session import
# ---------------------------------------------------------------------------


def import_session_deep(
    session_id: str,
    raw_root: Path,
    namespace: str = "opencode-session",
    provider: LLMProvider | None = None,
    db_path: Path | None = None,
    *,
    batch_max_chars: int = 20_000,
    dry_run: bool = False,
) -> list[Path]:
    """Deep-import an opencode session: parse, chunk, summarize each batch via LLM."""
    db = db_path or _opencode_db()
    if not db.is_file():
        return []

    manifest_key = f"opencode:{session_id}"

    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT time_updated FROM session WHERE id = ?", (session_id,)
        ).fetchone()
        if not row:
            return []
        content_hash = str(row["time_updated"])

        manifest = load_import_manifest(raw_root, namespace)
        if not dry_run and manifest.get(manifest_key) == content_hash:
            return []

        turns = _extract_turns(con, session_id)
    finally:
        con.close()

    if not turns:
        return []

    batches = chunk_turns(turns, max_chars=batch_max_chars)
    dest_dir = raw_root / namespace
    written: list[Path] = []

    previous_summary = ""
    for i, batch in enumerate(batches):
        if dry_run:
            dest = dest_dir / f"session-{session_id}-batch-{i + 1:02d}.md"
            written.append(dest)
            continue

        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"session-{session_id}-batch-{i + 1:02d}.md"

        try:
            if provider is None:
                raise RuntimeError("deep mode requires a provider")
            md = summarize_batch(
                batch, i, len(batches), namespace, session_id,
                provider, previous_summary,
            )
        except Exception as exc:
            md = f"# Error summarizing batch {i + 1}\n\nLLM call failed: {exc}\n"

        dest.write_text(md, encoding="utf-8")
        previous_summary = md[:1000]
        written.append(dest)

    if not dry_run and written:
        manifest[manifest_key] = content_hash
        save_import_manifest(raw_root, namespace, manifest)

    return written


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def import_opencode(
    raw_root: Path,
    session_id: str | None = None,
    *,
    session_ns: str = "opencode-session",
    provider: LLMProvider | None = None,
    batch_max_chars: int = 20_000,
    dry_run: bool = False,
) -> dict[str, list[Path]]:
    """Import an opencode session into the lorekeep raw/ tree.

    Deep-only — opencode has no memory/*.md directory.
    Returns ``{"memory": [], "session": [...]}``.
    """
    result: dict[str, list[Path]] = {"memory": [], "session": []}

    if session_id is None:
        session_id = find_current_session()
    if session_id is not None:
        result["session"] = import_session_deep(
            session_id, raw_root, namespace=session_ns,
            provider=provider, batch_max_chars=batch_max_chars,
            dry_run=dry_run,
        )

    return result
