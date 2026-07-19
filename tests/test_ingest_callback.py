"""agent.ingest_source on_progress callback."""
import json
from pathlib import Path

from lorekeep.agent import ingest_source
from lorekeep.compile.providers import FakeProvider
from lorekeep.models import Schema


def test_ingest_source_on_progress_called_per_chunk(tmp_path: Path, fixtures: Path, fake_extraction):
    raw = tmp_path / "raw"
    raw.mkdir()
    src = raw / "notes.md"
    src.write_text("# Doc\n" + "line.\n" * 200)  # >chunk_lines → multiple chunks
    schema = Schema.load(json.loads((fixtures / "schema.json").read_text()))
    provider = FakeProvider(responses=[fake_extraction] * 50)

    calls: list[tuple[int, int]] = []
    result = ingest_source(
        source_path=src, raw_root=raw, provider=provider, schema=schema,
        chunk_lines=60,
        on_progress=lambda i, total, chunk: calls.append((i, total)),
    )
    assert len(calls) == result.chunk_count
    assert all(total == result.chunk_count for _, total in calls)
    assert [i for i, _ in calls] == list(range(result.chunk_count))


def test_ingest_source_default_none_is_silent(tmp_path: Path, fixtures: Path, fake_extraction):
    raw = tmp_path / "raw"
    raw.mkdir()
    src = raw / "notes.md"
    src.write_text("# Doc\ncontent.\n")
    schema = Schema.load(json.loads((fixtures / "schema.json").read_text()))
    provider = FakeProvider(responses=[fake_extraction] * 50)
    result = ingest_source(src, raw, provider, schema)  # no callback
    assert result.chunk_count >= 1
