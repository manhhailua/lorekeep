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


def test_compile_total_failure_logs_errors(monkeypatch, tmp_path: Path, fixtures: Path, caplog):
    """Every compile error must reach the 'lorekeep' logger so daemon agent.log
    surfaces it (regression: daemon path was silent)."""
    import logging as _logging
    monkeypatch.setenv("LOREKEEP_RAW", str(tmp_path / "raw"))
    monkeypatch.setenv("LOREKEEP_OUT", str(tmp_path / "graph"))
    monkeypatch.setenv("LOREKEEP_CACHE", str(tmp_path / "cache.json"))
    monkeypatch.setenv("LOREKEEP_SCHEMA", str(fixtures / "schema.json"))
    raw = tmp_path / "raw/test/doc.md"
    raw.parent.mkdir(parents=True)
    raw.write_text("# Doc\ncontent\n")
    monkeypatch.setattr("lorekeep.cli._make_provider", lambda c: FakeProvider(responses=[]))
    monkeypatch.setattr("lorekeep.cli._has_provider", lambda c: True)

    with caplog.at_level(_logging.ERROR, logger="lorekeep"):
        result = runner.invoke(app, ["compile"])
    assert result.exit_code == 1
    assert any("compile error" in r.message for r in caplog.records)


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


def test_compile_rejects_bare_model_before_litellm(monkeypatch, tmp_path: Path, fixtures: Path):
    """Regression for the 2026-07-18 incident: a bare model name must fail fast
    with a suggestion at compile, not silently produce 0 nodes via litellm."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("provider:\n  model: deepseek-chat\napi_key: sk-test\n")
    monkeypatch.setenv("LOREKEEP_CONFIG", str(cfg))
    monkeypatch.setenv("LOREKEEP_RAW", str(tmp_path / "raw"))
    monkeypatch.setenv("LOREKEEP_OUT", str(tmp_path / "graph"))
    monkeypatch.setenv("LOREKEEP_CACHE", str(tmp_path / "cache.json"))
    monkeypatch.setenv("LOREKEEP_SCHEMA", str(fixtures / "schema.json"))
    raw = tmp_path / "raw/test/doc.md"
    raw.parent.mkdir(parents=True)
    raw.write_text("# Doc\nSome content.\n")

    result = runner.invoke(app, ["compile"])
    assert result.exit_code != 0
    # load_config raises ValueError with the suggestion; surface it clearly
    assert result.exception is not None
    assert "deepseek/deepseek-chat" in str(result.exception)


def test_compile_partial_systemic_errors_surface_all(monkeypatch, tmp_path: Path, fixtures: Path, fake_extraction):
    """≥3 chunks failing with the SAME error (partial — some nodes produced)
    must surface every error + a provider-config hint, not a one-line summary."""
    monkeypatch.setenv("LOREKEEP_RAW", str(tmp_path / "raw"))
    monkeypatch.setenv("LOREKEEP_OUT", str(tmp_path / "graph"))
    monkeypatch.setenv("LOREKEEP_CACHE", str(tmp_path / "cache.json"))
    monkeypatch.setenv("LOREKEEP_SCHEMA", str(fixtures / "schema.json"))

    # 4 chunks: first succeeds (produces nodes), next 3 fail identically.
    for name in ("ok.md", "bad1.md", "bad2.md", "bad3.md"):
        (tmp_path / "raw/test" / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "raw/test" / name).write_text(f"# {name}\ncontent.\n")

    class _Systematic(FakeProvider):
        def __init__(self):
            super().__init__(responses=[])
            self._n = 0

        def extract_json(self, system, user):
            self._n += 1
            if self._n == 1:
                return fake_extraction
            raise RuntimeError("LLM Provider NOT provided")

    monkeypatch.setattr("lorekeep.cli._make_provider", lambda config: _Systematic())
    monkeypatch.setattr("lorekeep.cli._has_provider", lambda config: True)

    result = runner.invoke(app, ["compile"])
    assert result.exit_code == 0, result.output  # partial compile is valid
    out = result.output.lower()
    assert "same error" in out
    assert "lorekeep doctor" in out
    assert out.count("llm provider not provided") >= 3  # every error echoed


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


def test_v4_compile_wiki_check_end_to_end(
    patch_make_provider, monkeypatch, tmp_path: Path,
):
    """Stock v4 compile produces enriched facts and a readable, valid vault."""
    from lorekeep.defaults import DEFAULT_CONFIG_YAML, DEFAULT_SCHEMA

    home = tmp_path / "home"
    (home / "raw" / "backend").mkdir(parents=True)
    (home / "raw" / "backend" / "payments.md").write_text(
        "# Payments\n\npayments-api depends on auth for credential validation.\n"
    )
    (home / "schema.json").write_text(json.dumps(DEFAULT_SCHEMA))
    (home / "config.yaml").write_text(DEFAULT_CONFIG_YAML)
    monkeypatch.setenv("LOREKEEP_HOME", str(home))

    compiled = runner.invoke(app, ["compile"])
    checked = runner.invoke(app, ["check"])
    regenerated = runner.invoke(app, ["wiki"])

    assert compiled.exit_code == 0, compiled.output
    assert checked.exit_code == 0, checked.output
    assert regenerated.exit_code == 0, regenerated.output
    manifest = json.loads((home / "graph" / "manifest.json").read_text())
    assert manifest["schema_version"] == 4
    assert manifest["content_quality"]["node_summary_coverage"] == 1.0
    assert manifest["content_quality"]["edge_description_coverage"] == 1.0
    page = (home / "wiki" / "svc-payments-api.md").read_text()
    assert "> Main API for payment requests." in page
    assert "## Connections" in page
    assert "Uses auth to validate incoming credentials." in page
    assert (home / "wiki" / "catalog.md").exists()
