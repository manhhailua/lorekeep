"""Unit tests for the 5 MCP write tools (journal-based)."""
import json
import shutil
import tempfile
from pathlib import Path

import lorekeep.mcp_server as ms


def _setup(fixtures: Path, allowed, with_pending=True):
    d = Path(tempfile.mkdtemp())
    shutil.copy(fixtures / "gold/payments.facts.jsonl", d / "facts.jsonl")
    pending = d / "pending" if with_pending else None
    if with_pending:
        pending.mkdir()
    ms.configure(
        graph_dir=d,
        allowed_ns=allowed,
        schema_path=fixtures / "schema.json",
        pending_dir=pending,
    )
    return d, pending


def _journal_entries(pending: Path) -> list[dict]:
    entries = []
    for jf in sorted(pending.rglob("journal.jsonl")):
        for line in jf.read_text().splitlines():
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


# ── propose_fact ──────────────────────────────────────────────────────────


def test_propose_fact_writes_journal(fixtures: Path):
    d, pending = _setup(fixtures, ["backend"])
    fact = {
        "kind": "node",
        "id": "svc:new-service",
        "type": "service",
        "props": {"name": "new-service"},
    }
    r = ms.propose_fact(fact, confidence=0.9)
    assert r["accepted"] is True
    assert r["status"] == "pending"

    entries = _journal_entries(pending)
    assert len(entries) == 1
    assert entries[0]["fact"]["id"] == "svc:new-service"
    assert entries[0]["status"] == "pending"
    assert entries[0]["confidence"] == 0.9


def test_propose_fact_rejects_invalid_node_type(fixtures: Path):
    _setup(fixtures, ["backend"])
    fact = {"kind": "node", "id": "x", "type": "nonexistent", "props": {}}
    r = ms.propose_fact(fact, confidence=0.9)
    assert "error" in r


def test_propose_fact_strips_caller_ns(fixtures: Path):
    d, pending = _setup(fixtures, ["backend"])
    fact = {
        "kind": "node",
        "id": "svc:stripped",
        "type": "service",
        "ns": ["evil"],
        "props": {},
    }
    ms.propose_fact(fact, confidence=0.9)
    entries = _journal_entries(pending)
    assert "evil" not in entries[0]["fact"].get("ns", [])


def test_propose_fact_without_pending_dir(fixtures: Path):
    _setup(fixtures, ["backend"], with_pending=False)
    fact = {"kind": "node", "id": "x", "type": "service", "props": {}}
    r = ms.propose_fact(fact, confidence=0.9)
    assert "error" in r


# ── link_facts ────────────────────────────────────────────────────────────


def test_link_facts_writes_journal(fixtures: Path):
    d, pending = _setup(fixtures, ["backend"])
    r = ms.link_facts("svc:payments-api", "svc:auth", "depends_on", confidence=0.85)
    assert r["accepted"] is True

    entries = _journal_entries(pending)
    assert len(entries) == 1
    fact = entries[0]["fact"]
    assert fact["kind"] == "edge"
    assert fact["from"] == "svc:payments-api"
    assert fact["to"] == "svc:auth"
    assert fact["type"] == "depends_on"


def test_link_facts_rejects_unknown_from(fixtures: Path):
    _setup(fixtures, ["backend"])
    r = ms.link_facts("svc:nonexistent", "svc:auth", "depends_on", confidence=0.8)
    assert "error" in r


def test_link_facts_rejects_unknown_edge_type(fixtures: Path):
    _setup(fixtures, ["backend"])
    r = ms.link_facts("svc:payments-api", "svc:auth", "bogus_type", confidence=0.8)
    assert "error" in r


# ── flag_contradiction ───────────────────────────────────────────────────


def test_flag_contradiction_writes_journal(fixtures: Path):
    d, pending = _setup(fixtures, ["backend"])
    r = ms.flag_contradiction("svc:payments-api", "svc:auth", "mutually exclusive configs")
    assert r["accepted"] is True
    assert "contradiction" in r["id"]

    entries = _journal_entries(pending)
    assert len(entries) == 1
    assert entries[0]["confidence"] == 0.0


def test_flag_contradiction_without_pending_dir(fixtures: Path):
    _setup(fixtures, ["backend"], with_pending=False)
    r = ms.flag_contradiction("svc:payments-api", "svc:auth", "test")
    assert "error" in r


# ── update_fact ───────────────────────────────────────────────────────────


def test_update_fact_writes_journal(fixtures: Path):
    d, pending = _setup(fixtures, ["backend"])
    r = ms.update_fact("svc:payments-api", {"lang": "rust"}, confidence=0.8)
    assert r["accepted"] is True

    entries = _journal_entries(pending)
    assert len(entries) == 1
    assert entries[0]["fact"]["props"]["lang"] == "rust"


def test_update_fact_rejects_unknown_id(fixtures: Path):
    _setup(fixtures, ["backend"])
    r = ms.update_fact("svc:nonexistent", {"lang": "rust"}, confidence=0.8)
    assert "error" in r


# ── suggest_improvement ──────────────────────────────────────────────────


def test_suggest_improvement_writes_journal(fixtures: Path):
    d, pending = _setup(fixtures, ["backend"])
    r = ms.suggest_improvement("Add documentation for auth flow")
    assert r["accepted"] is True
    assert "suggestion" in r["id"]

    entries = _journal_entries(pending)
    assert len(entries) == 1
    assert entries[0]["fact"]["type"] == "note"


def test_suggest_improvement_without_pending_dir(fixtures: Path):
    _setup(fixtures, ["backend"], with_pending=False)
    r = ms.suggest_improvement("test suggestion")
    assert "error" in r
