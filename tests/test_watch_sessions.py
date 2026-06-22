"""Tests for agent watch session import and auto-resolve chain."""
import json
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lorekeep.cli import app, _do_auto_resolve

runner = CliRunner()


# ── _do_auto_resolve ──────────────────────────────────────────────────────

def test_auto_resolve_merges_pending_journals(tmp_path: Path, fixtures: Path):
    """_do_auto_resolve reads pending journals and merges into facts.jsonl."""
    out_dir = tmp_path / "graph"
    out_dir.mkdir()
    pending_dir = tmp_path / "pending"
    pending_dir.mkdir()

    # Pre-populate facts.jsonl with gold data
    gold = fixtures / "gold" / "payments.facts.jsonl"
    import shutil
    shutil.copy(gold, out_dir / "facts.jsonl")

    # Add a pending journal entry
    ns_dir = pending_dir / "backend"
    ns_dir.mkdir()
    now = "2026-06-22T00:00:00Z"
    entry = {
        "agent": "test",
        "ns": "backend",
        "confidence": 1.0,
        "proposed_at": now,
        "status": "pending",
        "fact": {
            "kind": "node",
            "id": "svc:test-service",
            "type": "service",
            "ns": ["backend"],
            "props": {"name": "test"},
            "src": ["test.md"],
        },
    }
    (ns_dir / "journal.jsonl").write_text(json.dumps(entry, sort_keys=True) + "\n")

    # Run auto-resolve
    _do_auto_resolve(out_dir, pending_dir)

    # Facts should now include the new node
    lines = (out_dir / "facts.jsonl").read_text().strip().splitlines()
    nodes_in_graph = []
    for line in lines:
        f = json.loads(line)
        if f["kind"] == "node":
            nodes_in_graph.append(f["id"])
    assert "svc:payments-api" in nodes_in_graph  # original preserved
    assert "svc:test-service" in nodes_in_graph  # merged from journal

    # Journal status updated
    jlines = (ns_dir / "journal.jsonl").read_text().strip().splitlines()
    je = json.loads(jlines[0])
    assert je["status"] == "merged"


def test_auto_resolve_handles_empty_pending(tmp_path: Path, fixtures: Path):
    """_do_auto_resolve is a no-op when no pending entries exist."""
    out_dir = tmp_path / "graph"
    out_dir.mkdir()
    pending_dir = tmp_path / "pending"
    pending_dir.mkdir()

    # No journal files
    _do_auto_resolve(out_dir, pending_dir)

    # No crash, no facts.jsonl created (since nothing to write)
    assert not (out_dir / "facts.jsonl").exists()


def test_auto_resolve_preserves_graph_on_no_pending_entries(tmp_path: Path, fixtures: Path):
    """_do_auto_resolve keeps facts.jsonl unchanged when no pending entries."""
    import shutil
    out_dir = tmp_path / "graph"
    out_dir.mkdir()
    pending_dir = tmp_path / "pending"
    pending_dir.mkdir()

    gold = fixtures / "gold" / "payments.facts.jsonl"
    shutil.copy(gold, out_dir / "facts.jsonl")
    original = (out_dir / "facts.jsonl").read_text()

    # Pending dir has ns dir but journal has only already-merged entries
    ns_dir = pending_dir / "backend"
    ns_dir.mkdir()
    merged_entry = {
        "agent": "test",
        "ns": "backend",
        "confidence": 1.0,
        "proposed_at": "2026-06-22T00:00:00Z",
        "status": "merged",
        "fact": {"kind": "node", "id": "svc:dummy", "type": "service", "ns": [], "props": {}, "src": []},
    }
    (ns_dir / "journal.jsonl").write_text(json.dumps(merged_entry, sort_keys=True) + "\n")

    _do_auto_resolve(out_dir, pending_dir)

    # Facts unchanged (no pending entries → skip write)
    assert (out_dir / "facts.jsonl").read_text() == original


# ── agent watch CLI ───────────────────────────────────────────────────────


def test_watch_help_shows_session_flag():
    """agent watch --help mentions --watch-sessions."""
    result = runner.invoke(app, ["agent", "watch", "--help"])
    assert result.exit_code == 0
    assert "--watch-sessions" in result.stdout


def test_watch_boots_and_shuts_down_cleanly(monkeypatch, tmp_path: Path):
    """agent watch starts without error and exits cleanly on timeout."""
    # Use a minimal env with no raw/ or pending/ so nothing happens
    monkeypatch.setenv("LOREKEEP_RAW", str(tmp_path / "raw"))
    monkeypatch.setenv("LOREKEEP_OUT", str(tmp_path / "graph"))
    monkeypatch.setenv("LOREKEEP_CACHE", str(tmp_path / "cache.json"))
    monkeypatch.setenv("LOREKEEP_PENDING", str(tmp_path / "pending"))
    monkeypatch.setenv("LOREKEEP_PROVIDER", "fake")
    monkeypatch.setenv("LOREKEEP_DEV", "1")

    (tmp_path / "raw").mkdir()
    (tmp_path / "graph").mkdir()
    (tmp_path / "pending").mkdir()

    # Run with a tight interval, should exit on KeyboardInterrupt via timeout
    import subprocess
    import os
    result = subprocess.run(
        ["timeout", "2", "python3", "-m", "lorekeep.cli", "agent", "watch",
         "--interval", "1", "--no-watch-sessions"],
        capture_output=True, text=True,
        cwd=str(tmp_path.parent),
        env={**os.environ,
             "LOREKEEP_RAW": str(tmp_path / "raw"),
             "LOREKEEP_OUT": str(tmp_path / "graph"),
             "LOREKEEP_CACHE": str(tmp_path / "cache.json"),
             "LOREKEEP_PENDING": str(tmp_path / "pending"),
             "LOREKEEP_PROVIDER": "fake",
             "LOREKEEP_DEV": "1",
             "PYTHONPATH": str(Path(__file__).parent.parent / "src"),
             },
        timeout=5,
    )
    # Exit 124 = timeout (normal shutdown)
    assert result.returncode in (0, 124), f"stderr: {result.stderr}"


# ── Session delta import (import_memories) ────────────────────────────────


def test_import_memories_delta_imports_only_new_files(tmp_path: Path, fixtures: Path):
    """import_memories copies only new/changed files (idempotent delta)."""
    from lorekeep.importer.claude import import_memories

    session_dir = tmp_path / "session"
    session_dir.mkdir()
    mem_dir = session_dir / "memory"
    mem_dir.mkdir()

    raw_root = tmp_path / "raw"
    exported_files = import_memories(session_dir, raw_root, "test-ns")

    # Nothing to import
    assert exported_files == []

    # Write a new memory file
    (mem_dir / "new.md").write_text("# New\ncontent\n")
    exported_files = import_memories(session_dir, raw_root, "test-ns")
    assert len(exported_files) == 1
    assert (raw_root / "test-ns" / "new.md").exists()

    # Run again — should be idempotent (no new copies)
    exported_files = import_memories(session_dir, raw_root, "test-ns")
    assert exported_files == []

    # Modify the file — should detect change and re-import
    time.sleep(0.1)
    (mem_dir / "new.md").write_text("# Modified\ncontent v2\n")
    exported_files = import_memories(session_dir, raw_root, "test-ns")
    assert len(exported_files) == 1
    assert "content v2" in (raw_root / "test-ns" / "new.md").read_text()


def test_import_memories_handles_missing_memory_dir(tmp_path: Path):
    """import_memories returns empty when session/memory/ doesn't exist."""
    from lorekeep.importer.claude import import_memories

    session_dir = tmp_path / "no-memory"
    session_dir.mkdir()
    raw_root = tmp_path / "raw"

    exported_files = import_memories(session_dir, raw_root, "ns")
    assert exported_files == []
