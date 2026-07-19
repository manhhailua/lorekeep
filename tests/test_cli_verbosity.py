"""--verbose/--quiet/LOREKEEP_DEBUG wiring + ANSI-leak sweep under CliRunner."""
from __future__ import annotations

import logging
from pathlib import Path

from typer.testing import CliRunner

from lorekeep import output
from lorekeep.cli import app
from lorekeep.compile.providers import FakeProvider

runner = CliRunner()


class TestVerbosity:
    def _capture(self, monkeypatch):
        captured: dict[str, int] = {}
        monkeypatch.setattr(output, "configure_logging",
                            lambda lvl: captured.__setitem__("lvl", lvl))
        return captured

    def test_verbose_flag_sets_debug(self, monkeypatch):
        captured = self._capture(monkeypatch)
        r = runner.invoke(app, ["--verbose", "version"])
        assert r.exit_code == 0, r.output
        assert captured["lvl"] == logging.DEBUG

    def test_quiet_flag_sets_warning(self, monkeypatch):
        captured = self._capture(monkeypatch)
        r = runner.invoke(app, ["--quiet", "version"])
        assert r.exit_code == 0, r.output
        assert captured["lvl"] == logging.WARNING

    def test_lorekeep_debug_env_forces_debug(self, monkeypatch):
        captured = self._capture(monkeypatch)
        monkeypatch.setenv("LOREKEEP_DEBUG", "1")
        r = runner.invoke(app, ["version"])
        assert r.exit_code == 0, r.output
        assert captured["lvl"] == logging.DEBUG

    def test_default_is_info(self, monkeypatch):
        captured = self._capture(monkeypatch)
        monkeypatch.delenv("LOREKEEP_DEBUG", raising=False)
        r = runner.invoke(app, ["version"])
        assert r.exit_code == 0, r.output
        assert captured["lvl"] == logging.INFO


class TestNoAnsiUnderCliRunner:
    """Colorized commands must emit NO ANSI under CliRunner (non-tty)."""

    def _seed(self, tmp_path: Path, fixtures: Path):
        monkeypatch_dirs = {
            "LOREKEEP_RAW": str(tmp_path / "raw"),
            "LOREKEEP_OUT": str(tmp_path / "graph"),
            "LOREKEEP_CACHE": str(tmp_path / "cache.json"),
            "LOREKEEP_SCHEMA": str(fixtures / "schema.json"),
        }
        return monkeypatch_dirs

    def test_compile_success_no_ansi(self, monkeypatch, tmp_path: Path, fixtures: Path):
        for k, v in self._seed(tmp_path, fixtures).items():
            monkeypatch.setenv(k, v)
        raw = tmp_path / "raw/test/doc.md"
        raw.parent.mkdir(parents=True)
        raw.write_text("# Doc\ncontent\n")
        monkeypatch.setattr("lorekeep.cli._make_provider", lambda c: FakeProvider(responses=[]))
        # provide a canned extraction via patch_make_provider-style: use fake_extraction
        monkeypatch.setattr("lorekeep.cli._has_provider", lambda c: True)
        # Feed a real canned response
        import json as _json
        canned = _json.dumps({"nodes": [{"id": "svc:x", "type": "service", "name": "x"}], "edges": [], "aliases": {}})
        monkeypatch.setattr("lorekeep.cli._make_provider", lambda c: FakeProvider(responses=[canned] * 50))
        r = runner.invoke(app, ["compile"])
        assert r.exit_code == 0, r.output
        assert "\x1b[" not in r.output
        assert "compiled:" in r.output          # ✓ prefix, plain text survives

    def test_doctor_no_ansi(self, monkeypatch, tmp_path: Path, fixtures: Path):
        import shutil
        out = tmp_path / "graph"
        out.mkdir()
        shutil.copy(fixtures / "gold/payments.facts.jsonl", out / "facts.jsonl")
        monkeypatch.setenv("LOREKEEP_OUT", str(out))
        monkeypatch.setenv("LOREKEEP_SCHEMA", str(fixtures / "schema.json"))
        monkeypatch.setattr("lorekeep.cli._has_provider", lambda c: False)
        r = runner.invoke(app, ["doctor"])
        assert r.exit_code == 0, r.output
        assert "\x1b[" not in r.output
        assert "all checks passed" in r.output
