import json
from pathlib import Path
from typer.testing import CliRunner
from lorekeep.cli import app

runner = CliRunner()


def test_agent_ingest_yes_flag(patch_make_provider, monkeypatch, tmp_path: Path, fixtures: Path):
    """agent ingest --yes: approve all facts, journal is created."""
    monkeypatch.setenv("LOREKEEP_RAW", str(tmp_path / "raw"))
    monkeypatch.setenv("LOREKEEP_OUT", str(tmp_path / "graph"))
    monkeypatch.setenv("LOREKEEP_CACHE", str(tmp_path / "cache.json"))
    monkeypatch.setenv("LOREKEEP_SCHEMA", str(fixtures / "schema.json"))
    monkeypatch.setenv("LOREKEEP_PENDING", str(tmp_path / "pending"))

    raw_file = tmp_path / "raw" / "backend" / "payments.md"
    raw_file.parent.mkdir(parents=True)
    raw_file.write_text((fixtures / "raw/backend/payments.md").read_text())

    result = runner.invoke(app, ["agent", "ingest", str(raw_file), "--yes"])
    assert result.exit_code == 0, result.stdout

    journal = tmp_path / "pending" / "backend" / "journal.jsonl"
    assert journal.exists(), f"journal not created at {journal}"

    lines = journal.read_text().strip().splitlines()
    assert len(lines) == 6  # 4 nodes + 2 edges

    for line in lines:
        entry = json.loads(line)
        assert entry["agent"] == "cli-ingest"
        assert entry["confidence"] == 1.0
        assert entry["status"] == "pending"


def test_agent_ingest_interactive_approve_all(patch_make_provider, monkeypatch, tmp_path: Path, fixtures: Path):
    """agent ingest: interactive mode, approve all nodes and edges."""
    monkeypatch.setenv("LOREKEEP_RAW", str(tmp_path / "raw"))
    monkeypatch.setenv("LOREKEEP_OUT", str(tmp_path / "graph"))
    monkeypatch.setenv("LOREKEEP_CACHE", str(tmp_path / "cache.json"))
    monkeypatch.setenv("LOREKEEP_SCHEMA", str(fixtures / "schema.json"))
    monkeypatch.setenv("LOREKEEP_PENDING", str(tmp_path / "pending"))

    raw_file = tmp_path / "raw" / "backend" / "payments.md"
    raw_file.parent.mkdir(parents=True)
    raw_file.write_text((fixtures / "raw/backend/payments.md").read_text())

    result = runner.invoke(
        app, ["agent", "ingest", str(raw_file)],
        input="y\ny\n",
    )
    assert result.exit_code == 0, result.stdout

    journal = tmp_path / "pending" / "backend" / "journal.jsonl"
    assert journal.exists()

    lines = journal.read_text().strip().splitlines()
    assert len(lines) == 6  # 4 nodes + 2 edges


def test_agent_ingest_interactive_reject_all(patch_make_provider, monkeypatch, tmp_path: Path, fixtures: Path):
    """agent ingest: interactive mode, reject all → no journal entries."""
    monkeypatch.setenv("LOREKEEP_RAW", str(tmp_path / "raw"))
    monkeypatch.setenv("LOREKEEP_OUT", str(tmp_path / "graph"))
    monkeypatch.setenv("LOREKEEP_CACHE", str(tmp_path / "cache.json"))
    monkeypatch.setenv("LOREKEEP_SCHEMA", str(fixtures / "schema.json"))
    monkeypatch.setenv("LOREKEEP_PENDING", str(tmp_path / "pending"))

    raw_file = tmp_path / "raw" / "backend" / "payments.md"
    raw_file.parent.mkdir(parents=True)
    raw_file.write_text((fixtures / "raw/backend/payments.md").read_text())

    result = runner.invoke(
        app, ["agent", "ingest", str(raw_file)],
        input="n\nn\nn\nn\n",
    )
    assert result.exit_code == 0, result.stdout
    assert "nothing approved" in result.stdout


def test_agent_ingest_interactive_review_individual(patch_make_provider, monkeypatch, tmp_path: Path, fixtures: Path):
    """agent ingest: interactive mode, reject all-at-once but approve individually."""
    monkeypatch.setenv("LOREKEEP_RAW", str(tmp_path / "raw"))
    monkeypatch.setenv("LOREKEEP_OUT", str(tmp_path / "graph"))
    monkeypatch.setenv("LOREKEEP_CACHE", str(tmp_path / "cache.json"))
    monkeypatch.setenv("LOREKEEP_SCHEMA", str(fixtures / "schema.json"))
    monkeypatch.setenv("LOREKEEP_PENDING", str(tmp_path / "pending"))

    raw_file = tmp_path / "raw" / "backend" / "payments.md"
    raw_file.parent.mkdir(parents=True)
    raw_file.write_text((fixtures / "raw/backend/payments.md").read_text())

    # n to reject all nodes at once, y to review individually,
    # y x4 to approve each of 4 nodes,
    # n to reject all edges, n to skip edge review
    result = runner.invoke(
        app, ["agent", "ingest", str(raw_file)],
        input="n\ny\ny\ny\ny\ny\nn\nn\n",
    )
    assert result.exit_code == 0, result.stdout

    journal = tmp_path / "pending" / "backend" / "journal.jsonl"
    assert journal.exists()

    lines = journal.read_text().strip().splitlines()
    assert len(lines) == 4  # 4 nodes approved, 0 edges


def test_agent_ingest_missing_source(patch_make_provider, monkeypatch, tmp_path: Path, fixtures: Path):
    """agent ingest: missing source file errors out."""
    monkeypatch.setenv("LOREKEEP_RAW", str(tmp_path / "raw"))
    monkeypatch.setenv("LOREKEEP_SCHEMA", str(fixtures / "schema.json"))
    monkeypatch.setenv("LOREKEEP_PENDING", str(tmp_path / "pending"))

    raw_root = tmp_path / "raw"
    raw_root.mkdir(parents=True)

    result = runner.invoke(app, ["agent", "ingest", str(raw_root / "nope.md")])
    assert result.exit_code == 1
    assert "not found" in result.stdout


def test_agent_ingest_source_outside_raw(patch_make_provider, monkeypatch, tmp_path: Path, fixtures: Path):
    """agent ingest: source outside raw/ errors out."""
    monkeypatch.setenv("LOREKEEP_RAW", str(tmp_path / "raw"))
    monkeypatch.setenv("LOREKEEP_SCHEMA", str(fixtures / "schema.json"))
    monkeypatch.setenv("LOREKEEP_PENDING", str(tmp_path / "pending"))

    raw_root = tmp_path / "raw"
    raw_root.mkdir(parents=True)
    outside = tmp_path / "outside.md"
    outside.write_text("# hello\n")

    result = runner.invoke(app, ["agent", "ingest", str(outside)])
    assert result.exit_code == 1
    assert "must be under raw/" in result.stdout
