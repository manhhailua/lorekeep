from pathlib import Path
import json
import pytest

from lorekeep.compile.providers import FakeProvider


@pytest.fixture
def fixtures() -> Path:
    return Path(__file__).parent / "fixtures"


CANNED_EXTRACTION = json.dumps({
    "nodes": [
        {"id": "svc:payments-api", "type": "service", "name": "payments-api",
         "props": {"lang": "go"}, "valid_from": "2024-01-15"},
        {"id": "svc:auth", "type": "service", "name": "auth"},
        {"id": "team:backend", "type": "team", "name": "team-backend"},
        {"id": "dec:adr-007", "type": "decision",
         "props": {"title": "payments-api adopts internal signing"}},
    ],
    "edges": [
        {"type": "depends_on", "from": "svc:payments-api", "to": "svc:auth",
         "valid_from": "2024-01-15", "valid_to": "2025-03-01"},
        {"type": "decided_by", "from": "dec:adr-007", "to": "team:backend"},
    ],
    "aliases": {},
})


@pytest.fixture
def fake_provider():
    """FakeProvider with canned extraction responses for compile/ingest tests."""
    return FakeProvider(responses=[CANNED_EXTRACTION] * 50)


@pytest.fixture
def fake_extraction():
    """Canned extraction JSON string for tests that build their own FakeProvider."""
    return CANNED_EXTRACTION


@pytest.fixture
def patch_make_provider(monkeypatch, fake_provider):
    """Monkeypatch cli._make_provider + _has_provider to return a FakeProvider."""
    monkeypatch.setattr("lorekeep.cli._make_provider", lambda config: fake_provider)
    monkeypatch.setattr("lorekeep.cli._has_provider", lambda config: True)


@pytest.fixture
def patch_make_import_provider(monkeypatch):
    """Monkeypatch cli._make_import_provider to return a FakeProvider for import deep mode."""
    canned = "# Knowledge Summary\n\n## Decisions\n- Test import summary.\n"
    monkeypatch.setattr(
        "lorekeep.cli._make_import_provider",
        lambda config: FakeProvider(responses=[canned] * 50),
    )
