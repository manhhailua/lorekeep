import shutil
from pathlib import Path
from typer.testing import CliRunner

from lorekeep.cli import app
from lorekeep.compile.providers import FakeProvider

runner = CliRunner()


def _seed_graph(tmp_path: Path, fixtures: Path) -> Path:
    out = tmp_path / "graph"
    out.mkdir(exist_ok=True)
    shutil.copy(fixtures / "gold/payments.facts.jsonl", out / "facts.jsonl")
    return out


def test_doctor_ok(tmp_path: Path, fixtures: Path, monkeypatch):
    out = _seed_graph(tmp_path, fixtures)
    monkeypatch.setenv("LOREKEEP_OUT", str(out))
    monkeypatch.setenv("LOREKEEP_SCHEMA", str(fixtures / "schema.json"))
    monkeypatch.setenv("LOREKEEP_NS", "teams/backend")
    # Keep the provider ping offline — this test is about graph/schema/MCP only.
    monkeypatch.setattr("lorekeep.cli._has_provider", lambda c: False)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.stdout
    assert "all checks passed" in result.stdout.lower()


def test_doctor_missing_graph(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LOREKEEP_OUT", str(tmp_path / "nope"))
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "facts.jsonl not found" in result.stdout.lower()


def test_doctor_pings_provider_when_key_present(tmp_path: Path, fixtures: Path, monkeypatch):
    out = _seed_graph(tmp_path, fixtures)
    monkeypatch.setenv("LOREKEEP_OUT", str(out))
    monkeypatch.setenv("LOREKEEP_SCHEMA", str(fixtures / "schema.json"))
    monkeypatch.setattr("lorekeep.cli._has_provider", lambda c: True)
    monkeypatch.setattr("lorekeep.cli._make_provider", lambda c: FakeProvider([]))
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.stdout
    assert "provider: ok" in result.stdout.lower()


def test_doctor_reports_auth_failure(tmp_path: Path, fixtures: Path, monkeypatch):
    out = _seed_graph(tmp_path, fixtures)
    monkeypatch.setenv("LOREKEEP_OUT", str(out))
    monkeypatch.setenv("LOREKEEP_SCHEMA", str(fixtures / "schema.json"))

    class _BadAuth:
        def ping(self):
            raise Exception("401 Authentication Error")

    monkeypatch.setattr("lorekeep.cli._has_provider", lambda c: True)
    monkeypatch.setattr("lorekeep.cli._make_provider", lambda c: _BadAuth())
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "auth failed" in result.stdout.lower()


def test_doctor_reports_model_not_found(tmp_path: Path, fixtures: Path, monkeypatch):
    out = _seed_graph(tmp_path, fixtures)
    monkeypatch.setenv("LOREKEEP_OUT", str(out))
    monkeypatch.setenv("LOREKEEP_SCHEMA", str(fixtures / "schema.json"))

    class _BadModel:
        def ping(self):
            raise Exception("NotFoundError: model not found")

    monkeypatch.setattr("lorekeep.cli._has_provider", lambda c: True)
    monkeypatch.setattr("lorekeep.cli._make_provider", lambda c: _BadModel())
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "model not found" in result.stdout.lower()


def test_doctor_skips_ping_when_no_key(tmp_path: Path, fixtures: Path, monkeypatch):
    out = _seed_graph(tmp_path, fixtures)
    monkeypatch.setenv("LOREKEEP_OUT", str(out))
    monkeypatch.setenv("LOREKEEP_SCHEMA", str(fixtures / "schema.json"))
    monkeypatch.setattr("lorekeep.cli._has_provider", lambda c: False)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.stdout
    assert "provider: skipped" in result.stdout.lower()


def test_doctor_reports_bare_model_as_problem(tmp_path: Path, fixtures: Path, monkeypatch):
    """A bare model name is reported as a problem, not a crash."""
    out = _seed_graph(tmp_path, fixtures)
    cfg = tmp_path / "config.yaml"
    cfg.write_text("provider:\n  model: deepseek-chat\n")
    monkeypatch.setenv("LOREKEEP_OUT", str(out))
    monkeypatch.setenv("LOREKEEP_SCHEMA", str(fixtures / "schema.json"))
    monkeypatch.setenv("LOREKEEP_CONFIG", str(cfg))
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "provider config" in result.stdout.lower()
    assert "deepseek/deepseek-chat" in result.stdout
