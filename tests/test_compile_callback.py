"""compile_graph on_progress callback — output-only, must not change results."""
import json
from pathlib import Path

from lorekeep.compile.providers import FakeProvider
from lorekeep.models import Schema
from lorekeep.pipeline import compile_graph


def _setup(tmp_path: Path, fixtures: Path):
    raw = tmp_path / "raw"
    (raw / "teams/backend").mkdir(parents=True)
    (raw / "teams/backend/payments.md").write_text(
        (fixtures / "raw/backend/payments.md").read_text())
    schema = Schema.load(json.loads((fixtures / "schema.json").read_text()))
    return raw, schema


def test_on_progress_called_per_chunk(tmp_path: Path, fixtures: Path, fake_extraction):
    raw, schema = _setup(tmp_path, fixtures)
    provider = FakeProvider(responses=[fake_extraction] * 50)
    calls: list[tuple[int, int]] = []

    manifest = compile_graph(
        raw_root=raw, out_dir=tmp_path / "g1", schema=schema,
        provider=provider, cache_path=tmp_path / "c1.json",
        on_progress=lambda i, total, chunk: calls.append((i, total)),
    )
    assert len(calls) == manifest.chunk_count
    assert all(total == manifest.chunk_count for _, total in calls)
    assert [i for i, _ in calls] == list(range(manifest.chunk_count))


def test_on_progress_none_is_silent_default(tmp_path: Path, fixtures: Path, fake_extraction):
    """Default on_progress=None behaves exactly as before (no callback, no error)."""
    raw, schema = _setup(tmp_path, fixtures)
    provider = FakeProvider(responses=[fake_extraction] * 50)
    manifest = compile_graph(
        raw_root=raw, out_dir=tmp_path / "g2", schema=schema,
        provider=provider, cache_path=tmp_path / "c2.json",
    )
    assert manifest.chunk_count > 0


def test_on_progress_does_not_change_facts(tmp_path: Path, fixtures: Path, fake_extraction):
    """Determinism guard: progress callback is output-only — facts.jsonl identical."""
    raw, schema = _setup(tmp_path, fixtures)

    provider_a = FakeProvider(responses=[fake_extraction] * 50)
    compile_graph(raw_root=raw, out_dir=tmp_path / "ga", schema=schema,
                  provider=provider_a, cache_path=tmp_path / "ca.json")
    provider_b = FakeProvider(responses=[fake_extraction] * 50)
    compile_graph(raw_root=raw, out_dir=tmp_path / "gb", schema=schema,
                  provider=provider_b, cache_path=tmp_path / "cb.json",
                  on_progress=lambda i, t, c: None)
    a = (tmp_path / "ga/facts.jsonl").read_text()
    b = (tmp_path / "gb/facts.jsonl").read_text()
    assert a == b
