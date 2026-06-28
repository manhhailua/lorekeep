"""E2E tests: raw changes and pending journals become visible through MCP."""
import json
import os
import shutil
from pathlib import Path

from typer.testing import CliRunner

from lorekeep.cli import app, _do_auto_resolve
import lorekeep.mcp_server as ms

runner = CliRunner()


def _bootstrap_home(tmp_path: Path):
    """Create a minimal LOREKEEP_HOME with schema + config."""
    home = tmp_path / "home"
    home.mkdir()
    (home / "raw").mkdir()
    (home / "graph").mkdir()
    (home / "pending").mkdir()
    (home / "cache.json").write_text("{}")
    return home


def _set_env(home: Path):
    return {
        "LOREKEEP_HOME": str(home),
        "LOREKEEP_DEV": "0",
    }


# ── Compile → MCP visibility ──────────────────────────────────────────────


def test_raw_change_compiled_then_visible_in_mcp(patch_make_provider, tmp_path: Path, fixtures: Path, monkeypatch):
    """Write markdown to raw/ → compile (fake) → facts.jsonl → MCP search finds new nodes."""
    home = _bootstrap_home(tmp_path)
    shutil.copy(fixtures / "schema.json", home / "schema.json")

    md = home / "raw" / "backend" / "svc.md"
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text("# payments-api\nA go service.\n")

    for k, v in _set_env(home).items():
        monkeypatch.setenv(k, v)

    result = runner.invoke(app, ["compile"])
    assert result.exit_code == 0, result.output

    facts_path = home / "graph" / "facts.jsonl"
    assert facts_path.exists()

    ms.configure(
        graph_dir=home / "graph",
        allowed_ns=["backend"],
        schema_path=home / "schema.json",
    )
    ids = ms.search("payments")
    assert "svc:payments-api" in ids


# ── Pending journal → resolve → MCP visibility ────────────────────────────


def test_pending_journal_resolved_then_visible_in_mcp(tmp_path: Path, fixtures: Path, monkeypatch):
    """Write a journal entry → resolve → merged node visible via MCP get_node."""
    home = _bootstrap_home(tmp_path)

    shutil.copy(fixtures / "gold/payments.facts.jsonl", home / "graph" / "facts.jsonl")
    shutil.copy(fixtures / "schema.json", home / "schema.json")

    ns_dir = home / "pending" / "backend"
    ns_dir.mkdir(parents=True)
    entry = {
        "agent": "test",
        "ns": "backend",
        "confidence": 1.0,
        "proposed_at": "2026-06-28T00:00:00Z",
        "status": "pending",
        "fact": {
            "kind": "node",
            "id": "svc:journal-added",
            "type": "service",
            "ns": ["backend"],
            "props": {"name": "journal-added"},
            "src": [],
        },
    }
    (ns_dir / "journal.jsonl").write_text(json.dumps(entry, sort_keys=True) + "\n")

    _do_auto_resolve(home / "graph", home / "pending")

    ms.configure(
        graph_dir=home / "graph",
        allowed_ns=["backend"],
        schema_path=home / "schema.json",
    )
    node = ms.get_node("svc:journal-added")
    assert "error" not in node
    assert node["id"] == "svc:journal-added"


# ── Standalone compile preserves pending journals (PR1 fix) ───────────────


def test_standalone_compile_preserves_pending_journals(patch_make_provider, tmp_path: Path, fixtures: Path, monkeypatch):
    """lorekeep compile re-merges pending journals after regenerating facts.jsonl."""
    home = _bootstrap_home(tmp_path)
    shutil.copy(fixtures / "schema.json", home / "schema.json")

    md = home / "raw" / "backend" / "svc.md"
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text("# payments-api\nA go service.\n")

    ns_dir = home / "pending" / "backend"
    ns_dir.mkdir(parents=True)
    entry = {
        "agent": "test",
        "ns": "backend",
        "confidence": 1.0,
        "proposed_at": "2026-06-28T00:00:00Z",
        "status": "pending",
        "fact": {
            "kind": "node",
            "id": "svc:from-journal",
            "type": "service",
            "ns": ["backend"],
            "props": {"name": "from-journal"},
            "src": [],
        },
    }
    (ns_dir / "journal.jsonl").write_text(json.dumps(entry, sort_keys=True) + "\n")

    for k, v in _set_env(home).items():
        monkeypatch.setenv(k, v)

    result = runner.invoke(app, ["compile"])
    assert result.exit_code == 0, result.output

    facts_path = home / "graph" / "facts.jsonl"
    lines = [json.loads(l) for l in facts_path.read_text().strip().splitlines() if l.strip()]
    node_ids = [f["id"] for f in lines if f["kind"] == "node"]

    assert "svc:payments-api" in node_ids
    assert "svc:from-journal" in node_ids


# ── Full loop: raw write + journal → compile + resolve → MCP ──────────────


def test_full_loop_compile_and_resolve_visible_in_mcp(patch_make_provider, tmp_path: Path, fixtures: Path, monkeypatch):
    """Raw markdown + pending journal → compile → both visible through MCP search."""
    home = _bootstrap_home(tmp_path)
    shutil.copy(fixtures / "schema.json", home / "schema.json")

    md = home / "raw" / "backend" / "svc.md"
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text("# payments-api\nA go service.\n")

    ns_dir = home / "pending" / "backend"
    ns_dir.mkdir(parents=True)
    entry = {
        "agent": "test",
        "ns": "backend",
        "confidence": 0.9,
        "proposed_at": "2026-06-28T00:00:00Z",
        "status": "pending",
        "fact": {
            "kind": "node",
            "id": "svc:extra-from-journal",
            "type": "service",
            "ns": ["backend"],
            "props": {"name": "extra"},
            "src": [],
        },
    }
    (ns_dir / "journal.jsonl").write_text(json.dumps(entry, sort_keys=True) + "\n")

    for k, v in _set_env(home).items():
        monkeypatch.setenv(k, v)

    result = runner.invoke(app, ["compile"])
    assert result.exit_code == 0, result.output

    ms.configure(
        graph_dir=home / "graph",
        allowed_ns=["backend"],
        schema_path=home / "schema.json",
    )

    ids = ms.search("payments")
    assert "svc:payments-api" in ids

    node = ms.get_node("svc:extra-from-journal")
    assert "error" not in node
    assert node["props"]["name"] == "extra"

    journal_lines = (ns_dir / "journal.jsonl").read_text().strip().splitlines()
    je = json.loads(journal_lines[0])
    assert je["status"] == "merged"
