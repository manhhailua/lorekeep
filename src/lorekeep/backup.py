"""Manual backup of the lorekeep data home to a private git repo.

The backup repo lives inside the data home (`.lorekeep/` in dev mode). It
tracks the curated source (`raw/`) and the schema; it ignores the secret
`config.yaml` and the regenerable compile outputs.
"""
from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

BACKUP_GITIGNORE = """\
config.yaml
graph/facts.jsonl
graph/manifest.json
cache.json
pending/
"""


class BackupError(RuntimeError):
    """Raised when a git operation during backup fails."""


def _git(args: list[str], cwd: Path) -> str:
    """Run git in `cwd`, returning stdout. Raise BackupError on non-zero exit.

    Inline user.email/user.name so commits work without a global git identity
    (important in CI and fresh machines).
    """
    proc = subprocess.run(
        [
            "git",
            "-c", "user.email=lorekeep@backup.local",
            "-c", "user.name=lorekeep backup",
            *args,
        ],
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise BackupError(
            f"git {' '.join(args)} failed (exit {proc.returncode}): {proc.stderr.strip()}"
        )
    return proc.stdout.strip()


def _commit(home: Path, prefix: str) -> bool:
    """Stage all and commit with an ISO-8601 UTC message. Return True if committed."""
    _git(["add", "-A"], home)
    staged = _git(["diff", "--cached", "--name-only"], home)
    if not staged:
        return False
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    _git(["commit", "-q", "-m", f"{prefix} {ts}"], home)
    return True


def init_backup(home: Path, remote: str) -> None:
    """Init a git repo in `home`, write .gitignore, set origin, commit, push.

    Idempotent: safe to re-run; rewrites the .gitignore and points origin at `remote`.
    """
    home.mkdir(parents=True, exist_ok=True)
    if not (home / ".git").is_dir():
        _git(["init", "-q"], home)
    (home / ".gitignore").write_text(BACKUP_GITIGNORE)
    remotes = _git(["remote"], home).split()
    if "origin" in remotes:
        _git(["remote", "set-url", "origin", remote], home)
    else:
        _git(["remote", "add", "origin", remote], home)
    _commit(home, "backup init")
    _git(["push", "-u", "origin", "HEAD"], home)


def backup(home: Path) -> bool:
    """Commit + push pending changes. Raise BackupError if not a backup repo.

    Push is always attempted, independent of whether a new commit was made, so a
    previously-rejected push (remote diverged) is retried. When nothing remains
    to push, git exits 0 ("Everything up-to-date"). The `committed` return bool
    reflects only whether a new commit was created.
    """
    if not (home / ".git").is_dir():
        raise BackupError(
            f"not a backup repo at {home} — run `lorekeep backup --init <remote>` first"
        )
    committed = _commit(home, "backup")
    _git(["push"], home)
    return committed
