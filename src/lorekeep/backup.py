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


def _remote_sha(home: Path) -> str | None:
    """SHA of the remote branch tracking the current local branch, or None."""
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], home)
    if not branch or branch == "HEAD":
        return None
    out = _git(["ls-remote", "origin", branch], home)
    return out.split()[0] if out.strip() else None


def _reconcile_remote(home: Path) -> None:
    """Fetch + rebase before a push, to avoid non-fast-forward rejection.

    Used by ``init_backup`` on re-init (when ``.git`` already exists) so the
    remote's commits are not lost.  Silently skips if the remote has no
    matching branch yet (first init) or a rebase is unnecessary.
    """
    try:
        _git(["fetch", "origin"], home)
        branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], home)
        if branch and branch != "HEAD":
            _git(["rebase", f"origin/{branch}"], home)
    except BackupError:
        pass  # no remote ref yet, or clean tree — push will surface real errors


def init_backup(home: Path, remote: str) -> None:
    """Init a git repo in ``home``, write .gitignore, set origin, commit, push.

    Idempotent: safe to re-run.  On re-init (``.git`` already present) it
    fetches and rebases on the remote before pushing, so commits pushed by
    another device are preserved.
    """
    home.mkdir(parents=True, exist_ok=True)
    already_repo = (home / ".git").is_dir()
    if not already_repo:
        _git(["init", "-q"], home)
    (home / ".gitignore").write_text(BACKUP_GITIGNORE)
    remotes = _git(["remote"], home).split()
    if "origin" in remotes:
        _git(["remote", "set-url", "origin", remote], home)
    else:
        _git(["remote", "add", "origin", remote], home)
    _commit(home, "backup init")
    if already_repo:
        _reconcile_remote(home)
    _git(["push", "-u", "origin", "HEAD"], home)


def backup(home: Path) -> bool:
    """Commit + push pending changes.

    Returns ``True`` if the **remote was advanced** (a new commit was pushed
    or a previously-rejected push finally succeeded).  Returns ``False`` when
    the remote was already up-to-date.

    Push is always attempted, even without a new commit, so a previously-
    rejected push (remote diverged, network glitch) is retried automatically.
    """
    if not (home / ".git").is_dir():
        raise BackupError(
            f"not a backup repo at {home} — run `lorekeep backup --init <remote>` first"
        )
    before = _remote_sha(home)
    _commit(home, "backup")
    _git(["push"], home)
    after = _remote_sha(home)
    return after != before
