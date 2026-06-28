"""Import knowledge from Codex CLI sessions into lorekeep raw/ tree.

Codex stores sessions as JSONL rollout files at:
  $CODEX_HOME/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl

Each line = {"timestamp": "...", "type": "...", "payload": {...}}
The first line is always "session_meta" with cwd, session_id, model.
Conversation turns are in "response_item" lines with payload.type == "message".

Memory files live at $CODEX_HOME/memories/*.md (optional, may not exist).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from lorekeep.compile.providers import LLMProvider
from lorekeep.importer.claude import (
    ConversationTurn,
    chunk_turns,
    summarize_batch,
    load_import_manifest,
    save_import_manifest,
)

_CODEX_PREFIX_RE = re.compile(r"^## My request for Codex:\n", re.DOTALL)


def _codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))


# ---------------------------------------------------------------------------
# Session discovery
# ---------------------------------------------------------------------------


def find_current_session(cwd: Path | None = None) -> Path | None:
    """Find the most recent Codex rollout file for the given cwd.

    Codex shards sessions by date, not by project. The cwd is stored
    inside the first line (session_meta) of each rollout file.
    """
    cwd = str((cwd or Path.cwd()).resolve())
    sessions_dir = _codex_home() / "sessions"
    if not sessions_dir.is_dir():
        return None

    rollouts = sorted(sessions_dir.rglob("rollout-*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    for rp in rollouts:
        try:
            first_line = rp.read_text(encoding="utf-8").splitlines()[0].strip()
            if not first_line:
                continue
            rec = json.loads(first_line)
            if rec.get("type") == "session_meta":
                session_cwd = rec.get("payload", {}).get("cwd", "")
                if session_cwd and Path(session_cwd).resolve() == Path(cwd):
                    return rp
        except (json.JSONDecodeError, IndexError, OSError):
            continue
    return None


# ---------------------------------------------------------------------------
# Transcript parsing
# ---------------------------------------------------------------------------


def _extract_message_text(content: list[dict] | str) -> str:
    """Extract text from a Codex message content array."""
    if isinstance(content, str):
        return content
    parts = []
    for block in content:
        if isinstance(block, dict):
            text = block.get("text", "")
            if text:
                parts.append(text)
    return "\n".join(parts)


def _strip_codex_prefix(text: str) -> str:
    """Strip Codex's '## My request for Codex:' prefix from user messages."""
    return _CODEX_PREFIX_RE.sub("", text)


def parse_rollout(rollout_path: Path) -> list[ConversationTurn]:
    """Parse a Codex rollout JSONL file into structured conversation turns."""
    turns: list[ConversationTurn] = []
    current_user: str | None = None
    current_assistant: list[str] = []
    current_tools: list[str] = []

    for line in rollout_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue

        if rec.get("type") != "response_item":
            continue

        payload = rec.get("payload", {})
        ptype = payload.get("type", "")

        if ptype == "message":
            role = payload.get("role", "")
            text = _extract_message_text(payload.get("content", []))

            if role == "user":
                if current_user is not None:
                    turns.append(ConversationTurn(
                        user_content=current_user,
                        assistant_text="\n\n".join(current_assistant),
                        tool_calls=list(current_tools),
                    ))
                current_user = _strip_codex_prefix(text)
                current_assistant = []
                current_tools = []
            elif role == "assistant":
                current_assistant.append(text)

        elif ptype in ("function_call", "custom_tool_call"):
            name = payload.get("name", "unknown")
            if name not in current_tools:
                current_tools.append(name)

    if current_user is not None:
        turns.append(ConversationTurn(
            user_content=current_user,
            assistant_text="\n\n".join(current_assistant),
            tool_calls=list(current_tools),
        ))

    return turns


# ---------------------------------------------------------------------------
# Memory import (quick mode)
# ---------------------------------------------------------------------------


def import_memories(
    raw_root: Path,
    namespace: str = "codex-memory",
    *,
    dry_run: bool = False,
) -> list[Path]:
    """Copy memories/*.md files from $CODEX_HOME into raw/<namespace>/.

    Returns list of written paths. Idempotent via SHA-256 manifest.
    """
    mem_dir = _codex_home() / "memories"
    if not mem_dir.is_dir():
        return []

    dest_dir = raw_root / namespace
    manifest = load_import_manifest(raw_root, namespace)
    written: list[Path] = []

    for src in sorted(mem_dir.glob("*.md")):
        content = src.read_text(encoding="utf-8")
        h = hashlib.sha256(content.encode()).hexdigest()
        if manifest.get(str(src)) == h:
            continue

        if dry_run:
            written.append(dest_dir / src.name)
            continue

        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name
        dest.write_text(content, encoding="utf-8")
        manifest[str(src)] = h
        written.append(dest)

    if not dry_run and written:
        save_import_manifest(raw_root, namespace, manifest)

    return written


# ---------------------------------------------------------------------------
# Deep session import
# ---------------------------------------------------------------------------


def import_session_deep(
    rollout_path: Path,
    raw_root: Path,
    namespace: str = "codex-session",
    provider: LLMProvider | None = None,
    *,
    batch_max_chars: int = 20_000,
    dry_run: bool = False,
) -> list[Path]:
    """Deep-import a Codex session: parse, chunk, summarize each batch via LLM."""
    session_id = rollout_path.stem

    transcript_hash = hashlib.sha256(rollout_path.read_bytes()).hexdigest()
    manifest = load_import_manifest(raw_root, namespace)
    manifest_key = str(rollout_path)

    if not dry_run and manifest.get(manifest_key) == transcript_hash:
        return []

    turns = parse_rollout(rollout_path)
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
        manifest[manifest_key] = transcript_hash
        save_import_manifest(raw_root, namespace, manifest)

    return written


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def import_codex(
    raw_root: Path,
    rollout_path: Path | None = None,
    *,
    quick: bool = False,
    memory_ns: str = "codex-memory",
    session_ns: str = "codex-session",
    provider: LLMProvider | None = None,
    batch_max_chars: int = 20_000,
    dry_run: bool = False,
) -> dict[str, list[Path]]:
    """Import a Codex session into the lorekeep raw/ tree.

    Returns ``{"memory": [...], "session": [...]}`` with written file paths.
    """
    result: dict[str, list[Path]] = {"memory": [], "session": []}

    result["memory"] = import_memories(raw_root, namespace=memory_ns, dry_run=dry_run)

    if not quick:
        if rollout_path is None:
            rollout_path = find_current_session()
        if rollout_path is not None:
            result["session"] = import_session_deep(
                rollout_path, raw_root, namespace=session_ns,
                provider=provider, batch_max_chars=batch_max_chars,
                dry_run=dry_run,
            )

    return result
