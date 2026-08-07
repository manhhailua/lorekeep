from pathlib import Path
import json
import os
import signal
import subprocess
import pytest

from lorekeep.compile.providers import FakeProvider


@pytest.fixture(autouse=True)
def _disable_bugreport_in_tests(monkeypatch):
    """Prevent BugReportHandler from creating GitHub issues during tests.

    Individual test_bugreport.py tests unset this so they can verify the
    handler's real behaviour.
    """
    monkeypatch.setenv("LOREKEEP_BUGREPORT_TEST_MODE", "1")


@pytest.fixture(autouse=True)
def _kill_stray_daemons():
    """Kill any lorekeep daemon processes spawned by tests after each test.

    Some tests (e.g. test_init_interactive*) trigger _start_daemon via the
    real init flow, spawning background 'agent watch' processes. Without this
    cleanup, those processes accumulate as zombies across test runs.
    """
    yield
    try:
        result = subprocess.run(
            ["ps", "aux"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.splitlines():
            # Match only 'lorekeep.cli agent watch' daemons, not the test runner
            if "lorekeep.cli agent watch" in line and "grep" not in line and "pytest" not in line:
                parts = line.split()
                if len(parts) > 1:
                    try:
                        pid = int(parts[1])
                        os.kill(pid, signal.SIGTERM)
                    except (ProcessLookupError, ValueError, PermissionError):
                        pass
    except Exception:
        pass


@pytest.fixture
def fixtures() -> Path:
    return Path(__file__).parent / "fixtures"


CANNED_EXTRACTION = json.dumps({
    "nodes": [
        {"id": "svc:payments-api", "type": "service", "name": "payments-api",
         "summary": "Main API for payment requests.",
         "props": {"lang": "go"}, "valid_from": "2024-01-15"},
        {"id": "svc:auth", "type": "service", "name": "auth",
         "summary": "Validates service credentials."},
        {"id": "team:backend", "type": "team", "name": "team-backend",
         "summary": "Backend engineering team."},
        {"id": "dec:adr-007", "type": "decision",
         "summary": "Adopts internal request signing.",
         "props": {"title": "payments-api adopts internal signing"}},
    ],
    "edges": [
        {"type": "depends_on", "from": "svc:payments-api", "to": "svc:auth",
         "description": "Uses auth to validate incoming credentials.",
         "valid_from": "2024-01-15", "valid_to": "2025-03-01"},
        {"type": "decided_by", "from": "dec:adr-007", "to": "team:backend",
         "description": "The backend team approved the signing decision."},
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
