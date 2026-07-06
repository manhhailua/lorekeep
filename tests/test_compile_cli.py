import json
from pathlib import Path
from typer.testing import CliRunner
from lorekeep.cli import app
from lorekeep.compile.providers import FakeProvider

runner = CliRunner()


def test_compile_command_uses_config_provider(patch_make_provider, monkeypatch, tmp_path: Path, fixtures: Path):
    # point the CLI at temp dirs via env
    monkeypatch.setenv("LOREKEEP_RAW", str(tmp_path / "raw"))
    monkeypatch.setenv("LOREKEEP_OUT", str(tmp_path / "graph"))
    monkeypatch.setenv("LOREKEEP_CACHE", str(tmp_path / "cache.json"))
    monkeypatch.setenv("LOREKEEP_SCHEMA", str(fixtures / "schema.json"))

    raw = tmp_path / "raw/backend/payments.md"
    raw.parent.mkdir(parents=True)
    raw.write_text((fixtures / "raw/backend/payments.md").read_text())

    result = runner.invoke(app, ["compile"])
    assert result.exit_code == 0, result.stdout
    assert (tmp_path / "graph/facts.jsonl").exists()


# ── compile error surfacing (regression: silent 0-node compile) ───────────────


def test_compile_total_failure_exits_nonzero(monkeypatch, tmp_path: Path, fixtures: Path):
    """When ALL chunks fail, compile must print errors and exit 1 — not silently
    report 'compiled: 0 nodes' with exit 0."""
    monkeypatch.setenv("LOREKEEP_RAW", str(tmp_path / "raw"))
    monkeypatch.setenv("LOREKEEP_OUT", str(tmp_path / "graph"))
    monkeypatch.setenv("LOREKEEP_CACHE", str(tmp_path / "cache.json"))
    monkeypatch.setenv("LOREKEEP_SCHEMA", str(fixtures / "schema.json"))

    raw = tmp_path / "raw/test/doc.md"
    raw.parent.mkdir(parents=True)
    raw.write_text("# Doc\nSome content.\n")

    # Provider with no responses → extract_json raises RuntimeError every call
    failing = FakeProvider(responses=[])
    monkeypatch.setattr("lorekeep.cli._make_provider", lambda config: failing)
    monkeypatch.setattr("lorekeep.cli._has_provider", lambda config: True)

    result = runner.invoke(app, ["compile"])
    assert result.exit_code == 1, result.output
    assert "chunk(s) failed" in result.output
    assert "0 nodes produced" in result.output
    assert "canned response left" in result.output  # the actual error message


def test_compile_partial_failure_exits_zero(monkeypatch, tmp_path: Path, fixtures: Path, fake_extraction):
    """When some chunks succeed but others fail, compile warns but exits 0
    (partial compile is valid)."""
    monkeypatch.setenv("LOREKEEP_RAW", str(tmp_path / "raw"))
    monkeypatch.setenv("LOREKEEP_OUT", str(tmp_path / "graph"))
    monkeypatch.setenv("LOREKEEP_CACHE", str(tmp_path / "cache.json"))
    monkeypatch.setenv("LOREKEEP_SCHEMA", str(fixtures / "schema.json"))

    # Two files → two chunks; first succeeds, second fails
    for name, content in [("ok.md", "# OK\nFine doc.\n"), ("bad.md", "# Bad\nBroken.\n")]:
        (tmp_path / "raw/test" / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "raw/test" / name).write_text(content)

    call_count = [0]

    class _Partial(FakeProvider):
        def extract_json(self, system, user):
            call_count[0] += 1
            if call_count[0] == 1:
                return fake_extraction
            raise RuntimeError("simulated extraction failure")

    partial = _Partial(responses=[])
    monkeypatch.setattr("lorekeep.cli._make_provider", lambda config: partial)
    monkeypatch.setattr("lorekeep.cli._has_provider", lambda config: True)

    result = runner.invoke(app, ["compile"])
    assert result.exit_code == 0, result.output
    assert "chunk(s) failed" in result.output
    assert "partial" in result.output


def test_compile_success_no_error_output(patch_make_provider, monkeypatch, tmp_path: Path, fixtures: Path):
    """When all chunks succeed, no error warning is printed."""
    monkeypatch.setenv("LOREKEEP_RAW", str(tmp_path / "raw"))
    monkeypatch.setenv("LOREKEEP_OUT", str(tmp_path / "graph"))
    monkeypatch.setenv("LOREKEEP_CACHE", str(tmp_path / "cache.json"))
    monkeypatch.setenv("LOREKEEP_SCHEMA", str(fixtures / "schema.json"))

    (tmp_path / "raw/test").mkdir(parents=True)
    (tmp_path / "raw/test/doc.md").write_text("# Doc\nFine.\n")

    result = runner.invoke(app, ["compile"])
    assert result.exit_code == 0, result.output
    assert "failed" not in result.output.lower()
