"""Colored, terminal-aware output for the lorekeep CLI.

Single chokepoint for all ``rich`` interaction. The module-level
:class:`~rich.console.Console` auto-strips color in a non-tty (tests via
CliRunner, the daemon's ``agent.log`` redirect), so plain-text contracts hold
and ANSI never leaks into captured output or log files.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import TYPE_CHECKING

from rich.console import Console
from rich.markup import escape

if TYPE_CHECKING:
    from collections.abc import Iterator

console = Console()
stderr_console = Console(stderr=True)

_quiet = False
_logging_configured = False


def is_quiet() -> bool:
    """True when ``--quiet``/WARNING level suppresses progress output."""
    return _quiet


def is_terminal() -> bool:
    """Whether stdout is a tty (color + progress bars). False under CliRunner/daemon."""
    return console.is_terminal


# ── status-line helpers ──────────────────────────────────────────────────────
# The message is escaped so a caller's ``[``/`]`` (e.g. namespaces lists) is
# never misread as Rich markup. After color-strip the plain text is verbatim,
# so substring assertions ("all checks passed", "backup failed", …) survive.

def ok(msg: str) -> None:
    console.print(f"[green]✓[/green] {escape(msg)}")


def info(msg: str) -> None:
    console.print(f"[cyan]→[/cyan] {escape(msg)}")


def step(msg: str) -> None:
    console.print(f"[dim]…[/dim] {escape(msg)}")


def dim(msg: str) -> None:
    console.print(f"[dim]{escape(msg)}[/dim]")


def warn(msg: str) -> None:
    # stdout (not stderr): lorekeep's status tests assert on result.stdout, and
    # the pre-existing doctor/check/backup FAIL lines were plain stdout echos.
    console.print(f"[yellow]![/yellow] {escape(msg)}")


def error(msg: str) -> None:
    console.print(f"[red]✗[/red] {escape(msg)}")


# ── progress / spinner ───────────────────────────────────────────────────────

class _NullProgress:
    """No-op handle used when there's no tty (tests, daemon log). Falsy."""

    def __bool__(self) -> bool:
        return False

    def advance(self, n: int = 1) -> None:
        pass

    def update(self, completed: int | None = None, total: int | None = None) -> None:
        pass


class _ProgressHandle:
    def __init__(self, bar, task) -> None:
        self._bar = bar
        self._task = task

    def __bool__(self) -> bool:
        return True

    def advance(self, n: int = 1) -> None:
        self._bar.advance(self._task, n)

    def update(self, completed: int | None = None, total: int | None = None) -> None:
        kwargs: dict[str, int] = {}
        if completed is not None:
            kwargs["completed"] = completed
        if total is not None:
            kwargs["total"] = total
        if kwargs:
            self._bar.update(self._task, **kwargs)


@contextmanager
def progress(description: str, total: int | None = None) -> Iterator[_NullProgress | _ProgressHandle]:
    """A Rich Progress bar in a tty; a silent no-op elsewhere.

    Hard-gated on ``console.is_terminal`` so progress text can never appear in
    CliRunner capture or ``agent.log`` (belt-and-suspenders on top of Rich's
    own auto-disable in a non-tty).
    """
    if not console.is_terminal:
        yield _NullProgress()
        return
    from rich.progress import BarColumn, Progress, TextColumn
    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        console=console,
        transient=True,
    ) as bar:
        task = bar.add_task(description, total=total)
        yield _ProgressHandle(bar, task)


@contextmanager
def status(label: str) -> Iterator[None]:
    """A Rich Status spinner in a tty; a single plain line elsewhere."""
    if not console.is_terminal:
        console.print(escape(label))
        yield
        return
    from rich.status import Status
    with Status(escape(label), console=console, spinner="dots"):
        yield


# ── logging ──────────────────────────────────────────────────────────────────

def configure_logging(level: int = logging.INFO) -> None:
    """Attach a colored ``RichHandler`` to the root logger; set the lorekeep level.

    The handler goes on *root* (not just ``lorekeep``) so litellm WARNING records
    keep their current stderr visibility — reformatted only, no level change, so
    no new INFO/DEBUG noise. ``propagate`` stays True (pytest ``caplog`` depends
    on it). Idempotent: the handler is attached once per process.
    """
    global _quiet, _logging_configured
    _quiet = level >= logging.WARNING
    logging.getLogger("lorekeep").setLevel(level)
    if _logging_configured:
        return
    from rich.logging import RichHandler
    logging.getLogger().addHandler(RichHandler(
        console=stderr_console,
        show_time=False,
        show_path=False,
        rich_tracebacks=True,
        markup=False,
    ))
    _logging_configured = True
