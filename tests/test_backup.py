import subprocess
from pathlib import Path

import pytest

from lorekeep.backup import BACKUP_GITIGNORE, BackupError, backup, init_backup


def _bare_remote(tmp_path: Path) -> str:
    """A local bare repo usable as a push remote (file:// not required for path remote)."""
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True)
    return str(bare)


def _tracked(home: Path) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=home, capture_output=True, text=True, check=True
    ).stdout
    return out.split()


def _log(home: Path) -> str:
    return subprocess.run(
        ["git", "log", "--oneline"], cwd=home, capture_output=True, text=True, check=True
    ).stdout


def test_init_backup_creates_repo_gitignore_and_remote(tmp_path: Path):
    home = tmp_path / "home"
    (home / "raw" / "ns").mkdir(parents=True)
    (home / "raw" / "ns" / "a.md").write_text("# a")
    remote = _bare_remote(tmp_path)
    init_backup(home, remote)
    assert (home / ".git").is_dir()
    assert (home / ".gitignore").read_text() == BACKUP_GITIGNORE
    refs = subprocess.run(
        ["git", "ls-remote", remote], capture_output=True, text=True, check=True
    ).stdout
    assert refs.strip() != ""  # initial commit landed on the remote


def test_backup_commits_and_pushes_new_changes(tmp_path: Path):
    home = tmp_path / "home"
    remote = _bare_remote(tmp_path)
    init_backup(home, remote)
    (home / "raw" / "ns").mkdir(parents=True)
    (home / "raw" / "ns" / "b.md").write_text("# b")
    made = backup(home)
    assert made is True
    assert "backup " in _log(home)


def test_backup_skips_when_nothing_staged(tmp_path: Path):
    home = tmp_path / "home"
    remote = _bare_remote(tmp_path)
    init_backup(home, remote)
    made = backup(home)
    assert made is False


def test_backup_raises_when_not_a_repo(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    with pytest.raises(BackupError):
        backup(home)


def test_backup_never_tracks_secret_or_regenerable(tmp_path: Path):
    home = tmp_path / "home"
    remote = _bare_remote(tmp_path)
    init_backup(home, remote)
    (home / "config.yaml").write_text("api_key: sk-leaked\n")
    (home / "graph").mkdir()
    (home / "graph" / "facts.jsonl").write_text("{}")
    (home / "graph" / "manifest.json").write_text("{}")
    (home / "cache.json").write_text("{}")
    backup(home)
    tracked = _tracked(home)
    assert "config.yaml" not in tracked
    assert "graph/facts.jsonl" not in tracked
    assert "graph/manifest.json" not in tracked
    assert "cache.json" not in tracked


def test_backup_never_tracks_pending_journals(tmp_path: Path):
    home = tmp_path / "home"
    remote = _bare_remote(tmp_path)
    init_backup(home, remote)
    (home / "pending" / "public").mkdir(parents=True)
    (home / "pending" / "public" / "journal.jsonl").write_text(
        '{"id":"x","kind":"node","ns":"public","label":"leaked","type":"Concept"}\n'
    )
    backup(home)
    tracked = _tracked(home)
    assert "pending/public/journal.jsonl" not in tracked


def test_backup_retries_previously_rejected_push(tmp_path: Path):
    """Even with nothing new staged, backup() must still push — retrying a
    commit that landed locally but never reached the remote."""
    home = tmp_path / "home"
    remote = _bare_remote(tmp_path)
    init_backup(home, remote)
    # Make a local commit that the remote does not yet have, with nothing new
    # staged afterward (so _commit() returns committed=False).
    (home / "raw" / "ns").mkdir(parents=True)
    (home / "raw" / "ns" / "pre.md").write_text("# pre")
    subprocess.run(["git", "add", "-A"], cwd=home, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "pre-made local"],
        cwd=home,
        check=True,
    )
    made = backup(home)
    assert made is False  # nothing newly staged by backup()
    # The pre-made commit must have reached the remote.
    remote_refs = subprocess.run(
        ["git", "ls-remote", remote],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert remote_refs.strip() != ""
    # And the remote's HEAD must point at our local HEAD.
    remote_head = remote_refs.splitlines()[0].split()[0]
    local_head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=home,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert remote_head == local_head
