"""Import knowledge from Cursor composer conversations into lorekeep raw/ tree.

Cursor stores conversations GLOBALLY (not per-project) in a SQLite DB:

    <cursor-config>/User/globalStorage/state.vscdb
        table cursorDiskKV, keys ``composerData:<uuid>`` -> JSON blob:
            { composerId, text (initial user prompt),
              conversationMap: { bubbleId: { type/role, text/richText, createdAt } },
              context.folderSelections, createdAt, ... }

This importer extracts every populated composer conversation, parses its
bubbles into the same ``ConversationTurn`` shape the Claude importer uses,
and summarizes each via the shared LLM path. It is **deep-only** -- Cursor has
no equivalent of Claude Code's curated ``memory/*.md``, so there is no quick
path. Re-uses ``chunk_turns`` / ``summarize_batch`` / the import-manifest
helpers from :mod:`lorekeep.importer.claude` (DRY).

Caveat: Cursor frequently persists only conversation *headers* locally and
lazy-loads the full transcript from the cloud. On such installs
``conversationMap``/``text`` are empty and a conversation yields nothing -- the
importer reports "no importable conversations" rather than crashing. The DB is
opened read-only so Cursor's live process is never disturbed.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path

from lorekeep.compile.providers import LLMProvider
from lorekeep.importer.claude import (
    ConversationTurn,
    chunk_turns,
    load_import_manifest,
    save_import_manifest,
    summarize_batch,
)


# ---------------------------------------------------------------------------
# DB discovery
# ---------------------------------------------------------------------------


def _cursor_config_dir() -> Path:
    """Cursor's per-platform config dir (Linux / macOS)."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Cursor"
    return Path.home() / ".config" / "Cursor"


def default_cursor_db() -> Path:
    return _cursor_config_dir() / "User" / "globalStorage" / "state.vscdb"


def find_cursor_state_db() -> Path | None:
    """Return the global ``state.vscdb`` if it exists, else None.

    Honors the ``CURSOR_STATE_DB`` env override (point at the .vscdb file or
    its parent globalStorage dir).
    """
    env = os.environ.get("CURSOR_STATE_DB")
    if env:
        p = Path(env).expanduser()
        return p if p.is_file() else (p / "state.vscdb" if (p / "state.vscdb").is_file() else None)
    db = default_cursor_db()
    return db if db.is_file() else None


# ---------------------------------------------------------------------------
# Loading composer conversations
# ---------------------------------------------------------------------------


def load_composer_conversations(db_path: Path) -> list[dict]:
    """Read all populated ``composerData:<uuid>`` blobs from the global DB.

    Skips corrupt JSON and headers-only blobs (empty ``conversationMap`` AND
    empty ``text``). Returns blobs newest-first by ``createdAt``. A missing
    ``cursorDiskKV`` table yields ``[]``.
    """
    uri = f"file:{db_path}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    try:
        cur = con.cursor()
        try:
            rows = cur.execute(
                "SELECT value FROM cursorDiskKV WHERE key LIKE 'composerData:%'"
            ).fetchall()
        except sqlite3.DatabaseError:
            return []          # table missing / unreadable
    finally:
        con.close()

    blobs: list[dict] = []
    for (raw,) in rows:
        try:
            blob = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(blob, dict):
            continue
        cm = blob.get("conversationMap") or {}
        text = blob.get("text") or ""
        if not cm and not text:
            continue              # headers-only -- nothing to import
        blobs.append(blob)

    blobs.sort(key=lambda b: b.get("createdAt") or 0, reverse=True)
    return blobs


# ---------------------------------------------------------------------------
# Bubble -> ConversationTurn parsing
# ---------------------------------------------------------------------------


# Cursor encodes a bubble's speaker inconsistently across versions: sometimes a
# string role, sometimes a numeric type (1=user, 2=assistant). Accept the union.
_USER_TOKENS = {"user", "human", "you"}
_ASSISTANT_TOKENS = {"assistant", "ai", "model", "system"}


def _bubble_role(bubble: dict) -> str | None:
    """Resolve a bubble's role to 'user' | 'assistant' | None (unknown)."""
    for field in ("role", "type", "fromRole", "messageType"):
        val = bubble.get(field)
        if isinstance(val, str):
            v = val.lower()
            if v in _USER_TOKENS:
                return "user"
            if v in _ASSISTANT_TOKENS:
                return "assistant"
        elif isinstance(val, int):
            if val == 1:
                return "user"
            if val == 2:
                return "assistant"
    return None


def _bubble_text(bubble: dict) -> str:
    for field in ("text", "richText", "content"):
        val = bubble.get(field)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def parse_composer_turns(blob: dict) -> list[ConversationTurn]:
    """Parse a composer blob into ``ConversationTurn``s (user->assistant pairs).

    Bubbles are ordered by ``createdAt`` (numeric id fallback). Unknown-role
    bubbles are treated as assistant text (the common case). If
    ``conversationMap`` is empty but ``text`` is set, emits a single user turn.
    """
    cm = blob.get("conversationMap") or {}

    # Headers-only blob with just an initial prompt: emit one user turn.
    initial_text = (blob.get("text") or "").strip()
    if not cm:
        if initial_text:
            return [ConversationTurn(user_content=initial_text, assistant_text="")]
        return []

    def _order_key(item):
        _bid, b = item
        ca = b.get("createdAt")
        if isinstance(ca, (int, float)):
            return (0, ca)
        # fall back to a numeric leading portion of the bubble id
        digits = "".join(ch for ch in str(_bid) if ch.isdigit())
        return (1, int(digits) if digits else 0)

    ordered = sorted(cm.items(), key=_order_key)

    turns: list[ConversationTurn] = []
    cur_user: str | None = None
    cur_assistant: list[str] = []
    cur_tools: list[str] = []

    def _flush() -> None:
        nonlocal cur_user, cur_assistant, cur_tools
        if cur_user is not None or cur_assistant:
            turns.append(ConversationTurn(
                user_content=cur_user or "",
                assistant_text="\n\n".join(cur_assistant),
                tool_calls=list(cur_tools),
            ))
        cur_user, cur_assistant, cur_tools = None, [], []

    for _bid, bubble in ordered:
        if not isinstance(bubble, dict):
            continue
        role = _bubble_role(bubble)
        text = _bubble_text(bubble)
        if not text:
            continue
        if role == "user":
            _flush()
            cur_user = text
        else:                      # assistant or unknown -> assistant text
            cur_assistant.append(text)
            name = bubble.get("toolName") or bubble.get("name")
            if isinstance(name, str) and name and name not in cur_tools:
                cur_tools.append(name)

    _flush()
    return turns


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def _conversation_hash(blob: dict) -> str:
    """Stable hash of a conversation's content for idempotent re-import."""
    payload = {
        "composerId": blob.get("composerId"),
        "conversationMap": blob.get("conversationMap") or {},
        "text": blob.get("text") or "",
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def import_cursor(
    raw_root: Path,
    db_path: Path | None = None,
    *,
    namespace: str = "cursor-session",
    provider: LLMProvider | None,
    batch_max_chars: int = 20_000,
    dry_run: bool = False,
) -> dict[str, list[Path]]:
    """Deep-import all Cursor composer conversations into ``raw/<namespace>/``.

    Deep-only: requires a provider. Each conversation is chunked and LLM-
    summarized into per-batch markdown (re-using the Claude summarizer).
    Re-import is idempotent: a conversation whose content hash is unchanged
    since the last import is skipped.

    Returns ``{"session": [<written paths>]}``.
    """
    db = db_path or find_cursor_state_db()
    if db is None or not db.is_file():
        raise FileNotFoundError(
            "Cursor state.vscdb not found; set CURSOR_STATE_DB or pass --session-path"
        )
    if provider is None:
        raise RuntimeError("cursor import is deep-only and requires a provider")

    conversations = load_composer_conversations(db)
    if not conversations:
        return {"session": []}

    dest_dir = raw_root / namespace
    manifest = load_import_manifest(raw_root, namespace)
    written: list[Path] = []

    for blob in conversations:
        composer_id = str(blob.get("composerId") or "unknown")
        short = composer_id[:8]
        key = f"cursor:{composer_id}"
        content_hash = _conversation_hash(blob)
        if manifest.get(key) == content_hash:
            continue                 # unchanged since last import

        turns = parse_composer_turns(blob)
        if not turns:
            continue
        batches = chunk_turns(turns, max_chars=batch_max_chars)

        if dry_run:
            for i in range(len(batches)):
                written.append(dest_dir / f"cursor-{short}-batch-{i + 1:02d}.md")
            continue

        dest_dir.mkdir(parents=True, exist_ok=True)
        previous_summary = ""
        for i, batch in enumerate(batches):
            try:
                md = summarize_batch(
                    batch, i, len(batches), namespace, short,
                    provider, previous_summary,
                )
            except Exception as exc:
                md = f"# Error summarizing batch {i + 1}\n\nLLM call failed: {exc}\n"
            dest = dest_dir / f"cursor-{short}-batch-{i + 1:02d}.md"
            dest.write_text(md, encoding="utf-8")
            previous_summary = md[:1000]
            written.append(dest)

        manifest[key] = content_hash

    if not dry_run and (written or conversations):
        save_import_manifest(raw_root, namespace, manifest)

    return {"session": written}
