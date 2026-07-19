"""`lorekeep wiki --open` launches Obsidian via the obsidian:// URL scheme."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

from typer.testing import CliRunner

from lorekeep.cli import app

runner = CliRunner()


def _seed(tmp_path: Path, fixtures: Path) -> tuple[Path, Path]:
    out = tmp_path / "graph"
    out.mkdir()
    (out / "facts.jsonl").write_text((fixtures / "gold/payments.facts.jsonl").read_text())
    wiki = tmp_path / "wiki"
    return out, wiki


def test_wiki_open_launches_obsidian_url(monkeypatch, tmp_path: Path, fixtures: Path):
    out, wiki = _seed(tmp_path, fixtures)
    monkeypatch.setenv("LOREKEEP_OUT", str(out))
    monkeypatch.setenv("LOREKEEP_WIKI", str(wiki))

    captured: list[list[str]] = []
    real_run = subprocess.run

    def _fake_run(args, *a, **k):
        captured.append(list(args))
        return MagicMock(returncode=0)

    monkeypatch.setattr(subprocess, "run", _fake_run)
    result = runner.invoke(app, ["wiki", "--open"])

    assert result.exit_code == 0, result.output
    assert "wiki:" in result.output                  # generation result line
    # exactly one opener call, with an obsidian:// URL containing the wiki path
    obs_calls = [c for c in captured if c and str(c[-1]).startswith("obsidian://")]
    assert len(obs_calls) == 1
    url = obs_calls[0][-1]
    assert url.startswith("obsidian://open?path=")
    # the resolved wiki dir is encoded in the URL
    assert str(wiki.resolve()).lstrip("/") in url.replace("%2F", "/").replace("%5C", "\\")


def test_wiki_without_open_does_not_spawn(monkeypatch, tmp_path: Path, fixtures: Path):
    out, wiki = _seed(tmp_path, fixtures)
    monkeypatch.setenv("LOREKEEP_OUT", str(out))
    monkeypatch.setenv("LOREKEEP_WIKI", str(wiki))

    def _fail(*a, **k):
        raise AssertionError("subprocess spawned without --open")

    monkeypatch.setattr(subprocess, "run", _fail)
    result = runner.invoke(app, ["wiki"])
    assert result.exit_code == 0, result.output


def test_wiki_open_survives_missing_opener(monkeypatch, tmp_path: Path, fixtures: Path):
    """If the platform opener is missing, --open warns (non-fatal) — wiki still generated."""
    out, wiki = _seed(tmp_path, fixtures)
    monkeypatch.setenv("LOREKEEP_OUT", str(out))
    monkeypatch.setenv("LOREKEEP_WIKI", str(wiki))

    def _missing(*a, **k):
        raise FileNotFoundError("no opener")

    monkeypatch.setattr(subprocess, "run", _missing)
    result = runner.invoke(app, ["wiki", "--open"])
    assert result.exit_code == 0, result.output          # non-fatal
    flat = result.output.replace("\n", "")
    assert "could not launch Obsidian" in flat
    assert str(wiki) in flat
