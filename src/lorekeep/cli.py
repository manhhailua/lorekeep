"""Lorekeep CLI."""
from __future__ import annotations

import json
import logging
import os
import warnings
from pathlib import Path

import typer

# TODO(upstream): remove once the mcp package resolves the forward reference
# in FastMCP.Settings.lifespan. Track: https://github.com/jlowin/fastmcp/issues
# When the warning disappears on a fresh `lorekeep doctor` after an mcp
# upgrade, delete this block.
try:
    from pydantic_settings.sources.utils import IncompleteFieldDefinitionWarning
    warnings.filterwarnings("ignore", category=IncompleteFieldDefinitionWarning)
except ImportError:
    pass

from lorekeep import __version__
from lorekeep.compile.providers import LiteLLMProvider
from lorekeep.config import (
    CompileConfig,
    Config,
    NamespacesConfig,
    load_config,
    migrate_config_file,
)
from lorekeep.models import now_iso
from lorekeep.pipeline import compile_graph
from lorekeep.paths import resolve_paths
from lorekeep.defaults import DEFAULT_CONFIG_YAML, DEFAULT_SCHEMA
from lorekeep.providers import NATIVE_PROVIDERS, model_provider, validate_model_prefix
from lorekeep.schema_io import load_schema

log = logging.getLogger("lorekeep")

app = typer.Typer(help="Lorekeep — compile team docs into a temporal knowledge graph.")


# Empty callback forces multi-command mode so subcommands are not auto-promoted.
@app.callback()
def _main(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug-level logs."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Warnings only; suppress progress."),
) -> None:
    """Lorekeep — compile team docs into a temporal knowledge graph."""
    import logging as _logging
    from lorekeep.output import configure_logging
    if os.environ.get("LOREKEEP_DEBUG"):
        verbose = True
    level = _logging.DEBUG if verbose else (_logging.WARNING if quiet else _logging.INFO)
    configure_logging(level)


# Agent subcommand group (created early so commands below can register on it).
agent_app = typer.Typer(
    help="Agent operations: ingest, lint, suggest, status, watch, profile, "
         "contribution, service.",
)
app.add_typer(agent_app, name="agent")

quarantine_app = typer.Typer(
    help="Park orphaned (zero-edge) nodes for human review instead of "
         "losing or re-litigating them on every compile (#266).",
)
app.add_typer(quarantine_app, name="quarantine")


def _build_provider(config: Config) -> LiteLLMProvider:
    """Create a real LLM provider from config.  Shared by compile + import."""
    from lorekeep.compile.providers import setup_observability

    obs = config.observability
    if obs.provider:
        setup_observability(
            provider=obs.provider,
            api_key_env=obs.api_key_env,
            project=obs.project,
            api_url=obs.api_url,
        )

    api_key = None
    if config.provider.api_key_env:
        api_key = os.environ.get(config.provider.api_key_env)
    if not api_key:
        api_key = config.provider.api_key
    validate_model_prefix(config.provider.model)  # defense-in-depth (load_config already gates)
    return LiteLLMProvider(
        model=config.provider.model,
        api_base=config.provider.api_base,
        temperature=config.provider.temperature,
        api_key=api_key,
        timeout_seconds=config.provider.timeout_seconds,
        max_retries=config.provider.max_retries,
    )


def _make_provider(config: Config) -> LiteLLMProvider:
    """Create provider for compile.  Tests monkeypatch this to inject FakeProvider."""
    return _build_provider(config)


def _make_import_provider(config: Config) -> LiteLLMProvider:
    """Create provider for import deep mode.  Tests monkeypatch this."""
    return _build_provider(config)


def _has_provider(config: Config) -> bool:
    """Check if a provider API key is available.  Tests monkeypatch this."""
    return bool(
        (config.provider.api_key_env and os.environ.get(config.provider.api_key_env))
        or config.provider.api_key
    )


@app.command()
def version() -> None:
    """Print the Lorekeep version."""
    typer.echo(f"lorekeep {__version__}")


def _latest_pypi_version() -> str | None:
    """Fetch the latest lorekeep version from PyPI.

    Returns ``None`` on any network/parsing error — callers handle the
    graceful degradation (tell the user to upgrade manually).
    """
    import json as _json
    import urllib.request
    try:
        with urllib.request.urlopen(
            "https://pypi.org/pypi/lorekeep/json", timeout=5,
        ) as resp:
            data = _json.loads(resp.read())
            return data.get("info", {}).get("version")
    except Exception:
        return None


def _detect_install_method() -> str:
    """Detect how lorekeep was installed.

    Returns one of: ``"uv"``, ``"pipx"``, ``"pip"``, ``"unknown"``.
    """
    import shutil as _shutil
    import sys as _sys
    exe = _sys.executable
    if ".local/share/uv/tools/lorekeep" in exe or "/uv/tools/lorekeep" in exe:
        return "uv"
    if _shutil.which("pipx"):
        return "pipx"
    if _shutil.which("pip") or _shutil.which("pip3"):
        return "pip"
    try:
        import pip  # noqa: F401
        return "pip"
    except ImportError:
        pass
    return "unknown"


@app.command()
def update(
    check: bool = typer.Option(
        False, "--check", "-c",
        help="Only show current vs latest version; do not upgrade.",
    ),
    force: bool = typer.Option(
        False, "--force", "-f",
        help="Upgrade even if already at the latest version (reinstall).",
    ),
) -> None:
    """Upgrade lorekeep to the latest version from PyPI.

    Detects the install method (uv tool, pipx, or pip) and runs the
    appropriate upgrade command. Use ``--check`` to preview without upgrading.
    """
    import subprocess
    import sys

    from lorekeep.output import ok, warn

    current = __version__
    typer.echo(f"current: {current}")

    latest = _latest_pypi_version()
    if latest is None:
        warn("could not reach PyPI — check your network and try manually:")
        typer.echo("  uv tool upgrade lorekeep")
        typer.echo("  # or: pip install --upgrade lorekeep")
        raise typer.Exit(code=1)

    typer.echo(f"latest:  {latest}")

    if check:
        if latest == current:
            ok("already up to date")
        else:
            typer.echo(f"update available: {current} → {latest}")
            typer.echo("run `lorekeep update` to upgrade")
        return

    if latest == current and not force:
        ok("already up to date")
        return

    method = _detect_install_method()
    typer.echo(f"install method: {method}")

    if method == "uv":
        cmd = ["uv", "tool", "upgrade", "lorekeep"]
        if force:
            cmd = ["uv", "tool", "install", "--force", "lorekeep"]
    elif method == "pipx":
        cmd = ["pipx", "upgrade", "lorekeep"]
        if force:
            cmd = ["pipx", "install", "--force", "lorekeep"]
    elif method == "pip":
        cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "--user", "lorekeep"]
    else:
        warn("could not detect install method. Upgrade manually:")
        typer.echo("  uv tool upgrade lorekeep")
        typer.echo("  # or: pip install --upgrade lorekeep")
        typer.echo("  # or: pipx upgrade lorekeep")
        raise typer.Exit(code=1)

    typer.echo(f"running: {' '.join(cmd)}")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        warn("upgrade command failed")
        raise typer.Exit(code=result.returncode)

    # Re-read on-disk version after upgrade
    new_version = _on_disk_version()
    if new_version:
        ok(f"upgraded to {new_version}")
    else:
        ok("upgrade complete")

    # Restart daemon if running
    p = resolve_paths()
    pid = _daemon_pid(p)
    if pid:
        import signal as _signal
        try:
            os.kill(pid, _signal.SIGTERM)
            typer.echo(f"daemon: restarting (was pid={pid})")
        except OSError:
            pass


@app.command(hidden=True)
def hook(
    agent: str = typer.Option(None, "--agent", help="Native hook source"),
    trigger: str = typer.Option(
        "session_end", "--trigger",
        help="session_end | idle_fallback | turn_end_fallback",
    ),
    home: Path = typer.Option(None, "--home", help="Resolved Lorekeep data home"),
    session_id: str = typer.Option(None, "--session-id"),
    cwd: str = typer.Option(None, "--cwd"),
) -> None:
    """Fast lifecycle ingress: enqueue metadata; daemon performs all I/O."""
    import sys

    if not agent:
        # Pre-0.38 entries called without an agent. Keep them fail-open and
        # fast until automatic wiring replaces them; never scan every agent.
        log.warning("legacy hook invocation ignored", extra={"event": "hook.legacy"})
        return

    from lorekeep.hook_events import MAX_HOOK_PAYLOAD_BYTES, enqueue_hook_event

    target_home = Path(home) if home else resolve_paths()["home"]
    raw = sys.stdin.read(MAX_HOOK_PAYLOAD_BYTES + 1)
    try:
        enqueue_hook_event(
            target_home,
            agent=agent,
            trigger=trigger,
            raw_payload=raw,
            session_id=session_id,
            cwd=cwd,
        )
    except (KeyError, OSError, ValueError) as exc:
        log.warning(
            "hook enqueue failed agent=%s error_type=%s",
            agent, type(exc).__name__, extra={"event": "hook.enqueue_failed"},
        )
        raise typer.Exit(code=1) from exc


def _load_prev_aliases(facts_path: Path) -> dict[str, str]:
    """Extract alias→canonical map from existing ``facts.jsonl``.

    Reads ``props.merged_ids`` on every node and builds ``{alias_id: canonical_id}``.
    This carries forward merge decisions across recompiles so that manual and
    LLM-detected entity merges are not lost when ``compile_graph`` rebuilds from
    ``raw/*.md``.
    """
    if not facts_path.exists():
        return {}
    from lorekeep.facts_io import read_facts
    from lorekeep.models import Node as _Node
    prev: dict[str, str] = {}
    try:
        for fact in read_facts(facts_path):
            if not isinstance(fact, _Node):
                continue
            for mid in fact.props.get("merged_ids", []):
                if isinstance(mid, str) and mid != fact.id:
                    prev[mid] = fact.id
    except Exception:
        log.debug("failed to load prev_aliases from %s", facts_path, exc_info=True)
    return prev


def _load_prev_quarantine(facts_path: Path) -> dict[str, dict[str, str]]:
    """Extract orphan-quarantine flags from existing ``facts.jsonl``.

    Reads ``props.quarantined_at``/``props.quarantined_reason`` on every node so
    a decision made via ``lorekeep quarantine`` survives the next ``compile``,
    which rebuilds nodes fresh from ``raw/*.md`` and would otherwise silently
    drop the flag — the same problem ``_load_prev_aliases`` solves for
    ``props.merged_ids`` (see issue #266).
    """
    if not facts_path.exists():
        return {}
    from lorekeep.facts_io import read_facts
    from lorekeep.models import Node as _Node
    from lorekeep.store.graph import is_quarantined
    prev: dict[str, dict[str, str]] = {}
    try:
        for fact in read_facts(facts_path):
            if not isinstance(fact, _Node) or not is_quarantined(fact):
                continue
            prev[fact.id] = {
                "quarantined_at": fact.props["quarantined_at"],
                "quarantined_reason": str(fact.props.get("quarantined_reason", "")),
            }
    except Exception:
        log.debug("failed to load prev_quarantine from %s", facts_path, exc_info=True)
    return prev


def _report_compile_errors(manifest, *, exit_on_total_failure: bool = True) -> None:
    """Surface compile errors from a :class:`~lorekeep.models.Manifest`.

    ``compile_graph`` uses a skip-and-log strategy: per-chunk failures are
    collected in ``manifest.errors`` rather than raised.  Without this helper
    the user would see ``compiled: 0 nodes`` and an exit code of 0, with no
    indication that every LLM extraction call failed (e.g. wrong model string,
    missing API key, bad ``api_base``).

    * **Partial failure** — some chunks failed but nodes were produced.
      Prints a one-line summary to stderr so the user knows to check
      ``manifest.json`` for details.
    * **Total failure** — ``node_count == 0`` with ``chunk_count > 0``.
      Prints every error to stderr and, when *exit_on_total_failure* is
      ``True`` (the interactive ``compile`` command), exits with code 1.
      The daemon passes ``False`` so it can keep running.
    """
    errs = manifest.errors or []
    if not errs:
        return
    for e in errs:
        log.error(
            "compile error line=%s", e.line,
            extra={"event": "compile.manifest_error"},
        )
    from lorekeep.output import dim, error, warn
    total_fail = manifest.node_count == 0 and manifest.chunk_count > 0
    if total_fail:
        error(
            f"compile: ALL {manifest.chunk_count} chunk(s) failed — 0 nodes produced. "
            "Check provider config (model, api_base, api_key)."
        )
        for e in errs:
            dim(f"  {e.path}:{e.line}: {e.message}")
        if exit_on_total_failure:
            raise typer.Exit(code=1)
    else:
        # Systemic-error heuristic: if most chunks failed with the SAME message,
        # it's almost always a provider config issue (bad model/api_base/api_key)
        # rather than per-doc content. Surface every identical error + a hint so
        # the user isn't left with a one-line summary and an empty-looking graph.
        messages = [e.message for e in errs]
        distinct = set(messages)
        systemic = len(errs) >= 3 and (len(distinct) == 1 or max(messages.count(m) for m in distinct) >= 0.8 * len(errs))
        if systemic:
            error(
                f"compile: {len(errs)} of {manifest.chunk_count} chunk(s) failed "
                f"with the same error ({manifest.node_count} nodes still produced)."
            )
            for e in errs:
                dim(f"  {e.path}:{e.line}: {e.message}")
            dim(
                "  hint: identical errors across chunks usually mean a provider "
                "config issue (model/api_base/api_key). Run 'lorekeep doctor'."
            )
        else:
            warn(
                f"compile: {len(errs)} chunk(s) failed (partial — "
                f"{manifest.node_count} nodes still produced). See manifest.json."
            )


def _report_content_quality(manifest) -> None:
    """Warn about readability gaps without rejecting otherwise valid facts."""
    quality = manifest.content_quality
    if quality is None:
        return
    issues: list[str] = []
    if quality.node_summary_coverage < 1.0:
        issues.append(f"summaries {quality.node_summary_coverage:.0%}")
    if quality.edge_description_coverage < 1.0:
        issues.append(
            f"relationship explanations {quality.edge_description_coverage:.0%}"
        )
    if quality.generic_edge_ratio > 0.5:
        issues.append(f"generic edges {quality.generic_edge_ratio:.0%}")
    if quality.duplicate_label_count:
        issues.append(f"duplicate labels {quality.duplicate_label_count}")
    if issues:
        from lorekeep.output import warn
        warn(
            "compile: content quality needs attention ("
            + ", ".join(issues)
            + "). Facts were kept; see manifest.json and wiki/overview.md."
        )


def _progress_ctx(raw_root, chunk_lines):
    """Context manager for a compile progress bar.

    tty + not quiet → a Rich Progress bar (total pre-counted via ingest, a pure
    file-slicer). Else → a nullcontext whose handle is None, so compile_graph
    runs silent (current behavior under CliRunner / the daemon's agent.log).
    """
    from contextlib import nullcontext
    from lorekeep.compile.ingest import ingest as _ingest
    from lorekeep.output import is_quiet, is_terminal, progress
    if not is_quiet() and is_terminal():
        total = len(_ingest(raw_root, chunk_lines=chunk_lines))
        return progress(f"Compiling {total} chunk(s)", total=total)
    return nullcontext(None)


def _progress_cb(handle):
    """Build an on_progress callback from a progress handle (None → None)."""
    if not handle:
        return None
    return lambda i, total, chunk: handle.advance()


@app.command()
def compile(
    foreground: bool = typer.Option(
        False, "--foreground", "-f",
        help="Run synchronously (blocking). Default: background in interactive mode, foreground in non-interactive.",
    ),
) -> None:
    """Compile raw/ → facts.jsonl + merge pending + generate wiki.

    By default delegates to the daemon (background) in interactive mode.
    Falls back to synchronous in non-interactive mode (CI, scripts, tests).
    """
    p = resolve_paths()

    # Background = default in interactive mode. Foreground in non-interactive
    # (tests, CI, pipes) unless --foreground is explicitly requested.
    use_background = not foreground and _is_interactive()
    if use_background:
        sentinel = p["home"] / ".compile-requested"
        sentinel.touch()
        _start_daemon_if_needed(p)
        from lorekeep.output import dim
        typer.echo("  ✓ Compile delegated to daemon.")
        log_path = p.get("logs", p["home"] / "logs") / "daemon-bootstrap.log"
        wiki_path = p.get("wiki", p["home"] / "wiki")
        typer.echo(f"\n  Watch progress:  tail -f {log_path}")
        typer.echo(f"  Open wiki:       {wiki_path}\n")
        dim("(use --foreground to run synchronously)")
        return

    from lorekeep.output import ok
    schema = load_schema(p["schema"])
    config = load_config(p["config"])
    provider = _make_provider(config)

    with _progress_ctx(p["raw"], config.compile.chunk_lines) as handle:
        manifest = compile_graph(
            raw_root=p["raw"], out_dir=p["out"], schema=schema,
            provider=provider, cache_path=p["cache"], chunk_lines=config.compile.chunk_lines,
            on_progress=_progress_cb(handle),
            personal_ns=config.namespaces.write,
            language=config.compile.language,
            prev_aliases=_load_prev_aliases(p["out"] / "facts.jsonl"),
            prev_quarantine=_load_prev_quarantine(p["out"] / "facts.jsonl"),
            max_workers=config.compile.max_workers,
            flush_interval=config.compile.flush_interval,
        )

    ok(f"compiled: {manifest.node_count} nodes, {manifest.edge_count} edges, "
       f"run_id={manifest.run_id}, facts_hash={manifest.facts_hash}")

    _report_compile_errors(manifest)
    _report_content_quality(manifest)

    pending_dir = p.get("pending")
    resolved = False
    if pending_dir and pending_dir.exists():
        resolved = _do_auto_resolve(
            p["out"], pending_dir, p.get("wiki"), p.get("schema"),
            replay_accepted=True,
        )

    if not resolved:
        _auto_generate_wiki(p["out"], p["wiki"], p.get("schema"))


def _open_in_obsidian(path: Path) -> None:
    """Open *path* as an Obsidian vault via the ``obsidian://`` URL scheme.

    Non-fatal: if Obsidian (or the platform opener) is missing, warn with the
    raw path so the user can open it manually. The wiki is already generated.
    """
    import subprocess
    import sys
    import urllib.parse
    from lorekeep.output import warn
    url = "obsidian://open?path=" + urllib.parse.quote(str(path.resolve()), safe="")
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", url], check=False)
        elif sys.platform.startswith("win"):
            subprocess.run(["cmd", "/c", "start", "", url], check=False)
        else:
            subprocess.run(["xdg-open", url], check=False)
    except (FileNotFoundError, OSError):
        warn(f"could not launch Obsidian; open this folder as a vault manually: {path}")


@app.command()
def wiki(
    open: bool = typer.Option(False, "--open", help="Open the wiki in Obsidian after generating."),
) -> None:
    """Generate Obsidian-compatible wiki from facts.jsonl."""
    from lorekeep.output import error, ok
    from lorekeep.wiki import generate_wiki
    p = resolve_paths()
    schema = load_schema(p["schema"]) if p["schema"].exists() else None
    result = generate_wiki(p["out"], p["wiki"], schema=schema)
    if "error" in result:
        error(f"wiki: {result['error']}")
        raise typer.Exit(code=1)
    ok(f"wiki: {result['pages']} pages written to {p['wiki']}")
    if open:
        _open_in_obsidian(p["wiki"])


@agent_app.command()
def profile(
    open: bool = typer.Option(False, "--open", help="Open your raw profile dir in Obsidian/Tolaria."),
) -> None:
    """Show / open your profile source in the write namespace.

    The wiki is a derived view; the editable source is
    raw/<namespaces.write>/about.md + profile.md. Edit those (in
    Obsidian/Tolaria), then `lorekeep compile`.
    """
    from lorekeep.output import info
    p = resolve_paths()
    try:
        ns = load_config(p["config"]).namespaces.write
    except Exception:
        ns = "me"
    ns_dir = p["raw"] / ns
    info(f"profile source: {ns_dir}")
    info("edit about.md / profile.md here, then `lorekeep compile` — the wiki reflects you")
    if open:
        _open_in_obsidian(ns_dir)


@agent_app.command()
def contribution() -> None:
    """Suggest knowledge in the write namespace not shared elsewhere.

    Scans the compiled graph for nodes of shareable types (service, project,
    decision, domain, skill) that live only in the configured write namespace.
    Move the source doc to a team
    namespace (raw/<team>/) and re-compile to share. Read-only.
    """
    from collections import defaultdict
    from lorekeep.compile.resolve import _normalize_id
    from lorekeep.output import dim, info, ok, warn
    from lorekeep.store.graph import GraphStore
    p = resolve_paths()
    facts = p["out"] / "facts.jsonl"
    if not facts.exists():
        warn(f"no compiled graph at {facts} — run `lorekeep compile` first")
        raise typer.Exit(code=1)
    try:
        personal_ns = load_config(p["config"]).namespaces.write
    except Exception:
        personal_ns = "me"
    SHARE_TYPES = {"service", "project", "decision", "domain", "skill"}

    store = GraphStore.from_jsonl(facts)
    where: dict[str, set[str]] = defaultdict(set)
    for n in store.all_nodes():
        where[_normalize_id(n.id)].update(n.ns)

    gaps = [
        n for n in store.all_nodes()
        if personal_ns in n.ns
        and n.type in SHARE_TYPES
        and not (where[_normalize_id(n.id)] - {personal_ns, "public"})
    ]
    gaps.sort(key=lambda n: (n.type, n.id))

    if not gaps:
        ok(f"no contribution gaps — your '{personal_ns}' knowledge is already shared")
        return
    info(f"{len(gaps)} node(s) in '{personal_ns}' not in any team namespace:")
    for n in gaps:
        dim(f"  {n.id} ({n.type}) — consider moving its source doc to raw/<team>/")





@app.command(name="eval", hidden=True)
def eval_cmd() -> None:
    """Run Tier-1 construction-quality evaluation vs the gold corpus."""
    p = resolve_paths()
    gold_dir = Path(os.environ.get("LOREKEEP_GOLD", "tests/fixtures/gold"))
    from lorekeep.eval.construction import extraction_report, structure_report
    report = {
        "extraction": extraction_report(p["out"], gold_dir),
        "structure": structure_report(p["out"]),
    }
    results_path = Path(os.environ.get("LOREKEEP_EVAL_RESULTS",
                                       ".lorekeep/eval/results.json"))
    results_path.parent.mkdir(parents=True, exist_ok=True)
    results_path.write_text(json.dumps(report, indent=2, sort_keys=True))
    typer.echo(json.dumps(report, indent=2, sort_keys=True))


@app.command(name="eval-locomo", hidden=True)
def eval_locomo_cmd(
    data: str = typer.Option("", "--data", help="Path to locomo10.json"),
    compile_first: bool = typer.Option(
        False, "--compile",
        help="Convert JSON to raw/ + compile before running eval",
    ),
) -> None:
    """Run Tier-2 LoCoMo retrieval eval."""
    from lorekeep.eval.locomo import convert_locomo, locomo_report

    p = resolve_paths()
    data_path = Path(data) if data else Path(
        os.environ.get("LOREKEEP_LOCOMO", "locomo10.json")
    )

    if compile_first:
        if not data_path.exists():
            typer.echo(f"eval-locomo: data file not found: {data_path}")
            raise typer.Exit(code=1)
        count = convert_locomo(data_path, p["raw"] / "locomo")
        typer.echo(f"eval-locomo: converted {count} session files to {p['raw'] / 'locomo'}")
        schema = load_schema(p["schema"])
        config = load_config(p["config"])
        provider = _make_provider(config)
        manifest = compile_graph(
            raw_root=p["raw"], out_dir=p["out"], schema=schema,
            provider=provider, cache_path=p["cache"],
            chunk_lines=config.compile.chunk_lines,
            personal_ns=config.namespaces.write,
            language=config.compile.language,
            prev_aliases=_load_prev_aliases(p["out"] / "facts.jsonl"),
            prev_quarantine=_load_prev_quarantine(p["out"] / "facts.jsonl"),
            max_workers=config.compile.max_workers,
            flush_interval=config.compile.flush_interval,
        )
        typer.echo(f"eval-locomo: compiled {manifest.node_count} nodes, {manifest.edge_count} edges")

    raw_ns = _read_namespace_override()
    allowed = [x.strip() for x in raw_ns.split(",")] if raw_ns else ["locomo"]
    report = locomo_report(p["out"], data_path, allowed, raw_dir=p["raw"])
    if "error" in report:
        typer.echo(f"eval-locomo: {report['error']}")
        raise typer.Exit(code=1)

    results_path = Path(os.environ.get(
        "LOREKEEP_EVAL_RESULTS", ".lorekeep/eval/locomo-results.json"
    ))
    results_path.parent.mkdir(parents=True, exist_ok=True)
    results_path.write_text(json.dumps(report, indent=2, sort_keys=True))

    s = report["summary"]
    typer.echo(f"\nLoCoMo Tier-2 Eval ({s['total_questions']} questions)")
    typer.echo(f"Overall F1: {s['overall_f1']}")
    typer.echo("")
    typer.echo(f"{'Category':<20} {'Count':>6} {'F1':>8}")
    typer.echo("-" * 36)
    for cat, stats in s["per_category"].items():
        typer.echo(f"{cat:<20} {stats['count']:>6} {stats['f1']:>8.4f}")


def _with_resolve_lock(func):
    """Typer-safe decorator serializing manual resolve with daemon resolve."""
    from functools import wraps

    @wraps(func)
    def wrapped(*args, **kwargs):
        from lorekeep.journal import resolve_lock
        pending = resolve_paths().get("pending")
        if pending is None:
            return func(*args, **kwargs)
        with resolve_lock(pending):
            return func(*args, **kwargs)

    return wrapped


@app.command()
@_with_resolve_lock
def resolve(
    archive: bool = typer.Option(
        False, "--archive",
        help="Archive processed journal entries instead of truncating",
    ),
) -> None:
    """Merge pending journal entries into facts.jsonl (full resolve pass)."""
    p = resolve_paths()
    from lorekeep.store.graph import GraphStore
    from lorekeep.facts_io import read_facts
    from lorekeep.compile.resolve import resolve as resolve_facts, merge_journals
    from lorekeep.compile.writer import write_graph
    from lorekeep.journal import load_journals, update_journal_status
    from lorekeep.models import Manifest
    from lorekeep.pipeline import measure_content_quality

    facts_path = p["out"] / "facts.jsonl"
    pending = p.get("pending")
    if not pending or not pending.exists():
        typer.echo("resolve: no pending directory, nothing to do")
        return

    journals = load_journals(pending)
    pending_entries = [j for j in journals if j.status == "pending"]
    if not pending_entries:
        typer.echo("resolve: no pending journal entries")
        return

    # Load current facts
    existing_nodes = []
    existing_edges = []
    if facts_path.exists():
        facts = read_facts(facts_path)
        from lorekeep.models import Edge, Node
        for f in facts:
            if isinstance(f, Node):
                existing_nodes.append(f)
            else:
                existing_edges.append(f)

    schema = load_schema(p["schema"])
    # Merge journals
    merged = merge_journals(
        existing_nodes, existing_edges, pending_entries, schema=schema,
    )

    # Run standard resolve over merged facts
    resolved = resolve_facts(
        merged.nodes, merged.edges, schema=schema,
    )

    # Build manifest
    manifest = Manifest(
        schema_version=schema.version,
        chunk_count=0,
        node_count=len(resolved.nodes),
        edge_count=len(resolved.edges),
        run_id="resolve",
        facts_hash="",
        compiled_at=now_iso(),
        merged_count=merged.merge_count,
        quarantined_count=merged.quarantine_count,
        flagged_count=merged.flagged_count,
        quarantine=[{"fact": q[0].fact, "reason": q[1]} for q in resolved.quarantined],
        review=[{"fact_id": f[0].fact.get("id", ""), "reason": f[1]}
                for f in merged.flagged],
        content_quality=measure_content_quality(
            resolved.nodes, resolved.edges, schema,
        ),
    )
    write_graph(p["out"], resolved.nodes, resolved.edges, manifest)

    # Update journal status per namespace
    ns_to_merged: dict[str, set[str]] = {}
    ns_to_flagged: dict[str, set[str]] = {}
    ns_to_quarantined: dict[str, set[str]] = {}
    for entry, _ in merged.merged:
        ns_to_merged.setdefault(entry.ns, set()).add(
            entry.entry_id or entry.proposed_at
        )
    for entry, _ in merged.flagged:
        ns_to_flagged.setdefault(entry.ns, set()).add(
            entry.entry_id or entry.proposed_at
        )
    for entry, _ in merged.quarantined:
        ns_to_quarantined.setdefault(entry.ns, set()).add(
            entry.entry_id or entry.proposed_at
        )

    # Flagged entries are still merged into the graph (just flagged for review)
    for ns, timestamps in ns_to_flagged.items():
        existing = ns_to_merged.get(ns, set())
        ns_to_merged[ns] = existing | timestamps

    for ns, timestamps in ns_to_merged.items():
        update_journal_status(pending, ns, timestamps, "merged")
    for ns, timestamps in ns_to_quarantined.items():
        # Don't overwrite merged status for entries already handled
        already = ns_to_merged.get(ns, set())
        to_quarantine = timestamps - already
        if to_quarantine:
            update_journal_status(pending, ns, to_quarantine, "quarantined")

    typer.echo(
        f"resolve: {len(resolved.nodes)} nodes, {len(resolved.edges)} edges — "
        f"{merged.merge_count} merged, {merged.flagged_count} flagged, "
        f"{merged.quarantine_count} quarantined"
    )

    if merged.merge_count > 0 or merged.flagged_count > 0:
        _auto_generate_wiki(p["out"], p["wiki"], p.get("schema"))


def _write_quarantine_update(p: dict, nodes: list, edges: list) -> None:
    """Persist a props-only node mutation to facts.jsonl outside compile/resolve.

    Node/edge counts don't change (quarantine only tags props), so the previous
    manifest is preserved and just re-stamped — same convention as `agent lint
    --auto-fix` and `resolve` use for out-of-band graph writes.
    """
    from lorekeep.compile.writer import write_graph
    from lorekeep.models import Manifest

    manifest_path = p["out"] / "manifest.json"
    if manifest_path.exists():
        manifest = Manifest.from_json(manifest_path.read_text(encoding="utf-8"))
        manifest = manifest.model_copy(update={
            "run_id": "quarantine", "facts_hash": "", "compiled_at": now_iso(),
        })
    else:
        manifest = Manifest(
            schema_version=0, chunk_count=0, node_count=len(nodes), edge_count=len(edges),
            run_id="quarantine", facts_hash="", compiled_at=now_iso(),
        )
    write_graph(p["out"], nodes, edges, manifest)


@quarantine_app.command("detect")
def quarantine_detect(
    apply: bool = typer.Option(
        False, "--apply",
        help="Write the quarantine flag (default: dry-run report only).",
    ),
) -> None:
    """List orphaned (zero-edge) nodes; with --apply, park them for review.

    Quarantined nodes stay in facts.jsonl with full provenance — they are only
    excluded from wiki output and future lint/heal noise — until a human
    decides their fate with `lorekeep quarantine review`.
    """
    from lorekeep.agent import lint as agent_lint
    from lorekeep.output import info, ok
    from lorekeep.store.graph import GraphStore

    p = resolve_paths()
    facts_path = p["out"] / "facts.jsonl"
    if not facts_path.exists():
        typer.echo("quarantine detect: no graph — run `lorekeep compile` first")
        raise typer.Exit(code=1)

    store = GraphStore.from_jsonl(facts_path)
    candidate_ids = sorted(agent_lint(store).orphans)
    if not candidate_ids:
        ok("quarantine detect: no orphaned nodes found")
        return

    for nid in candidate_ids:
        node = store.get_node(nid)
        typer.echo(f"  {nid}  ({node.type if node else '?'})")

    if not apply:
        info(
            f"{len(candidate_ids)} orphan node(s) found — "
            "re-run with --apply to quarantine them"
        )
        return

    today = now_iso()[:10]
    ids = set(candidate_ids)
    new_nodes = [
        n.model_copy(update={"props": {
            **n.props,
            "quarantined_at": today,
            "quarantined_reason": "orphan (no edges)",
        }}) if n.id in ids else n
        for n in store.all_nodes()
    ]
    _write_quarantine_update(p, new_nodes, store.all_edges())
    _auto_generate_wiki(p["out"], p.get("wiki"), p.get("schema"))
    ok(f"quarantined {len(ids)} node(s) — review with `lorekeep quarantine review`")


@quarantine_app.command("review")
def quarantine_review() -> None:
    """Walk each quarantined node and decide: restore, keep, or skip for now."""
    from lorekeep.output import info, ok
    from lorekeep.store.graph import GraphStore, is_quarantined

    p = resolve_paths()
    facts_path = p["out"] / "facts.jsonl"
    if not facts_path.exists():
        typer.echo("quarantine review: no graph — run `lorekeep compile` first")
        raise typer.Exit(code=1)

    store = GraphStore.from_jsonl(facts_path)
    quarantined = sorted(
        (n for n in store.all_nodes() if is_quarantined(n)), key=lambda n: n.id,
    )
    if not quarantined:
        ok("quarantine review: nothing quarantined")
        return

    restored: list[str] = []
    kept: list[str] = []
    nodes_by_id = {n.id: n for n in store.all_nodes()}

    for node in quarantined:
        typer.echo(f"\n{node.id} ({node.type})")
        reason = node.props.get("quarantined_reason")
        if reason:
            typer.echo(f"  reason: {reason}")
        summary = node.props.get("summary") or node.props.get("description")
        if summary:
            typer.echo(f"  {summary}")
        if node.src:
            typer.echo(f"  source: {', '.join(node.src)}")
        choice = typer.prompt(
            "  [r]estore / [k]eep quarantined / [s]kip", default="s",
        ).strip().lower()
        if choice.startswith("r"):
            cleaned = {k: v for k, v in node.props.items()
                       if k not in ("quarantined_at", "quarantined_reason")}
            nodes_by_id[node.id] = node.model_copy(update={"props": cleaned})
            restored.append(node.id)
        elif choice.startswith("k"):
            kept.append(node.id)
        # skip: leave untouched, revisit on the next `quarantine review`

    if restored:
        ordered_nodes = [nodes_by_id[n.id] for n in store.all_nodes()]
        _write_quarantine_update(p, ordered_nodes, store.all_edges())
        _auto_generate_wiki(p["out"], p.get("wiki"), p.get("schema"))

    skipped = len(quarantined) - len(restored) - len(kept)
    info(f"restored: {len(restored)}, kept quarantined: {len(kept)}, skipped: {skipped}")


def _runtime_namespaces(config) -> tuple[list[str], str]:
    """Resolve read patterns and concrete write ownership independently."""
    raw_ns = _read_namespace_override()
    allowed = (
        [item.strip() for item in raw_ns.split(",") if item.strip()]
        if raw_ns else list(config.namespaces.read)
    )
    return allowed, config.namespaces.write


def _read_namespace_override() -> str | None:
    """Read the explicit scope override, accepting the legacy env at runtime."""
    return os.environ.get("LOREKEEP_READ_NS") or os.environ.get("LOREKEEP_NS")


@app.command()
def serve(
    transport: str = typer.Option("stdio", "--transport", help="stdio (default) | http"),
) -> None:
    """Serve the scoped graph over MCP."""
    p = resolve_paths()
    config = load_config(p["config"])
    allowed, write_ns = _runtime_namespaces(config)
    try:
        from lorekeep.mcp_server import configure, mcp
    except ImportError as exc:
        from lorekeep.output import error
        missing = str(exc)
        if "fastmcp" in missing.lower():
            error("lorekeep requires mcp v1.x, but mcp v2.x is installed (FastMCP was removed).")
            error("Fix: pip install 'mcp>=1.0,<2.0'  (or: uv pip install 'mcp>=1.0,<2.0')")
        else:
            error(f"'lorekeep serve' requires the 'mcp' package, which is not installed: {exc}")
            error("Fix: pip install mcp  (or: uv pip install mcp)")
        log.error(
            "serve: mcp dependency missing error_type=ImportError detail=%s",
            "fastmcp" if "fastmcp" in missing.lower() else "mcp",
            extra={"event": "serve.mcp_missing"},
        )
        raise typer.Exit(code=1)
    # Pre-check: facts.jsonl must exist before starting the server.
    facts_path = p["out"] / "facts.jsonl"
    if not facts_path.exists():
        from lorekeep.output import error
        error(
            f"No knowledge graph found at {facts_path}.\n"
            f"Run 'lorekeep compile' first to build it."
        )
        raise typer.Exit(code=1)

    try:
        configure(
            graph_dir=p["out"], allowed_ns=allowed,
            schema_path=p["schema"], pending_dir=p.get("pending"),
            write_ns=write_ns,
        )
    except (FileNotFoundError, ValueError) as exc:
        from lorekeep.output import error
        error(str(exc))
        log.error(
            "serve: invalid startup configuration detail=%s", exc,
            extra={"event": "serve.invalid_config"},
        )
        raise typer.Exit(code=1)
    log.info(
        "MCP server starting transport=%s namespace_count=%s",
        transport, len(allowed),
        extra={"event": "mcp.start"},
    )
    from lorekeep.stdio_errors import (
        disconnect_error_types,
        is_client_disconnect,
        prepare_windows_stdio_loop,
    )

    if transport == "stdio":
        prepare_windows_stdio_loop()
    try:
        mcp.run(transport=transport)
    except Exception as exc:
        if is_client_disconnect(exc):
            log.info(
                "MCP client disconnected transport=%s error_types=%s",
                transport,
                ",".join(disconnect_error_types(exc)),
                extra={"event": "mcp.stop"},
            )
            return
        log.exception(
            "MCP server stopped unexpectedly error_type=%s", type(exc).__name__,
            extra={"event": "mcp.failed"},
        )
        # Do not re-raise: sys.excepthook would open a second auto-issue
        # (runtime.unhandled) for the same failure.
        raise typer.Exit(code=1)


mcp_app = typer.Typer(help="Coding-agent integration.")
app.add_typer(mcp_app, name="mcp")

config_app = typer.Typer(help="View and edit lorekeep config.")
app.add_typer(config_app, name="config")
schema_app = typer.Typer(help="Inspect and upgrade the graph schema.")
app.add_typer(schema_app, name="schema")
support_app = typer.Typer(
    help="Diagnostics and automatic error reporting.",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(support_app, name="support")


@support_app.callback()
def support(
    ctx: typer.Context,
    output: Path | None = typer.Option(None, "--output", "-o", help="Write the ZIP to this path."),
    report_only: bool = typer.Option(False, "--report-only", help="Print the report without creating a ZIP."),
    no_print: bool = typer.Option(False, "--no-print", help="Create the ZIP without printing the report."),
) -> None:
    """Print a support report and create its redacted attachment bundle."""
    if ctx.invoked_subcommand is not None:
        return
    if report_only and no_print:
        raise typer.BadParameter("--report-only and --no-print cannot be used together")
    if report_only and output is not None:
        raise typer.BadParameter("--output is only used when creating a bundle")

    from lorekeep.support import build_report, create_bundle
    if not no_print:
        typer.echo(build_report(), nl=False)
    if report_only:
        return
    path, digest = create_bundle(output)
    if not no_print:
        typer.echo()
    typer.echo(f"support bundle: {path}")
    typer.echo(f"sha256: {digest}")


@support_app.command("report", hidden=True)
def support_report(
    output: Path | None = typer.Option(None, "--output", "-o", help="Write Markdown to this path."),
) -> None:
    """Print a metadata-only report suitable for a GitHub issue."""
    from lorekeep.support import build_report, write_report
    if output is None:
        typer.echo(build_report(), nl=False)
    else:
        write_report(output)
        typer.echo(f"support report: {output}")


@support_app.command("bundle", hidden=True)
def support_bundle(
    output: Path | None = typer.Option(None, "--output", "-o", help="Write the ZIP to this path."),
) -> None:
    """Create a redacted, allowlisted ZIP for attachment to a bug report."""
    from lorekeep.support import create_bundle
    path, digest = create_bundle(output)
    typer.echo(f"support bundle: {path}")
    typer.echo(f"sha256: {digest}")


# ── support auto-reporting (merged from bugreport) ───────────────────────────

def _set_bugreport_enabled(value: bool) -> None:
    """Write bugreport.enabled in config.yaml."""
    import yaml
    from lorekeep.output import ok
    p = resolve_paths()
    if not p["config"].exists():
        typer.echo("No config.yaml found — run `lorekeep init` first.")
        raise typer.Exit(code=1)
    data = yaml.safe_load(p["config"].read_text(encoding="utf-8")) or {}
    br = data.setdefault("bugreport", {})
    br["enabled"] = value
    p["config"].write_text(
        yaml.dump(data, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
    ok(f"auto bug-report {'enabled' if value else 'disabled'}")


@support_app.command("on")
def support_on() -> None:
    """Enable automatic GitHub issue creation on errors."""
    _set_bugreport_enabled(True)


@support_app.command("off")
def support_off() -> None:
    """Disable automatic GitHub issue creation on errors."""
    _set_bugreport_enabled(False)


@support_app.command("status")
def support_status() -> None:
    """Show auto-report configuration, dedup stats, and token resolution."""
    import json
    from lorekeep.bugreport import _dedup_path, _load_dedup, _resolve_token
    from lorekeep.config import load_config
    from lorekeep.output import dim, info

    p = resolve_paths()
    cfg = load_config(p["config"])
    br = cfg.bugreport

    state = "enabled" if br.enabled else "disabled"
    info(f"auto bug-report: {state}")
    typer.echo(f"  repo: {br.repo}")
    typer.echo(f"  token env: {br.token_env}")

    # Show token resolution from all sources.
    token = _resolve_token(br.token_env)
    if token:
        sources = []
        if os.environ.get(br.token_env):
            sources.append(br.token_env)
        if os.environ.get("GITHUB_TOKEN"):
            sources.append("GITHUB_TOKEN")
        typer.echo(f"  token source: {', '.join(sources) or 'gh auth'}")
    else:
        typer.echo("  token: not found")

    typer.echo(f"  labels: {', '.join(br.labels)}")

    dpath = _dedup_path()
    dedup = _load_dedup(dpath)
    reported = len(dedup)
    total = sum(v.get("count", 1) for v in dedup.values())
    typer.echo(f"  dedup file: {dpath}")
    typer.echo(f"  issues created: {reported}")
    typer.echo(f"  total occurrences: {total}")
    if not dedup:
        dim("  (no errors reported yet)")


@schema_app.command("upgrade")
def schema_upgrade(
    dry_run: bool = typer.Option(False, "--dry-run", help="Show the upgrade without writing."),
    force: bool = typer.Option(False, "--force", help="Replace a custom older schema after backing it up."),
) -> None:
    """Upgrade a stock ontology schema to the latest version, preserving a backup."""
    from lorekeep.output import info, ok, warn
    from lorekeep.schema_io import upgrade_schema

    p = resolve_paths()
    result = upgrade_schema(p["schema"], dry_run=dry_run, force=force)
    if result["custom"] and not result["changed"]:
        warn(
            "custom schema detected; re-run with --force only after reviewing "
            "the current ontology changes"
        )
        raise typer.Exit(code=2)
    if not result["changed"]:
        ok(f"schema already at version {result['to']}")
        return
    action = "would upgrade" if dry_run else "upgraded"
    info(f"{action} schema v{result['from']} → v{result['to']}")
    if not dry_run:
        ok(f"backup: {result['backup']}")
        info("next: run `lorekeep compile` to rebuild the derived graph")


@config_app.command("show")
def config_show() -> None:
    """Print the current config.yaml."""
    p = resolve_paths()
    if not p["config"].exists():
        typer.echo("No config.yaml found — run `lorekeep init` first.")
        raise typer.Exit(code=1)
    # Persist the one-time ``ns`` -> ``namespaces`` migration before display.
    migrate_config_file(p["config"])
    typer.echo(p["config"].read_text(encoding="utf-8"))


@config_app.command("set")
def config_set(
    key: str = typer.Argument(..., help="Dot-notation key (e.g. provider.model)"),
    value: str = typer.Argument(..., help="Value to set"),
) -> None:
    """Set a config value (e.g. `config set provider.model deepseek/deepseek-chat`)."""
    import yaml
    p = resolve_paths()
    if not p["config"].exists():
        typer.echo("No config.yaml found — run `lorekeep init` first.")
        raise typer.Exit(code=1)

    migrate_config_file(p["config"])

    renamed = {
        "ns.default": "namespaces.read",
        "ns.personal": "namespaces.write",
    }
    if key in renamed:
        typer.echo(f"{key} was renamed; use {renamed[key]}", err=True)
        raise typer.Exit(code=1)

    data = yaml.safe_load(p["config"].read_text(encoding="utf-8")) or {}

    keys = key.split(".")
    target = data
    for k in keys[:-1]:
        target = target.setdefault(k, {})

    final_key = keys[-1]
    if key == "namespaces.read" or isinstance(target.get(final_key), list):
        target[final_key] = [v.strip() for v in value.split(",")]
    elif isinstance(target.get(final_key), bool):
        target[final_key] = value.lower() in ("true", "1", "yes")
    elif isinstance(target.get(final_key), int):
        target[final_key] = int(value)
    elif isinstance(target.get(final_key), float):
        target[final_key] = float(value)
    elif value.lower() in ("null", "none", ""):
        target[final_key] = None
    else:
        target[final_key] = value

    if key == "provider.model":
        try:
            validate_model_prefix(target[final_key])
        except ValueError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1)
    elif key == "compile.language":
        try:
            CompileConfig(language=target[final_key])
        except ValueError:
            typer.echo(
                "compile.language must be a lowercase ISO 639-1 code "
                "(for example: en, vi)",
                err=True,
            )
            raise typer.Exit(code=1)
    elif key == "namespaces.write":
        try:
            target[final_key] = NamespacesConfig(
                write=target[final_key],
            ).write
        except ValueError:
            typer.echo(
                "namespaces.write must be one concrete namespace",
                err=True,
            )
            raise typer.Exit(code=1)

    p["config"].write_text(
        yaml.dump(data, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
    typer.echo(f"  {key} = {value}")


def _validate_scope(scope: str) -> str:
    """Reject anything but the two real scopes, in one place."""
    if scope not in ("project", "user"):
        typer.echo(f"unknown scope: {scope} (choose project|user)")
        raise typer.Exit(code=1)
    return scope


def _wire_scope(acfg) -> str:
    """The configured wiring scope. ``wire_scope`` is a plain str, so check it."""
    if acfg.wire_scope not in ("project", "user"):
        typer.echo(
            f"config error: agents.wire_scope is {acfg.wire_scope!r} — fix with "
            "`lorekeep config set agents.wire_scope user`",
            err=True,
        )
        raise typer.Exit(code=1)
    return acfg.wire_scope


def _resolve_agent_arg(agent: str):
    """Look up one :class:`AgentSpec` by name, exiting 1 on an unknown name.

    Shared by ``mcp add`` and ``agent wire`` so the two cannot drift.
    """
    from lorekeep.integrations.registry import AGENT_NAMES, find

    spec = find(agent)
    if spec is None:
        typer.echo(f"unknown agent: {agent} (choose {'|'.join(AGENT_NAMES)})")
        raise typer.Exit(code=1)
    return spec


@mcp_app.command("add")
def mcp_add(
    agent: str = typer.Option(..., "--agent", help="claude | cursor | codex | opencode | grok | qoder | copilot | cmd"),
    scope: str = typer.Option(None, "--scope", help="project | user (default: agents.wire_scope)"),
    read_ns: str = typer.Option(
        None, "--read-ns",
        help="read namespace pattern override (default: namespaces.read)",
    ),
) -> None:
    """Write the agent's MCP config + print an agent-memory snippet."""
    from lorekeep.integrations.common import (
        agent_memory_snippet,
        resolve_command,
        resolve_hook_command,
    )

    p = resolve_paths()
    config = load_config(p["config"])
    command, args = resolve_command(config.install_source)
    scope = _validate_scope(scope) if scope else _wire_scope(config.agents)
    spec = _resolve_agent_arg(agent)

    writer = spec.writer()
    target = Path.cwd()
    written = writer.write_config(target, command, args, read_ns, scope=scope)
    if written is None:
        typer.echo(f"{agent} config unchanged -> {writer.config_target(target, scope)}")
    else:
        typer.echo(f"wrote {agent} config -> {written}")
    native_hook_path = spec.hook_path(target, scope)
    if spec.hook is not None and native_hook_path is not None:
        hook_cmd, hook_args = resolve_hook_command(
            spec.name, spec.hook.trigger, p["home"],
        )
        hook_path = writer.write_hook(target, hook_cmd, hook_args, scope=scope)
        if hook_path is None:
            typer.echo(f"lifecycle hook unchanged -> {native_hook_path}")
        else:
            label = "session-end" if spec.hook.exact else spec.hook.trigger
            typer.echo(f"wrote {label} hook -> {hook_path}")
    elif spec.hook is not None:
        typer.echo(
            f"{agent} lifecycle hook is unavailable at {scope} scope; "
            "use --scope user to enable local capture"
        )
    typer.echo("\n" + agent_memory_snippet())


@app.command()
def doctor() -> None:
    """Validate the full install: graph loads with no dangling edges, schema
    is valid, MCP tools respond, and the configured LLM provider is reachable.

    This is the sole validation command — run it after `compile` or when
    troubleshooting. Provider ping is skipped automatically when no API key
    is configured."""
    p = resolve_paths()
    problems = []
    notes = []
    from lorekeep.output import error as _err, info as _info, ok as _ok

    facts_path = p["out"] / "facts.jsonl"
    if not facts_path.exists():
        _err(f"FAIL: facts.jsonl not found at {facts_path}")
        raise typer.Exit(code=1)

    try:
        from lorekeep.store.graph import GraphStore
        store = GraphStore.from_jsonl(facts_path)
    except Exception as exc:
        _err(f"FAIL: cannot load graph: {exc}")
        raise typer.Exit(code=1)

    if not p["schema"].exists():
        problems.append("schema.json missing")
    else:
        try:
            load_schema(p["schema"])
        except Exception as exc:
            problems.append(f"schema invalid: {exc}")

    # Load config once; a bare model fails fast here (reported, not crashed).
    try:
        config = load_config(p["config"])
    except ValueError as exc:
        _err(f"FAIL: provider config: {exc}")
        raise typer.Exit(code=1)

    allowed, write_ns = _runtime_namespaces(config)

    try:
        from lorekeep.mcp_server import configure, context
        configure(
            graph_dir=p["out"], allowed_ns=allowed,
            schema_path=p["schema"], pending_dir=p.get("pending"),
            write_ns=write_ns,
        )
        ns = context("namespaces")["namespaces"]
    except Exception as exc:
        problems.append(f"mcp configure/tool failed: {exc}")
        ns = []

    # Hint: api_base is redundant for native providers — litellm already knows
    # their endpoint. openai/ + api_base is the documented custom
    # OpenAI-compatible pattern (vLLM, LM Studio, proxy), so that is a
    # confirmation note rather than a "usually unnecessary" warning.
    if config.provider.api_base:
        prefix = model_provider(config.provider.model)
        if prefix == "openai":
            notes.append(
                "provider: custom OpenAI-compatible endpoint "
                f"({config.provider.api_base})"
            )
        elif prefix in NATIVE_PROVIDERS:
            notes.append(
                f"provider: hint — api_base set for {prefix}/, but litellm "
                "already knows this endpoint; usually unnecessary (only "
                "vllm/lm_studio/proxies/non-default-ollama need api_base)."
            )

    # Provider connectivity probe — catches the most common breakage (bad
    # model/api_base/api_key) that a graph/schema check alone misses.
    if os.environ.get("LOREKEEP_DOCTOR_NO_PING") == "1":
        notes.append("provider: ping skipped (LOREKEEP_DOCTOR_NO_PING=1)")
    elif not _has_provider(config):
        notes.append("provider: skipped (no API key set — compile will skip until you add one)")
    else:
        try:
            _make_provider(config).ping()
            notes.append(f"provider: ok ({config.provider.model})")
        except Exception as exc:
            msg = str(exc).lower()
            if "401" in msg or "authentication" in msg or "unauthorized" in msg:
                problems.append("provider: AUTH FAILED (bad API key)")
            elif "404" in msg or "not found" in msg or "model" in msg and "exist" in msg:
                problems.append(f"provider: MODEL NOT FOUND ({config.provider.model}) — check the model string")
            elif "connection" in msg or "timeout" in msg or "unreachable" in msg or "refused" in msg:
                problems.append("provider: ENDPOINT UNREACHABLE (check api_base / network)")
            else:
                problems.append(f"provider: FAILED — {exc}")

    if problems:
        _err("FAIL: " + "; ".join(problems))
        raise typer.Exit(code=1)

    _ok(
        f"all checks passed: {len(store.node_ids())} nodes, "
        f"{len(store.all_edges())} edges, namespaces={ns}"
    )

    _doctor_agent_section(config)
    _doctor_session_section(p)
    _doctor_hook_event_section(p)

    for note in notes:
        _info(note)


def _doctor_agent_section(config: Config) -> None:
    """Print a compact agent-connection table in the doctor report.

    Reuses ``_agent_report`` but shows only the essentials: name, wired,
    ingest path. Full details (config paths, hooks) live in ``agent detect``.
    """
    scope = _wire_scope(config.agents)
    rows = _agent_report(scope)
    installed = [r for r in rows if r["installed"]]
    if not installed:
        return  # no agents to report — fresh machine

    typer.echo("")
    typer.echo("── agents ────────────────────────────────────────")
    table = [("agent", "mcp wired", "capture", "ingest")]
    for r in installed:
        table.append((
            r["name"],
            "yes" if r["wired"] else "no",
            _hook_label(r),
            " + ".join(r["ingest"]) or "—",
        ))
    widths = [max(len(row[i]) for row in table) for i in range(4)]
    for row in table:
        typer.echo("  ".join(c.ljust(w) for c, w in zip(row, widths)).rstrip())


def _doctor_session_section(p: dict) -> None:
    """Print the most recently imported session per agent namespace.

    Scans ``raw/*-session/`` for the newest ``.md`` per namespace. Silently
    skips when no session namespace exists (fresh install, tests).
    """
    sessions = _last_session_imports(p["raw"])
    if not sessions:
        return

    typer.echo("")
    typer.echo("── last session import ───────────────────────────")
    table = [("agent", "session", "imported")]
    for s in sessions:
        table.append((
            s["agent"],
            s["session"],
            f"{s['ts']} ({_format_relative_time(s['mtime'])})",
        ))
    widths = [max(len(row[i]) for row in table) for i in range(3)]
    for row in table:
        typer.echo("  ".join(c.ljust(w) for c, w in zip(row, widths)).rstrip())


def _doctor_hook_event_section(p: dict) -> None:
    """Show queued/retrying lifecycle events without reading transcripts."""
    root = p["home"] / "hook-events"
    if not root.is_dir():
        return

    rows: list[tuple[str, str, str]] = []
    for path in sorted(root.glob("*/*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            agent = str(data.get("agent") or path.parent.name)
            session = str(data.get("session_id") or "unknown")
            attempts = int(data.get("attempts") or 0)
            trigger = str(data.get("trigger") or "")
            if attempts:
                state = f"retrying ({attempts})"
            elif trigger == "session_end":
                state = "queued"
            else:
                state = "waiting for idle"
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            agent, session, state = path.parent.name, path.stem, "invalid"
        rows.append((agent, session, state))

    if not rows:
        return
    typer.echo("")
    typer.echo("── lifecycle event queue ─────────────────────────")
    table = [("agent", "session", "state"), *rows]
    widths = [max(len(row[i]) for row in table) for i in range(3)]
    for row in table:
        typer.echo("  ".join(c.ljust(w) for c, w in zip(row, widths)).rstrip())


def _is_interactive() -> bool:
    """True if stdin is a TTY (user can answer prompts)."""
    import sys
    return sys.stdin.isatty()


@app.command()
def init(
    yes: bool = typer.Option(
        False, "--yes", "-y",
        help="Skip interactive prompts, use defaults",
    ),
    watch: bool = typer.Option(
        True, "--watch/--no-watch",
        help="Install the OS daemon service (systemd/launchd/startup) after setup",
    ),
) -> None:
    """Bootstrap the data home, wire agents, import sessions, compile, and install the daemon service."""
    p = resolve_paths()
    created = []
    p["config"].parent.mkdir(parents=True, exist_ok=True)
    config_existed = p["config"].exists()
    personal_ns = "me"
    name = ""
    bio = ""

    if not config_existed:
        if not yes and _is_interactive():
            personal_ns, name, bio = _interactive_init(p)
        else:
            p["config"].write_text(DEFAULT_CONFIG_YAML)
            personal_ns = load_config(p["config"]).namespaces.write
        created.append(str(p["config"]))
    elif p["config"].exists():
        try:
            load_config(p["config"])
        except Exception as exc:
            log.warning(
                "existing config could not be loaded error_type=%s",
                type(exc).__name__, extra={"event": "init.config_invalid"},
            )

    p["schema"].parent.mkdir(parents=True, exist_ok=True)
    if not p["schema"].exists():
        p["schema"].write_text(json.dumps(DEFAULT_SCHEMA, indent=2))
        created.append(str(p["schema"]))

    p["raw"].mkdir(parents=True, exist_ok=True)
    p["out"].mkdir(parents=True, exist_ok=True)
    p["pending"].mkdir(parents=True, exist_ok=True)

    from lorekeep.output import info, ok
    ok(f"home ready: config={p['config']}")
    info(f"  schema={p['schema']}  raw={p['raw']}  graph={p['out']}")
    if created:
        typer.echo(f"  wrote defaults: {created}")
    else:
        typer.echo("  (existing config/schema preserved)")

    # First file: the user's about.md (profile from onboarding).
    # Written on first init — always, even if raw/ has other files.
    if not config_existed:
        ns_dir = p["raw"] / personal_ns
        ns_dir.mkdir(parents=True, exist_ok=True)
        about_path = ns_dir / "about.md"
        if not about_path.exists():
            about_md = (
                f"# {name or '(your name)'}\n\n"
                f"{bio or '(your bio — a one-line intro about you)'}\n"
            )
            about_path.write_text(about_md)
            typer.echo(f"  wrote: {about_path}")

            # Optional profile scaffold — the editable source for the personal
            # (subject-centric) namespace. User fills it via Obsidian/Tolaria;
            # the wiki is a derived view.
            profile_path = ns_dir / "profile.md"
            if not profile_path.exists():
                from lorekeep.defaults import DEFAULT_PROFILE_TEMPLATE
                profile_path.write_text(DEFAULT_PROFILE_TEMPLATE)
                typer.echo(f"  wrote: {profile_path}")
                typer.echo(
                    "  hint: edit profile.md (role/domains/skills/goals) in "
                    "Obsidian/Tolaria, then `lorekeep compile` — the wiki reflects you."
                )

    # --- One-click chain: wire → import → compile → persistent daemon ------
    # Wiring runs on every init: it is free and idempotent, so re-running
    # init is how you pick up an agent installed after the first run.
    wire_scope = _wire_scope(load_config(p["config"]).agents)
    _auto_wire_agents(p, None, scope=wire_scope)

    if not config_existed:
        _auto_import_and_compile(p)

    # Persistent OS service is the default. --no-watch skips it (agent-controlled
    # mode). If install fails, fall back to an ad-hoc `agent watch` on a TTY.
    if watch:
        import sys
        installed = _install_daemon_service(p)
        ad_hoc = False
        if installed and sys.platform == "win32" and _is_interactive():
            # Windows startup scripts do not launch until the next login.
            _start_daemon(p)
            ad_hoc = True
        elif not installed and _is_interactive():
            _start_daemon(p)
            ad_hoc = True
        elif not installed and not _is_interactive():
            typer.echo(
                "\n  (skipped daemon start in non-interactive mode — "
                "run `lorekeep agent service install` or `lorekeep agent watch`)"
            )

        if not config_existed and (installed or ad_hoc):
            log_path = p.get("logs", p["home"] / "logs") / "daemon-bootstrap.log"
            wiki_path = p.get("wiki", p["home"] / "wiki")
            if installed:
                typer.echo("\n  Daemon installed as a persistent OS service.")
                typer.echo("  Uninstall later:  lorekeep agent service uninstall")
            else:
                typer.echo("\n  Daemon watching raw/ for changes.")
            typer.echo(f"  Watch progress:  tail -f {log_path}")
            typer.echo(f"  Open wiki:       {wiki_path}")
            typer.echo("\nRestart your agent")
    else:
        typer.echo(
            "\n  Daemon disabled (--no-watch). Agent-controlled mode:\n"
            "  - Run `lorekeep compile` after editing raw/*.md (does compile + resolve + wiki)\n"
            "  - Run `lorekeep resolve` to merge agent-proposed facts (zero LLM cost)\n"
            "  - MCP server lazy-reloads on next query — no daemon needed"
        )

    if config_existed:
        typer.echo("\nAlready initialized.")


def _interactive_init(p: dict) -> tuple[str, str, str]:
    """Walk through provider, API key, write namespace, name, and bio.

    Returns ``(ns, name, bio)`` — the concrete write namespace plus the
    user's profile answers, so the caller can write ``raw/<ns>/about.md``.
    """
    from lorekeep.providers import (
        DYNAMIC_ENDPOINT_DEFAULTS,
        POPULAR,
        config_model_name,
        format_cost,
        is_dynamic,
        list_models,
        optional_api_key,
        provider_label,
        search_providers,
    )

    typer.echo("\n=== Lorekeep setup ===\n")

    typer.echo(
        "Lorekeep uses an LLM at compile time to extract entities, relationships,\n"
        "and temporal facts from your markdown docs. It is NOT used at query time\n"
        "(agents read the graph directly via MCP — zero LLM cost per query).\n"
    )

    # ── Provider selection ─────────────────────────────────────────────
    typer.echo("Popular providers:")
    for i, prov in enumerate(POPULAR, 1):
        typer.echo(f"  {i}. {provider_label(prov)}")
    typer.echo(f"  {len(POPULAR) + 1}. [Search all providers]")
    typer.echo(f"  {len(POPULAR) + 2}. [Skip — configure later]")

    choice = typer.prompt("\nChoice", default="1")

    idx = int(choice) if choice.isdigit() else 0
    if idx == len(POPULAR) + 2 or choice.lower() == "skip":
        typer.echo("  → Skipped (edit config.yaml to add a provider later)\n")
        ns = typer.prompt("Write namespace", default="me")
        name = typer.prompt("Your name", default="")
        bio = typer.prompt("Bio (one-line intro)", default="")
        _write_config(p, model="openai/gpt-4o-mini",
                       api_base=None, api_key_env=None, api_key=None, ns=ns)
        return ns, name, bio

    if idx == len(POPULAR) + 1 or choice.lower() == "search":
        query = typer.prompt("Type provider name to search", default="")
        from lorekeep.providers import list_providers
        all_providers = list_providers()
        results = search_providers(query, all_providers)
        if not results:
            typer.echo("  → No matches. Using default (openai).")
            provider_name = "openai"
        else:
            typer.echo("")
            for i, (prov, count) in enumerate(results[:20], 1):
                label = provider_label(prov)
                if count:
                    typer.echo(f"  {i}. {label} ({count} models)")
                else:
                    typer.echo(f"  {i}. {label}")
            sub = typer.prompt("Choice", default="1")
            sub_idx = int(sub) if sub.isdigit() else 1
            provider_name = results[min(sub_idx - 1, len(results) - 1)][0]
    elif 1 <= idx <= len(POPULAR):
        provider_name = POPULAR[idx - 1]
    else:
        provider_name = "openai"

    typer.echo(f"  → {provider_label(provider_name)}\n")

    # ── Model selection ────────────────────────────────────────────────
    typer.echo(f"Select a model for {provider_label(provider_name)} (used for knowledge extraction):\n")
    if is_dynamic(provider_name):
        model_default, base_default = DYNAMIC_ENDPOINT_DEFAULTS.get(
            provider_name, ("", ""),
        )
        if provider_name == "openai_compat":
            typer.echo(
                "Any OpenAI-compatible /v1/chat/completions endpoint works here\n"
                "(vLLM, LM Studio, LiteLLM proxy, OneAPI/NewAPI, or a custom gateway).\n"
                "LiteLLM routes it as openai/{model} plus api_base.\n"
            )
        model = typer.prompt(
            "Model name as served by the endpoint",
            default=model_default,
        )
        if not model.strip():
            model = typer.prompt("Model name (required)", default="")
        if not model.strip():
            typer.echo("  a model name is required for this provider")
            raise typer.Exit(code=1)
        api_base = typer.prompt(
            "API base URL", default=base_default,
        ) or None
    else:
        models = list_models(provider_name)
        if models:
            typer.echo("Models (chat, sorted by cost):")
            for i, m in enumerate(models[:20], 1):
                fc = format_cost(m.input_cost)
                fc_out = format_cost(m.output_cost)
                ctx = f"{m.max_input_tokens // 1000}K" if m.max_input_tokens else "?"
                typer.echo(f"  {i}. {m.model}  in={fc} out={fc_out} ctx={ctx}")
            typer.echo(f"  {len(models) + 1}. [Type custom model name]")
            mchoice = typer.prompt("Choice", default="1")
            midx = int(mchoice) if mchoice.isdigit() else 1
            if 1 <= midx <= len(models):
                model = models[midx - 1].model
            elif midx == len(models) + 1:
                model = typer.prompt("Model name (litellm string)", default="")
            else:
                model = models[0].model
        else:
            model = typer.prompt("Model name (litellm string)", default="")
        api_base = None

    # Prefix a bare model name with the litellm route so the written config
    # is always a valid litellm string. openai_compat is a menu alias and
    # persists as openai/{model} plus api_base.
    model = config_model_name(model, provider_name)

    typer.echo(f"  → {model}\n")

    # ── API key (skip for local providers; Shift+Tab toggles key ↔ env) ─
    env_var = None
    api_key = None
    if is_dynamic(provider_name) and not optional_api_key(provider_name):
        typer.echo("  → No API key needed for local provider.\n")
    else:
        from lorekeep.init_prompt import prompt_api_credential
        cred = prompt_api_credential(
            provider_name, optional=optional_api_key(provider_name),
        )
        api_key = cred.api_key
        env_var = cred.api_key_env

    # ── Namespace + profile ────────────────────────────────────────────
    ns = typer.prompt("Write namespace", default="me")
    name = typer.prompt("Your name", default="")
    typer.echo("  (your bio → raw/<ns>/about.md → compiled into the graph)")
    bio = typer.prompt("Bio (one-line intro)", default="")

    _write_config(
        p, model=model, api_base=api_base,
        api_key_env=env_var if not api_key else None,
        api_key=api_key, ns=ns,
    )
    return ns, name, bio


def _write_config(p, model, api_base, api_key_env, api_key, ns):
    """Write config.yaml from provider selection."""
    import yaml
    install_source = "local" if (Path.cwd() / ".lorekeep").exists() else "pypi"
    config = {
        "provider": {
            "model": model,
            "api_base": api_base,
            "api_key_env": api_key_env,
            "api_key": api_key,
            "temperature": 0.0,
            "timeout_seconds": 120,
            "max_retries": 2,
        },
        "compile": {"chunk_lines": 60},
        "namespaces": {"read": ["*"], "write": ns},
        "install_source": install_source,
    }
    p["config"].write_text(yaml.dump(config, default_flow_style=False, sort_keys=False))


def _wire_one(
    spec, target: Path, ns: str | None, *, scope: str = "user",
) -> tuple[Path | None, Path | None]:
    """Write one agent's MCP config + best available lifecycle hook.

    Returns ``(config_path, hook_path)``, each ``None`` when that file
    already held the desired wiring.
    """
    from lorekeep.integrations.common import resolve_command, resolve_hook_command

    paths = resolve_paths()
    config = load_config(paths["config"])
    command, args = resolve_command(config.install_source)

    writer = spec.writer()
    written = writer.write_config(target, command, args, ns, scope=scope)
    hooked = None
    if spec.hook is not None and spec.hook_path(target, scope) is not None:
        hook_cmd, hook_args = resolve_hook_command(
            spec.name, spec.hook.trigger, paths["home"],
        )
        hooked = writer.write_hook(target, hook_cmd, hook_args, scope=scope)
    return written, hooked


def _auto_wire_agents(p: dict, ns: str | None, *, scope: str = "user") -> None:
    """Detect every installed coding agent and write its MCP config.

    Idempotent: a writer that finds the desired entry already present
    reports ``unchanged`` instead of rewriting the file.
    """
    from lorekeep.integrations.detect import detect_agents
    from lorekeep.integrations.common import agent_memory_snippet
    from lorekeep.integrations.registry import find

    detected = detect_agents()
    if not detected:
        typer.echo("\n  No coding agents detected — run `lorekeep mcp add --agent <name>` after install.")
        return

    target = Path.cwd()
    typer.echo(f"\n  Detected agents: {', '.join(detected)}")
    for agent_name in detected:
        spec = find(agent_name)
        if spec is None:
            continue
        try:
            written, hooked = _wire_one(spec, target, ns, scope=scope)
            typer.echo(
                f"  wired {agent_name} -> {written}" if written
                else f"  {agent_name} already wired -> {spec.config_path(target, scope)}"
            )
            if spec.hook is not None and hooked:
                label = "session-end" if spec.hook.exact else spec.hook.trigger
                typer.echo(f"  hooked {agent_name} {label} -> {hooked}")
            elif spec.hook is not None and spec.hook_path(target, scope) is None:
                typer.echo(
                    f"  {agent_name}: lifecycle capture requires user scope"
                )
        except Exception as exc:
            log.warning(
                "agent wiring failed agent=%s error_type=%s",
                agent_name, type(exc).__name__,
                extra={"event": "init.agent_wiring_failed"},
            )
            typer.echo(f"  {agent_name}: failed ({exc})")

    typer.echo("\n  " + agent_memory_snippet().replace("\n", "\n  ").strip())


def _sync_agent_wiring(
    *, scope: str, ns: str | None, enabled: list[str],
    backoff: dict[str, float], now: float,
) -> list[tuple[str, Path]]:
    """Wire every detected agent, returning only the targets that changed.

    A steady state is silent: the writers report ``unchanged`` without touching
    a file, which is what makes this safe to run on a timer forever. An agent
    whose target cannot be written — read-only ``~``, a config another process
    left unparseable — is backed off for an hour rather than retried, and
    logged, on every pass.
    """
    from lorekeep.integrations.detect import detect_agents
    from lorekeep.integrations.registry import find

    target = Path.cwd()
    changed: list[tuple[str, Path]] = []

    for name in detect_agents():
        if name not in enabled or now < backoff.get(name, 0.0):
            continue
        spec = find(name)
        if spec is None:
            continue
        try:
            written, hooked = _wire_one(spec, target, ns, scope=scope)
        except Exception as exc:
            backoff[name] = now + 3600
            log.warning(
                "agent wiring failed agent=%s error_type=%s retry_after_seconds=3600",
                name, type(exc).__name__,
                extra={"event": "daemon.wire_failed"},
            )
            continue
        backoff.pop(name, None)
        changed += [(name, path) for path in (written, hooked) if path]

    return changed


def _auto_import_and_compile(p: dict, *, defer: bool = False) -> None:
    """Quick-import every agent's memory files, then compile if a provider is available.

    When *defer* is True, skip the compile step — the daemon will handle it
    via the .compile-requested sentinel.
    """
    from lorekeep.integrations.registry import all_specs

    # --- Quick import: agent-authored memory files (zero LLM cost) ---------
    for spec in all_specs():
        if spec.memory is None or spec.memory_ns is None:
            continue
        try:
            written = getattr(spec.importer(), spec.memory.import_fn)(
                p["raw"], namespace=spec.memory_ns,
            )
            if written:
                typer.echo(f"  imported {len(written)} memory file(s) from {spec.label}")
        except Exception as exc:
            log.warning(
                "automatic memory import failed agent=%s error_type=%s",
                spec.name, type(exc).__name__,
                extra={"event": "init.import_failed"},
            )
            if os.environ.get("LOREKEEP_DEBUG"):
                typer.echo(f"  import error ({spec.name}): {exc}")

    # --- Compile (if provider is usable) ----------------------------------
    if defer:
        return  # daemon will compile via sentinel

    schema = load_schema(p["schema"])
    config = load_config(p["config"])

    has_key = _has_provider(config)

    if not has_key:
        env_hint = ""
        if config.provider.api_key_env:
            env_hint = f" (export {config.provider.api_key_env}=sk-... before running `lorekeep compile`)"
        elif not config.provider.api_key:
            env_hint = " (add api_key to config.yaml, then run `lorekeep compile`)"
        typer.echo(
            f"  docs saved to raw/ but not yet compiled{env_hint}"
        )
        return

    try:
        provider = _make_provider(config)
        with _progress_ctx(p["raw"], config.compile.chunk_lines) as handle:
            manifest = compile_graph(
                raw_root=p["raw"], out_dir=p["out"], schema=schema,
                provider=provider, cache_path=p["cache"],
                chunk_lines=config.compile.chunk_lines,
                on_progress=_progress_cb(handle),
                personal_ns=config.namespaces.write,
                language=config.compile.language,
                prev_aliases=_load_prev_aliases(p["out"] / "facts.jsonl"),
                prev_quarantine=_load_prev_quarantine(p["out"] / "facts.jsonl"),
            max_workers=config.compile.max_workers,
            flush_interval=config.compile.flush_interval,
            )
        _report_compile_errors(manifest, exit_on_total_failure=False)
        _report_content_quality(manifest)
        pending_dir = p.get("pending")
        resolved = False
        if pending_dir and pending_dir.exists():
            resolved = _do_auto_resolve(
                p["out"], pending_dir, p.get("wiki"), p.get("schema"),
                replay_accepted=True,
            )
        if not resolved:
            _auto_generate_wiki(p["out"], p["wiki"], p.get("schema"))
        typer.echo(f"  compiled: {manifest.node_count} nodes, {manifest.edge_count} edges")
    except Exception as exc:
        log.exception(
            "initial compile failed error_type=%s", type(exc).__name__,
            extra={"event": "init.compile_failed"},
        )
        typer.echo(f"  compile skipped: {exc}")


def _install_daemon_service(p: dict) -> bool:
    """Install the OS-persistent daemon. Returns True on success.

    Never raises: init must still finish if systemd/launchd is unavailable.
    Tests monkeypatch this to avoid writing the developer's real user service.
    """
    from lorekeep.daemon_service import install as svc_install

    try:
        platform_name, config_path = svc_install(p["home"])
    except Exception as exc:
        log.warning(
            "daemon service install failed error_type=%s",
            type(exc).__name__, extra={"event": "init.service_install_failed"},
        )
        typer.echo(f"  daemon service skipped: {exc}")
        return False
    log.info(
        "daemon service installed platform=%s", platform_name,
        extra={"event": "daemon.service_installed"},
    )
    typer.echo(f"  daemon service: {platform_name} → {config_path}")
    return True


def _start_daemon(p: dict) -> None:
    """Start agent watch as a background process with PID + log files."""
    _start_daemon_if_needed(p, quiet=False)


def _start_daemon_if_needed(p: dict, *, quiet: bool = True) -> None:
    """Start daemon if not already running. No-op if alive."""
    import subprocess
    import sys

    pid_path = p["home"] / ".daemon.pid"
    log_dir = p.get("logs", p["home"] / "logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "daemon-bootstrap.log"

    # Check if already running
    if pid_path.exists():
        old_pid = pid_path.read_text().strip()
        try:
            os.kill(int(old_pid), 0)
            if not quiet:
                typer.echo(f"  daemon already running (pid={old_pid})")
            return
        except (ProcessLookupError, ValueError):
            pass

    cmd = [sys.executable, "-m", "lorekeep.cli", "agent", "watch", "--interval", "60"]
    log_file = open(log_path, "a")
    try:
        os.chmod(log_path, 0o600)
    except OSError:
        pass
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    finally:
        log_file.close()
    pid_path.write_text(str(proc.pid))
    log.info(
        "background daemon started pid=%s", proc.pid,
        extra={"event": "daemon.background_started"},
    )
    if not quiet:
        typer.echo(f"  daemon started (pid={proc.pid}, log={log_path})")


@app.command()
def backup(
    init_remote: str = typer.Option(
        None, "--init", help="remote URL; sets up the backup repo + initial push"
    ),
    force: bool = typer.Option(
        False, "--force",
        help="Auto-resolve graph/wiki snapshot conflicts (remote version wins)",
    ),
) -> None:
    """Sync data-home inputs and graph/wiki snapshot to a private Git repo."""
    from lorekeep.backup import (
        BackupError,
        _resolve_durable_conflicts,
        backup as backup_home,
        init_backup,
    )
    from lorekeep.config import load_config
    from lorekeep.output import dim, error, info, ok

    p = resolve_paths()
    home = p["home"]
    cfg = load_config(p["config"])
    bcfg = cfg.backup
    durable_resolver = None
    if bcfg.auto_resolve_durable:
        prov = _make_provider(cfg)
        durable_resolver = lambda h, paths: _resolve_durable_conflicts(
            h, paths, prov,
        )
    try:
        if init_remote:
            init_backup(home, init_remote, branch=bcfg.branch)
            info(f"backup: repo ready at {home} -> {init_remote}")
        else:
            pushed = backup_home(
                home, force=force, branch=bcfg.branch,
                durable_resolver=durable_resolver,
            )
            if pushed:
                ok(f"backup: pushed to remote from {home}")
            else:
                dim(f"backup: up to date (no changes at {home})")
    except BackupError as exc:
        error(f"backup failed: {exc}")
        raise typer.Exit(code=1)


@app.command("import")
def import_cmd(
    from_source: str = typer.Option(
        "claude", "--from",
        help=(
            "Source (claude | codex | cursor | opencode | grok | qoder | "
            "copilot | cmd)"
        ),
    ),
    quick: bool = typer.Option(
        False, "--quick",
        help="Quick mode: only import memory files, no LLM transcript analysis",
    ),
    session_path: str | None = typer.Option(
        None, "--session-path",
        help="Agent session path or id (auto-detect if omitted)",
    ),
    memory_ns: str = typer.Option(
        "claude-memory", "--memory-ns",
        help="Namespace for imported memory files",
    ),
    session_ns: str | None = typer.Option(
        None, "--session-ns",
        help="Namespace for imported session files (default: <agent>-session)",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would be imported without writing files",
    ),
) -> None:
    """Import knowledge from an agent's sessions into raw/.

    Claude, Codex, Cursor, and opencode support the existing deep summarization
    flow. Lifecycle adapters for Grok, Qoder, Copilot, and Command Code perform
    a deterministic zero-LLM transcript dump.

    Sources:
      claude    Claude Code sessions. --quick copies memory/*.md only (no LLM);
                default (deep) adds LLM-summarized transcript analysis.
      cursor    Cursor composer conversations (GLOBAL state.vscdb). No memory
                files, so --quick does not apply.
      codex     Codex CLI rollout transcripts ($CODEX_HOME/sessions/).
                --quick copies memories/*.md only; default (deep) summarizes.
      opencode  opencode sessions (SQLite DB). No memory files, so --quick
                does not apply.
      grok/qoder/copilot/cmd
                local transcript capture (zero LLM; also used by hooks).
    """
    from lorekeep.output import ok
    from lorekeep.integrations.registry import AGENT_NAMES
    if from_source not in AGENT_NAMES:
        typer.echo(f"unknown source: {from_source} ({' | '.join(AGENT_NAMES)})")
        raise typer.Exit(code=1)

    p = resolve_paths()
    config = load_config(p["config"])

    # --- Codex: rollout JSONL transcripts, quick + deep --------------------
    if from_source == "codex":
        from lorekeep.importer.codex import find_current_session as find_codex, import_codex

        rollout_path = Path(session_path).expanduser() if session_path else None
        if rollout_path is None:
            rollout_path = find_codex()
        if rollout_path is None and not quick:
            typer.echo("error: no Codex session found for this project. "
                       "Run Codex CLI here first, or pass --session-path.")
            raise typer.Exit(code=1)

        result = import_codex(
            raw_root=p["raw"],
            rollout_path=rollout_path,
            quick=quick,
            memory_ns=memory_ns,
            session_ns=session_ns or "codex-session",
            provider=None if quick else _make_import_provider(config),
            dry_run=dry_run,
        )
        mem_count = len(result.get("memory", []))
        ses_count = len(result.get("session", []))
        if dry_run:
            typer.echo(f"dry-run: would import {mem_count} memories, {ses_count} session files")
        else:
            ok(f"imported: {mem_count} memories -> raw/{memory_ns}/, "
               f"{ses_count} session files -> raw/{session_ns or 'codex-session'}/")
        return

    # --- opencode: SQLite DB, deep-only ------------------------------------
    if from_source == "opencode":
        if quick:
            typer.echo("error: opencode import is deep-only (--quick not supported)")
            raise typer.Exit(code=1)

        from lorekeep.importer.opencode import find_current_session as find_oc, import_opencode

        sid = session_path or find_oc()
        if sid is None:
            typer.echo("error: no opencode session found for this project. "
                       "Run opencode here first, or pass --session-path <session-id>.")
            raise typer.Exit(code=1)

        ns = session_ns or "opencode-session"
        result = import_opencode(
            raw_root=p["raw"],
            session_id=sid,
            session_ns=ns,
            provider=_make_import_provider(config),
            dry_run=dry_run,
        )
        ses_count = len(result.get("session", []))
        if dry_run:
            typer.echo(f"dry-run: would import {ses_count} opencode session files")
        else:
            ok(f"imported: {ses_count} session files -> raw/{ns}/")
            typer.echo("next: lorekeep compile")
        return

    # --- Cursor: global composer conversations, deep-only ------------------
    if from_source == "cursor":
        if quick:
            typer.echo("error: cursor import is deep-only (--quick not supported)")
            raise typer.Exit(code=1)

        from lorekeep.importer.cursor import find_cursor_state_db, import_cursor

        if session_path:
            sp = Path(session_path).expanduser()
            db = sp if sp.is_file() else sp / "state.vscdb"
            if not db.is_file():
                typer.echo(f"error: no Cursor state.vscdb at {session_path}")
                raise typer.Exit(code=1)
        else:
            db = find_cursor_state_db()
            if db is None:
                typer.echo("error: Cursor state.vscdb not found; set CURSOR_STATE_DB "
                           "or pass --session-path")
                raise typer.Exit(code=1)

        ns = session_ns or "cursor-session"
        result = import_cursor(
            raw_root=p["raw"], db_path=db, namespace=ns,
            provider=_make_import_provider(config), dry_run=dry_run,
        )
        ses_count = len(result.get("session", []))
        if dry_run:
            typer.echo(f"dry-run: would import {ses_count} cursor session files")
        else:
            ok(f"imported: {ses_count} session files -> raw/{ns}/")
            typer.echo("next: lorekeep compile")
        return

    # --- Registry transcript adapters: deterministic zero-LLM import ------
    if from_source in {"grok", "qoder", "copilot", "cmd"}:
        from lorekeep.integrations.registry import get

        if quick:
            typer.echo(
                f"error: {from_source} transcript import is already zero-LLM; "
                "--quick is not applicable"
            )
            raise typer.Exit(code=1)

        spec = get(from_source)
        importer = spec.importer()
        handle: object | None
        if session_path:
            candidate = Path(session_path).expanduser()
            if not candidate.exists():
                typer.echo(f"error: no {from_source} session at {candidate}")
                raise typer.Exit(code=1)
            if spec.session and spec.session.handle_kind == "dir" and candidate.is_file():
                candidate = candidate.parent
            handle = candidate
        else:
            handle = getattr(importer, spec.session.locate)(Path.cwd())
        if handle is None:
            typer.echo(
                f"error: no {from_source} session found for this project. "
                "Run the agent here first, or pass --session-path."
            )
            raise typer.Exit(code=1)

        ns = session_ns or spec.session_ns
        count = _dump_session_transcript(
            from_source, handle, p["raw"], config.agents,
            namespace=ns, dry_run=dry_run,
        )
        if dry_run:
            typer.echo(f"dry-run: would import {count} session files -> raw/{ns}/")
        else:
            ok(f"imported: {count} session files -> raw/{ns}/")
            typer.echo("next: lorekeep compile")
        return

    # --- Claude: per-project session dir, quick + deep ---------------------
    if session_path:
        session_dir = Path(session_path).expanduser()
        if not session_dir.exists():
            typer.echo(f"error: no Claude session found at {session_dir}")
            raise typer.Exit(code=1)
    else:
        from lorekeep.importer.claude import find_current_session
        session_dir = find_current_session()
        if session_dir is None:
            typer.echo("error: no Claude session found. "
                       "Run Claude Code in this project first.")
            raise typer.Exit(code=1)

    provider = None if quick else _make_import_provider(config)

    from lorekeep.importer.claude import import_claude
    result = import_claude(
        raw_root=p["raw"],
        session_dir=session_dir,
        quick=quick,
        memory_ns=memory_ns,
        session_ns=session_ns or "claude-session",
        provider=provider,
        dry_run=dry_run,
    )

    mem_count = len(result.get("memory", []))
    ses_count = len(result.get("session", []))
    if dry_run:
        typer.echo(f"dry-run: would import {mem_count} memories, "
                   f"{ses_count} session files")
    else:
        ok(f"imported: {mem_count} memories -> raw/{memory_ns}/, "
           f"{ses_count} session files -> raw/{session_ns}/")
        if not quick:
            typer.echo("next: lorekeep compile")


# --- Agent subcommands --------------------------------------------------------

service_app = typer.Typer(help="Install/uninstall the daemon as a persistent OS service.")
agent_app.add_typer(service_app, name="service")


@service_app.command("install")
def daemon_install() -> None:
    """Install daemon as a persistent OS service (survives restart).

    Linux: systemd user service. macOS: launchd LaunchAgent. Windows: Startup folder.

    The service runs `lorekeep agent watch` in the background.
    """
    from lorekeep.daemon_service import install as svc_install
    p = resolve_paths()
    try:
        platform_name, config_path = svc_install(p["home"])
        log.info(
            "daemon service installed platform=%s", platform_name,
            extra={"event": "daemon.service_installed"},
        )
        typer.echo(f"daemon: installed as {platform_name} service → {config_path}")
        typer.echo(f"daemon: will auto-start on login/restart")
    except RuntimeError as exc:
        log.error(
            "daemon service install failed error_type=%s", type(exc).__name__,
            extra={"event": "daemon.service_install_failed"},
        )
        typer.echo(f"daemon: {exc}")
        raise typer.Exit(code=1)


@service_app.command("uninstall")
def daemon_uninstall() -> None:
    """Remove the persistent daemon service."""
    from lorekeep.daemon_service import uninstall as svc_uninstall
    removed = svc_uninstall()
    log.info(
        "daemon service uninstall completed removed=%s", removed,
        extra={"event": "daemon.service_uninstalled"},
    )
    if removed:
        typer.echo("daemon: service removed")
    else:
        typer.echo("daemon: no service found")


@service_app.command("status")
def daemon_status() -> None:
    """Check if the persistent daemon service is installed and running."""
    from lorekeep.daemon_service import status as svc_status
    state = svc_status()
    log.info("daemon service status checked", extra={"event": "daemon.service_status"})
    typer.echo(state)


@agent_app.command()
def ingest(
    source: str = typer.Argument(
        ..., help="Path to a source .md file under raw/",
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y",
        help="Approve all extracted facts without review",
    ),
) -> None:
    """Conversational ingest: read a source, extract facts via LLM, review and journal.

    This is the Karpathy Ingest operation: the LLM reads the source, extracts
    key facts (nodes and edges), and the human reviews them before they enter
    the knowledge graph via the pending journal.

    Run `lorekeep resolve` or `lorekeep compile` afterwards to merge approved
    facts into facts.jsonl.
    """
    from datetime import datetime, timezone

    p = resolve_paths()
    schema = load_schema(p["schema"])
    config = load_config(p["config"])
    raw_root = p["raw"]
    pending_dir = p.get("pending")
    if pending_dir is None:
        typer.echo("ingest: no pending directory configured")
        raise typer.Exit(code=1)

    source_path = Path(source).expanduser()
    if not source_path.is_absolute():
        source_path = Path.cwd() / source_path
    source_path = source_path.resolve()

    if not source_path.exists():
        typer.echo(f"ingest: source not found: {source_path}")
        raise typer.Exit(code=1)

    if not source_path.is_relative_to(raw_root.resolve()):
        typer.echo(f"ingest: source must be under raw/ ({raw_root})")
        raise typer.Exit(code=1)

    provider = _make_provider(config)

    from contextlib import nullcontext
    from lorekeep.agent import ingest_source
    from lorekeep.output import is_quiet, is_terminal, progress

    if not is_quiet() and is_terminal():
        cm = progress(f"Extracting {source_path.name}", total=None)
    else:
        cm = nullcontext(None)
    with cm as handle:
        on_progress = None
        if handle:
            def _cb(i, total, chunk, _h=handle):
                if total:
                    _h.update(total=total)
                _h.advance()
            on_progress = _cb
        try:
            result = ingest_source(
                source_path=source_path,
                raw_root=raw_root,
                provider=provider,
                schema=schema,
                chunk_lines=config.compile.chunk_lines,
                on_progress=on_progress,
                personal_ns=config.namespaces.write,
                language=config.compile.language,
            )
        except Exception as exc:
            typer.echo(f"ingest: extraction failed: {exc}")
            raise typer.Exit(code=1)

    if not result.nodes and not result.edges:
        typer.echo("ingest: no facts extracted from source")
        return

    # Show extracted facts
    typer.echo(f"\nSource: {result.source_path}  (ns={result.ns}, chunks={result.chunk_count})")
    typer.echo(f"Extracted: {len(result.nodes)} nodes, {len(result.edges)} edges\n")

    for n in result.nodes:
        props_str = ", ".join(f"{k}={v}" for k, v in n.get("props", {}).items())
        vf = n.get("valid_from", "")
        vt = n.get("valid_to", "")
        valid = f" [{vf}..{vt}]" if vf or vt else ""
        typer.echo(f"  NODE: {n['id']} ({n['type']}){valid}")
        if props_str:
            typer.echo(f"    {props_str}")

    for e in result.edges:
        vf = e.get("valid_from", "")
        vt = e.get("valid_to", "")
        valid = f" [{vf}..{vt}]" if vf or vt else ""
        typer.echo(f"  EDGE: {e['from']} --[{e['type']}]--> {e['to']}{valid}")

    # Interactive review (unless --yes)
    approved_nodes: list[dict] = []
    approved_edges: list[dict] = []

    if yes:
        approved_nodes = list(result.nodes)
        approved_edges = list(result.edges)
    else:
        typer.echo("")
        if result.nodes:
            if typer.confirm(f"Approve all {len(result.nodes)} nodes?", default=True):
                approved_nodes = list(result.nodes)
            elif typer.confirm("Review each node individually?", default=True):
                for n in result.nodes:
                    props_str = ", ".join(f"{k}={v}" for k, v in n.get("props", {}).items())
                    line = f"  {n['id']} ({n['type']}) — {props_str}"
                    if typer.confirm(f"Approve? {line}", default=True):
                        approved_nodes.append(n)

        if result.edges:
            if typer.confirm(f"Approve all {len(result.edges)} edges?", default=True):
                approved_edges = list(result.edges)
            elif typer.confirm("Review each edge individually?", default=True):
                for e in result.edges:
                    line = f"  {e['from']} --[{e['type']}]--> {e['to']}"
                    if typer.confirm(f"Approve? {line}", default=True):
                        approved_edges.append(e)

    if not approved_nodes and not approved_edges:
        typer.echo("\ningest: nothing approved — no journal entries written")
        return

    # Write approved facts to journal
    from lorekeep.journal import append_journal
    from lorekeep.models import JournalEntry

    import socket
    import uuid
    now = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    device = os.environ.get("LOREKEEP_DEVICE", socket.gethostname())
    entry_count = 0

    for n in approved_nodes:
        n["src"] = list(n.get("src", []))
        if result.source_path not in n["src"]:
            n["src"].append(result.source_path)
        entry = JournalEntry(
            fact=n,
            agent="cli-ingest",
            device=device,
            entry_id=uuid.uuid4().hex,
            ns=result.ns,
            confidence=1.0,           # human-approved → max confidence
            proposed_at=now,
            status="pending",
        )
        append_journal(pending_dir, entry, result.ns)
        entry_count += 1

    for e in approved_edges:
        e["src"] = list(e.get("src", []))
        if result.source_path not in e["src"]:
            e["src"].append(result.source_path)
        entry = JournalEntry(
            fact=e,
            agent="cli-ingest",
            device=device,
            entry_id=uuid.uuid4().hex,
            ns=result.ns,
            confidence=1.0,
            proposed_at=now,
            status="pending",
        )
        append_journal(pending_dir, entry, result.ns)
        entry_count += 1

    typer.echo(f"\ningest: {entry_count} facts written to pending/{result.ns}/journal.jsonl")
    typer.echo("next: run `lorekeep resolve` to merge into facts.jsonl")


@agent_app.command()
def lint(
    auto_fix: bool = typer.Option(
        False, "--auto-fix",
        help="Auto-apply high-confidence fixes",
    ),
    focus: str = typer.Option(
        None, "--focus",
        help="Lint a specific entity by id",
    ),
) -> None:
    """Run semantic health checks on the graph.

    See also: `lorekeep doctor` for structural validation and full install checks.
    """
    p = resolve_paths()
    facts_path = p["out"] / "facts.jsonl"
    if not facts_path.exists():
        typer.echo("lint: no graph — run `lorekeep compile` first")
        raise typer.Exit(code=1)

    from lorekeep.store.graph import GraphStore
    from lorekeep.agent import lint as agent_lint
    store = GraphStore.from_jsonl(facts_path)
    report = agent_lint(store)

    if focus:
        report.orphans = [o for o in report.orphans if o == focus]
        report.stale = [s for s in report.stale if s == focus]
        report.missing_endpoints = [m for m in report.missing_endpoints
                                     if m["edge_id"] == focus]

    if not report.has_issues:
        typer.echo("lint: no issues found")
        return

    if report.contradictions:
        typer.echo(f"contradictions: {len(report.contradictions)}")
        for c in report.contradictions[:10]:
            typer.echo(f"  {c['id']}")
    if report.orphans:
        typer.echo(f"orphans: {len(report.orphans)}")
        for o in report.orphans[:10]:
            typer.echo(f"  {o}")
    if report.stale:
        typer.echo(f"stale: {len(report.stale)}")
    if report.missing_endpoints:
        typer.echo(f"missing endpoints: {len(report.missing_endpoints)}")
    if report.coverage_gaps:
        typer.echo(f"coverage gaps: {report.coverage_gaps}")

    if auto_fix:
        from lorekeep.agent import self_heal
        from lorekeep.models import Manifest
        schema = load_schema(p["schema"]) if p["schema"].exists() else None
        healed_store, heal_report = self_heal(store, schema)
        if heal_report.changes_made:
            from lorekeep.compile.writer import write_graph
            manifest = Manifest(
                schema_version=schema.version if schema else 0,
                chunk_count=0,
                node_count=len(healed_store.all_nodes()),
                edge_count=len(healed_store.all_edges()),
                run_id="auto-heal", facts_hash="",
                compiled_at=now_iso(),
            )
            write_graph(p["out"], healed_store.all_nodes(), healed_store.all_edges(), manifest)
            typer.echo(
                f"auto-fix: removed {len(heal_report.edges_removed)} dangling edges, "
                f"deduped {len(heal_report.edges_deduped)} edges"
            )
            _auto_generate_wiki(p["out"], p.get("wiki"), p.get("schema"))
        else:
            typer.echo("auto-fix: nothing to fix (graph is clean)")
    else:
        # Preview: show how many issues are auto-fixable
        from lorekeep.agent import self_heal
        _, preview = self_heal(store)
        if preview.changes_made:
            typer.echo(
                f"\n  {preview.total_fixes} issue(s) auto-fixable — "
                f"run `lorekeep agent lint --auto-fix` to apply"
            )

    typer.echo(f"total issues: {report.issue_count}")


@agent_app.command()
def suggest() -> None:
    """Generate improvement suggestions for the graph."""
    p = resolve_paths()
    facts_path = p["out"] / "facts.jsonl"
    if not facts_path.exists():
        typer.echo("suggest: no graph — run `lorekeep compile` first")
        raise typer.Exit(code=1)

    from lorekeep.store.graph import GraphStore
    from lorekeep.agent import suggest as agent_suggest
    store = GraphStore.from_jsonl(facts_path)
    report = agent_suggest(store)

    if report.gaps:
        typer.echo("knowledge gaps:")
        for g in report.gaps:
            typer.echo(f"  {g}")
    if report.under_sourced:
        typer.echo(f"under-sourced entities: {len(report.under_sourced)}")
        for u in report.under_sourced[:10]:
            typer.echo(f"  {u}")
    if report.suggestions:
        typer.echo("suggestions:")
        for s in report.suggestions:
            typer.echo(f"  {s}")

    if not report.gaps and not report.under_sourced and not report.suggestions:
        typer.echo("suggest: no suggestions (graph looks healthy)")


@agent_app.command()
def status() -> None:
    """Print a graph health dashboard."""
    p = resolve_paths()

    # --- Daemon status (PID, version) ---------------------------------------
    daemon_pid = _daemon_pid(p)
    version_file = p["home"] / ".daemon.version"
    daemon_version = None
    if version_file.exists():
        try:
            daemon_version = version_file.read_text().strip() or None
        except OSError:
            pass

    if daemon_pid:
        parts = [f"running (pid={daemon_pid}"]
        if daemon_version:
            parts.append(f", version={daemon_version}")
        cli_version = __version__
        if daemon_version and cli_version and daemon_version != cli_version:
            parts.append(f", CLI={cli_version} (restart needed)")
        elif not daemon_version and cli_version:
            parts.append(f", CLI={cli_version}")
        parts.append(")")
        typer.echo(f"daemon: {''.join(parts)}")
    else:
        typer.echo(f"daemon: stopped (CLI version={__version__})")

    facts_path = p["out"] / "facts.jsonl"
    if not facts_path.exists():
        typer.echo("status: no graph — run `lorekeep compile` first")
        raise typer.Exit(code=1)

    from lorekeep.store.graph import GraphStore
    from lorekeep.agent import agent_status
    store = GraphStore.from_jsonl(facts_path)
    dash = agent_status(store, p.get("pending"))

    typer.echo(f"nodes: {dash.node_count}")
    typer.echo(f"edges: {dash.edge_count}")
    typer.echo(f"namespaces: {dash.namespace_count} ({', '.join(dash.namespaces)})")
    typer.echo(f"lint issues: {dash.lint_issues}")
    typer.echo(f"pending journals: {dash.pending_journals}")


def _tilde(path: Path | None) -> str:
    """Render a path with ``~`` so a report row fits a terminal."""
    if path is None:
        return "—"
    home, text = str(Path.home()), str(path)
    return "~" + text[len(home):] if text.startswith(home) else text


def _is_wired(path: Path | None) -> bool:
    """Does this config/hook file already mention lorekeep?

    A substring probe, not a parse: agents use several file formats, and a
    report must not fail because one of them is mid-write.
    """
    if path is None or not path.is_file():
        return False
    try:
        return "lorekeep" in path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def _daemon_pid(p: dict) -> int | None:
    """PID of the live daemon, or ``None``. Read-only — signal 0 never kills."""
    try:
        pid = int((p["home"] / ".daemon.pid").read_text().strip())
    except (OSError, ValueError):
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    except OSError:
        pass                    # alive, just not ours to signal
    return pid


def _format_relative_time(ts: float) -> str:
    """Human-friendly '5 min ago' / '3 hours ago' / '2 days ago' for a mtime.

    Pure function — no I/O, no clock side effects beyond ``time.time()``.
    """
    import time as _time

    delta = _time.time() - ts
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{int(delta // 60)} min ago"
    if delta < 86400:
        return f"{int(delta // 3600)} hours ago"
    return f"{int(delta // 86400)} days ago"


def _last_session_imports(raw_dir: Path) -> list[dict]:
    """Newest imported session per ``*-session`` namespace, newest first.

    Scans ``raw/<ns>-session/*.md`` files; the newest mtime identifies the
    most recent import. Returns ``[]`` when no session namespace exists.
    """
    from datetime import datetime

    if not raw_dir.is_dir():
        return []
    results: list[dict] = []
    for ns_dir in sorted(raw_dir.glob("*-session")):
        if not ns_dir.is_dir():
            continue
        md_files = list(ns_dir.glob("*.md"))
        if not md_files:
            continue
        newest = max(md_files, key=lambda f: f.stat().st_mtime)
        mtime = newest.stat().st_mtime
        session_key = newest.stem.rsplit("-", 1)[0]
        results.append({
            "agent": ns_dir.name.removesuffix("-session"),
            "session": session_key,
            "ts": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M"),
            "mtime": mtime,
            "files": len(md_files),
        })
    results.sort(key=lambda r: r["mtime"], reverse=True)
    return results


def _agent_report(scope: str) -> list[dict]:
    """One row per known agent: installed, has data, wired, hooked, ingest paths."""
    from lorekeep.integrations.detect import (
        detect_installed_agents,
        resolve_agent_markers,
    )
    from lorekeep.integrations.registry import all_specs

    installed = set(detect_installed_agents())
    target = Path.cwd()
    rows = []
    for spec in all_specs():
        config_path = spec.config_path(target, scope)
        hook_path = spec.hook_path(target, scope)
        ingest = [n for n, src in (("memory", spec.memory), ("transcript", spec.session)) if src]
        rows.append({
            "name": spec.name,
            "label": spec.label,
            "installed": spec.name in installed,
            # Install markers and the paths importers actually read drift apart
            # (a stale ~/.cursor outlives an uninstall), so report them apart.
            "session_data": any(
                path.exists()
                for path in resolve_agent_markers(spec, spec.data_markers)
            ),
            "config": str(config_path),
            "wired": _is_wired(config_path),
            "hook": str(hook_path) if hook_path else None,
            "hooked": _is_wired(hook_path),
            "hook_trigger": spec.hook.trigger if spec.hook else None,
            "hook_event": spec.hook.event if spec.hook else None,
            "hook_timeout_seconds": (
                spec.hook.timeout_seconds if spec.hook else None
            ),
            "hook_surfaces": list(spec.hook.surfaces) if spec.hook else [],
            "ingest": ingest,
        })
    return rows


def _hook_label(row: dict) -> str:
    """Human-readable native event plus whether it is an approximation."""
    if not row.get("hook"):
        return "scope unsupported" if row.get("hook_event") else "n/a"
    if not row.get("hooked"):
        return "not wired"
    event = str(row.get("hook_event") or "hook")
    kind = "native" if row.get("hook_trigger") == "session_end" else "fallback"
    return f"{event} ({kind})"


@agent_app.command("detect")
def agent_detect(
    json_out: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Report which coding agents are installed, active, and wired.

    A report, not a check: exits 0 even when nothing is found. `doctor` is the
    command that fails.
    """
    from lorekeep.integrations.detect import detect_active_agent
    from lorekeep.integrations.registry import find

    p = resolve_paths()
    acfg = load_config(p["config"]).agents
    scope = _wire_scope(acfg)
    active = detect_active_agent()
    pid = _daemon_pid(p)
    rows = _agent_report(scope)

    if json_out:
        spec = find(active) if active else None
        typer.echo(json.dumps({
            "active": active,
            "active_env": next(
                (v for v in (spec.active_env if spec else ()) if os.environ.get(v)), None,
            ),
            "scope": scope,
            "enabled": acfg.enabled,
            "daemon": {"running": pid is not None, "pid": pid},
            "agents": rows,
        }, indent=2))
        return

    if active:
        spec = find(active)
        env = next((v for v in (spec.active_env if spec else ()) if os.environ.get(v)), None)
        typer.echo(f"active: {active}" + (f" ({env})" if env else ""))
    else:
        typer.echo("active: none (not running inside a coding agent)")
    typer.echo(f"daemon: running (PID {pid})" if pid else "daemon: not running")
    typer.echo("")

    header = ("agent", "installed", "session data", f"wired ({scope})", "hook", "ingest")
    yn = {True: "yes", False: "no"}
    table = [header] + [(
        r["name"], yn[r["installed"]], yn[r["session_data"]],
        _tilde(Path(r["config"])) if r["wired"] else "—",
        _hook_label(r),
        " + ".join(r["ingest"]) or "—",
    ) for r in rows]
    widths = [max(len(row[i]) for row in table) for i in range(len(header))]
    for row in table:
        typer.echo("  ".join(c.ljust(w) for c, w in zip(row, widths)).rstrip())

    # An installed, wired agent that config excludes contributes nothing. That
    # is exactly the silent surprise this command exists to make visible.
    muted = [r["name"] for r in rows if r["installed"] and r["name"] not in acfg.enabled]
    if muted:
        typer.echo(f"\nnote: {', '.join(muted)} installed but excluded by agents.enabled")
    if not any(r["installed"] for r in rows):
        typer.echo("\nno coding agents found — lorekeep has nothing to aggregate yet")


@agent_app.command("wire")
def agent_wire(
    agent: str = typer.Option(None, "--agent", help="Wire one agent (default: all detected)"),
    scope: str = typer.Option(None, "--scope", help="project | user (default: agents.wire_scope)"),
    read_ns: str = typer.Option(
        None, "--read-ns",
        help="Read namespace pattern override (default: namespaces.read)",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Report targets without writing"),
    force: bool = typer.Option(False, "--force", help="Include undetected agents"),
) -> None:
    """Write MCP config + lifecycle hooks for detected agents.

    Idempotent: a target already holding the right wiring reports ``unchanged``
    and keeps its mtime. That is what makes it safe for the daemon to call this
    every cycle, and it is why `--force` widens the roster rather than
    rewriting files.
    """
    from lorekeep.integrations.detect import detect_agents
    from lorekeep.integrations.registry import AGENT_NAMES, find

    p = resolve_paths()
    config = load_config(p["config"])
    scope = _validate_scope(scope) if scope else _wire_scope(config.agents)

    if agent:
        specs = [_resolve_agent_arg(agent)]          # explicit name beats both filters
    else:
        names = AGENT_NAMES if force else detect_agents()
        specs = [s for s in map(find, names) if s and s.name in config.agents.enabled]

    if not specs:
        typer.echo("no coding agents detected — install one, or pass --force")
        return

    target = Path.cwd()
    namespace = read_ns
    typer.echo(f"scope: {scope}   read namespace: {namespace or 'config default'}")
    failed = False

    for spec in specs:
        config_path = spec.config_path(target, scope)
        hook_path = spec.hook_path(target, scope)

        if dry_run:
            for label, path in (("config", config_path), ("hook", hook_path)):
                if path is None:
                    continue
                state = "already wired" if _is_wired(path) else "would write"
                typer.echo(f"{spec.name}: {label} -> {_tilde(path)} ({state})")
            if spec.hook is not None and hook_path is None:
                typer.echo(
                    f"{spec.name}: hook -> user scope required (would skip)"
                )
            continue

        try:
            written, hooked = _wire_one(spec, target, namespace, scope=scope)
        except Exception as exc:
            failed = True
            log.warning(
                "agent wiring failed agent=%s error_type=%s",
                spec.name, type(exc).__name__,
                extra={"event": "wire.failed"},
            )
            typer.echo(f"{spec.name}: failed ({type(exc).__name__}: {exc})")
            continue

        typer.echo(
            f"{spec.name}: wired -> {_tilde(written)}" if written
            else f"{spec.name}: unchanged -> {_tilde(config_path)}"
        )
        if hooked:
            typer.echo(f"{spec.name}: hooked -> {_tilde(hooked)}")
        elif hook_path:
            typer.echo(f"{spec.name}: hook unchanged -> {_tilde(hook_path)}")
        elif spec.hook is not None:
            typer.echo(f"{spec.name}: hook skipped -> user scope required")

    if failed:
        raise typer.Exit(code=1)


def _discover_watchable_sessions() -> list[tuple[str, Path, Path]]:
    """Find agent memory dirs that support zero-LLM quick import.

    Returns [(agent_name, session_dir, memory_dir), ...]. Only agents whose
    spec declares a memory source appear here — transcript-only agents are
    handled by lifecycle events and startup recovery.
    """
    from lorekeep.integrations.registry import all_specs

    sessions: list[tuple[str, Path, Path]] = []
    for spec in all_specs():
        if spec.memory is None:
            continue
        try:
            mem_dir = getattr(spec.importer(), spec.memory.dir_finder)()
            if mem_dir and any(mem_dir.glob("*.md")):
                sessions.append((spec.name, mem_dir.parent, mem_dir))
        except Exception as exc:
            log.warning(
                "session discovery failed agent=%s error_type=%s",
                spec.name, type(exc).__name__,
                extra={"event": "daemon.session_discovery_failed"},
            )

    return sessions


def _quick_import_session(agent: str, session_dir: Path, memory_dir: Path, raw_dir: Path) -> int:
    """Quick-import memory files for one agent. Returns file count."""
    from lorekeep.integrations.registry import find

    spec = find(agent)
    if spec is None or spec.memory is None or spec.memory_ns is None:
        return 0
    written = getattr(spec.importer(), spec.memory.import_fn)(
        raw_dir, namespace=spec.memory_ns, memory_dir=memory_dir,
    )
    return len(written)


def _discover_session_transcripts(cwd: Path | None = None) -> list[tuple[str, object]]:
    """Find each agent's latest local session for startup recovery.

    Returns [(agent_name, handle), ...]. The handle stays opaque here — whether
    it is a transcript path, a DB blob, or a session id is the agent's business,
    and the registry names the functions that understand it.
    """
    from lorekeep.integrations.registry import all_specs

    found: list[tuple[str, object]] = []
    for spec in all_specs():
        if spec.session is None:
            continue
        try:
            handle = getattr(spec.importer(), spec.session.locate)(cwd)
            if handle is not None:
                found.append((spec.name, handle))
        except Exception as exc:
            log.warning(
                "transcript discovery failed agent=%s error_type=%s",
                spec.name, type(exc).__name__,
                extra={"event": "daemon.transcript_discovery_failed"},
            )
    return found


def _dump_session_transcript(
    agent: str,
    handle: object,
    raw_dir: Path,
    acfg,
    *,
    namespace: str | None = None,
    dry_run: bool = False,
) -> int:
    """Dump one agent's conversation to raw/ markdown. Returns file count.

    Zero LLM cost — compile extracts from the markdown like any other doc.
    """
    from lorekeep.importer.session_dump import dump_session_turns, prune_sessions
    from lorekeep.integrations.registry import find

    spec = find(agent)
    if spec is None or spec.session is None or spec.session_ns is None:
        return 0

    importer = spec.importer()
    written = dump_session_turns(
        getattr(importer, spec.session.parse)(handle),
        raw_dir,
        namespace=namespace or spec.session_ns,
        session_key=getattr(importer, spec.session.key)(handle),
        max_chars=acfg.transcript_max_chars,
        max_batches=acfg.transcript_max_batches,
        dry_run=dry_run,
    )
    dest_ns = namespace or spec.session_ns
    if not dry_run and dest_ns.endswith("-session"):
        prune_sessions(
            raw_dir, dest_ns,
            retain=acfg.transcript_retain_sessions,
        )
    return len(written)


def _on_disk_version() -> str | None:
    """Read lorekeep's version from installed package metadata on disk.

    Called by the daemon loop to detect upgrades: the running process has
    ``__version__`` in memory, but ``importlib.metadata.version()`` reads
    from the dist-info dir. When ``uv tool upgrade`` installs a new version,
    the on-disk value changes while the process keeps the old code.
    """
    import importlib
    import importlib.metadata
    importlib.invalidate_caches()
    try:
        return importlib.metadata.version("lorekeep")
    except importlib.metadata.PackageNotFoundError:
        return None


@agent_app.command()
def watch(
    interval: int = typer.Option(
        60, "--interval",
        help="Polling interval in seconds",
    ),
    watch_sessions: bool = typer.Option(
        True, "--watch-sessions/--no-watch-sessions",
        help="Drain lifecycle events and recover missed local sessions",
    ),
) -> None:
    """Run the autonomous agent daemon: watch raw/, pending/, and agent sessions.

    Watches raw/ for new/changed markdown → auto-compile.
    Watches pending/ for new journal entries → auto-resolve.
    Watches Claude + Codex memory dirs → delta quick import → raw/.
    Drains exact/fallback lifecycle events → targeted transcript dump → raw/.

    `lorekeep init` installs this as a persistent OS service by default.
    Re-run `lorekeep agent service install` if the data home or command changed.
    """
    import signal
    import sys
    import time

    p = resolve_paths()
    raw_dir = p["raw"]
    pending_dir = p.get("pending")

    typer.echo(f"agent watch: monitoring raw={raw_dir}, pending={pending_dir}, interval={interval}s")
    typer.echo("agent: auto-compile (raw/) and auto-resolve (pending/) enabled")
    if watch_sessions:
        typer.echo("agent: lifecycle ingest enabled (events + startup recovery)")
    typer.echo("agent: MCP server lazy-reloads facts.jsonl — no reconnect needed")
    log.info(
        "daemon watch started interval=%s session_watch=%s",
        interval, watch_sessions, extra={"event": "daemon.start"},
    )

    pid_file = p["home"] / ".daemon.pid"
    version_file = p["home"] / ".daemon.version"
    if pid_file.exists():
        try:
            old_pid = int(pid_file.read_text().strip())
            os.kill(old_pid, 0)
            typer.echo(f"agent: daemon already running (PID {old_pid}), exiting")
            raise typer.Exit(code=1)
        except (ProcessLookupError, ValueError, PermissionError):
            pass
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()))
    startup_version = _on_disk_version()
    if startup_version:
        version_file.write_text(startup_version)

    # --- SIGTERM handler: clean PID + version files on kill / systemctl stop --
    def _on_sigterm(signum, frame):
        pid_file.unlink(missing_ok=True)
        version_file.unlink(missing_ok=True)
        log.info("daemon received SIGTERM — shutting down", extra={"event": "daemon.sigterm"})
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _on_sigterm)

    # --- Load agents config for auto-wire + transcript dump ------------------
    try:
        _acfg = load_config(p["config"]).agents
    except Exception:
        _acfg = None  # config may be missing in minimal homes
    wire_backoff: dict[str, float] = {}
    last_wire_check = 0.0
    first_pass = True

    last_raw_mtime = 0.0
    last_raw_count = -1
    last_pending_mtime = 0.0
    last_schema_mtime = 0.0
    manifest_path = p["out"] / "manifest.json"
    last_manifest_mtime = manifest_path.stat().st_mtime if manifest_path.exists() else 0.0
    session_state: dict[str, float] = {}
    session_import_time: dict[str, float] = {}

    # Sync from remote at startup (pull changes from other machines)
    _try_backup(p["home"], reason="startup",
                enabled=_acfg.auto_backup if _acfg else True)

    # Resolve/replay only after startup sync so remote journal events are visible.
    if pending_dir and pending_dir.exists():
        from lorekeep.journal import load_journals
        journals = load_journals(pending_dir)
        if any(j.status in {"pending", "merged", "flagged"} for j in journals):
            typer.echo("agent: replaying journals at startup...")
            _do_auto_resolve(
                p["out"], pending_dir, p.get("wiki"), p.get("schema"),
                replay_accepted=True,
            )

    while True:
        try:
            hook_ingested = False
            # --- auto-restart on upgrade ---------------------------------------
            # If lorekeep was upgraded (pip/uv) while this daemon is running,
            # the on-disk version changes but the process still runs old code.
            # Detect and hot-swap via os.execv (systemd/launchd won't notice).
            current_version = _on_disk_version()
            if startup_version is not None and current_version is not None and current_version != startup_version:
                log.info(
                    "lorekeep upgraded (%s → %s) — restarting daemon",
                    startup_version, current_version,
                    extra={"event": "daemon.upgrade_restart"},
                )
                typer.echo(f"agent: upgraded {startup_version} → {current_version}, restarting...")
                pid_file.unlink(missing_ok=True)
                os.execv(sys.argv[0], sys.argv)

            # --- auto-wire agents (root cause #4: re-detect every cycle) -----
            now_ts = time.monotonic()
            if _acfg and _acfg.auto_wire and (
                first_pass or now_ts - last_wire_check >= _acfg.wire_interval_seconds
            ):
                last_wire_check = now_ts
                try:
                    for name, path in _sync_agent_wiring(
                        scope=_acfg.wire_scope, ns=None,
                        enabled=_acfg.enabled, backoff=wire_backoff, now=now_ts,
                    ):
                        typer.echo(f"agent: wired {name} → {path}")
                        log.info("agent wired agent=%s", name, extra={"event": "daemon.agent_wired"})
                except Exception as exc:
                    log.warning(
                        "auto-wire failed error_type=%s", type(exc).__name__,
                        extra={"event": "daemon.auto_wire_failed"},
                    )

            # --- lifecycle events -> targeted transcript dump --------------
            # Hook processes only enqueue metadata. Drain before taking the
            # raw/ snapshot so a completed session can compile in this cycle.
            if watch_sessions and _acfg:
                try:
                    from lorekeep.hook_events import drain_hook_events

                    hook_report = drain_hook_events(
                        p["home"], raw_dir, _acfg,
                    )
                    hook_ingested = hook_report.written > 0
                    if hook_report.written:
                        typer.echo(
                            "agent: lifecycle ingest — "
                            f"{hook_report.written} file(s) → raw/"
                        )
                        log.info(
                            "hook events drained processed=%s written=%s "
                            "deferred=%s failed=%s",
                            hook_report.processed, hook_report.written,
                            hook_report.deferred, hook_report.failed,
                            extra={"event": "daemon.hook_events_drained"},
                        )
                except Exception as exc:
                    log.warning(
                        "hook event drain failed error_type=%s", type(exc).__name__,
                        extra={"event": "daemon.hook_event_drain_failed"},
                    )

            # Re-check existence each cycle (raw/ or pending/ may be created after start)
            has_raw = raw_dir.exists()
            has_pending = pending_dir and pending_dir.exists()
            # --- raw/ + schema watch → auto-compile ---------------------------
            raw_files = sorted(raw_dir.rglob("*.md")) if has_raw else []
            raw_mtime = max((f.stat().st_mtime for f in raw_files), default=0.0)
            raw_count = len(raw_files)
            schema_mtime = p["schema"].stat().st_mtime if p["schema"].exists() else 0.0
            compiled = False

            should_compile = False
            compile_reason = ""
            if hook_ingested:
                should_compile = True
                compile_reason = " (session ended)"
            # Sentinel from explicit `lorekeep compile` (background mode)
            sentinel = p["home"] / ".compile-requested"
            if sentinel.exists():
                should_compile = True
                compile_reason = " (compile requested)"
                sentinel.unlink(missing_ok=True)
            if last_raw_count >= 0:
                if raw_count != last_raw_count:
                    should_compile = True
                    compile_reason = f" ({raw_count} files)"
                elif raw_mtime > last_raw_mtime:
                    should_compile = True
                    compile_reason = " (raw/ mtime changed)"
            if last_schema_mtime > 0 and schema_mtime > last_schema_mtime:
                should_compile = True
                compile_reason = " (schema changed)"

            if should_compile:
                typer.echo(f"agent: compiling{compile_reason}...")
                try:
                    schema = load_schema(p["schema"])
                    config = load_config(p["config"])
                    provider = _make_provider(config)
                    with _progress_ctx(raw_dir, config.compile.chunk_lines) as handle:
                        dm = compile_graph(
                            raw_root=raw_dir, out_dir=p["out"], schema=schema,
                            provider=provider, cache_path=p["cache"],
                            chunk_lines=config.compile.chunk_lines,
                            on_progress=_progress_cb(handle),
                            personal_ns=config.namespaces.write,
                            language=config.compile.language,
                            prev_aliases=_load_prev_aliases(p["out"] / "facts.jsonl"),
                            prev_quarantine=_load_prev_quarantine(p["out"] / "facts.jsonl"),
                            max_workers=config.compile.max_workers,
                            flush_interval=config.compile.flush_interval,
                        )
                    _report_compile_errors(dm, exit_on_total_failure=False)
                    _report_content_quality(dm)
                    typer.echo(
                        f"agent: compiled {dm.node_count} nodes, {dm.edge_count} edges"
                    )
                    compiled = True
                except Exception as exc:
                    log.exception(
                        "daemon compile failed error_type=%s", type(exc).__name__,
                        extra={"event": "daemon.compile_failed"},
                    )
                    typer.echo(f"agent: compile error: {exc}")
            last_raw_mtime = raw_mtime
            last_raw_count = raw_count
            last_schema_mtime = schema_mtime

            # --- auto-backup + sync after compile ---------------------------
            if compiled:
                _try_backup(p["home"], reason="compile",
                            enabled=_acfg.auto_backup if _acfg else True,
                            provider=provider)
                resolved = False
                if has_pending:
                    resolved = _do_auto_resolve(
                        p["out"], pending_dir, p.get("wiki"), p.get("schema"),
                        replay_accepted=True,
                    )
                # --- auto self-heal (runs after compile/resolve, before wiki) ---
                healed = _do_self_heal(
                    p["out"], p.get("schema"),
                    enabled=_acfg.self_heal if _acfg else True,
                )
                if not resolved or healed:
                    _auto_generate_wiki(p["out"], p.get("wiki"), p.get("schema"))
                # Backup after resolve + heal (graph may have changed)
                if resolved or healed:
                    _try_backup(p["home"], reason="heal",
                                enabled=_acfg.auto_backup if _acfg else True)

            # --- pending/ watch → auto-resolve ------------------------------
            if has_pending:
                journal_files = sorted(pending_dir.rglob("journal.jsonl"))
                pending_mtime = max((f.stat().st_mtime for f in journal_files), default=0.0)
                if pending_mtime > last_pending_mtime and last_pending_mtime > 0:
                    typer.echo("agent: pending/ changed — resolving...")
                    resolved = _do_auto_resolve(
                        p["out"], pending_dir, p.get("wiki"), p.get("schema"),
                    )
                    if resolved:
                        _try_backup(p["home"], reason="resolve",
                                    enabled=_acfg.auto_backup if _acfg else True)
                last_pending_mtime = pending_mtime

            # --- manifest.json mtime → detect external compile → backup -----
            # When another process (CLI, serve, another daemon) writes
            # facts.jsonl + manifest.json, the daemon detects the mtime
            # change and backs up. The `not compiled` guard prevents
            # double-backup when the daemon compiled this cycle.
            current_manifest_mtime = (
                manifest_path.stat().st_mtime if manifest_path.exists() else 0.0
            )
            if (last_manifest_mtime > 0
                    and current_manifest_mtime > last_manifest_mtime
                    and not compiled):
                typer.echo("agent: graph updated externally — backing up...")
                log.info(
                    "external compile detected manifest_mtime=%s prev=%s",
                    current_manifest_mtime, last_manifest_mtime,
                    extra={"event": "daemon.external_compile_detected"},
                )
                _try_backup(p["home"], reason="external_compile",
                            enabled=_acfg.auto_backup if _acfg else True)
            last_manifest_mtime = current_manifest_mtime

            # --- session watch → delta quick import → raw/ ------------------
            # Re-discover every cycle (cheap — just directory scans).
            # Detects new sessions opened after daemon start.
            if watch_sessions:
                now = time.monotonic()
                sessions = _discover_watchable_sessions()
                if _acfg:
                    sessions = [
                        item for item in sessions
                        if item[0] in _acfg.enabled
                    ]
                for agent_name, session_dir, memory_dir in sessions:
                    mem_files = sorted(memory_dir.glob("*.md"))
                    mem_mtime = max((f.stat().st_mtime for f in mem_files), default=0.0)
                    prev = session_state.get(agent_name, 0.0)
                    last_import = session_import_time.get(agent_name, 0.0)

                    # Bug 1 fix: first-sight import (prev was 0.0 → blocked import forever).
                    first_sight = agent_name not in session_state
                    if (first_sight or mem_mtime > prev) and now - last_import >= 30:
                        typer.echo(f"agent: {agent_name} memory changed ({len(mem_files)} files) — importing...")
                        try:
                            count = _quick_import_session(agent_name, session_dir, memory_dir, raw_dir)
                            # Bug 2 fix: always advance import time (not just when count > 0).
                            session_import_time[agent_name] = now
                            # Bug 3 fix: only update state on success.
                            session_state[agent_name] = mem_mtime
                            if count:
                                typer.echo(f"agent: {agent_name} import done — {count} files → raw/{agent_name}-memory/")
                        except Exception as exc:
                            log.exception(
                                "session import failed agent=%s error_type=%s",
                                agent_name, type(exc).__name__,
                                extra={"event": "daemon.session_import_failed"},
                            )
                            typer.echo(f"agent: {agent_name} import error: {exc}")
                        continue  # state was set in the try block above
                    session_state[agent_name] = mem_mtime

                # One startup recovery pass covers sessions whose native hook
                # was missed while Lorekeep was stopped. Live polling is not a
                # primary ingest path: it would rewrite knowledge every turn.
                if first_pass and _acfg and _acfg.watch_transcripts:
                    try:
                        transcripts = _discover_session_transcripts()
                        for agent_name, handle in transcripts:
                            if agent_name not in _acfg.enabled:
                                continue
                            count = _dump_session_transcript(
                                agent_name, handle, raw_dir, _acfg,
                            )
                            if count:
                                typer.echo(
                                    f"agent: {agent_name} startup recovery — "
                                    f"{count} files → raw/{agent_name}-session/"
                                )
                    except Exception as exc:
                        log.warning(
                            "startup transcript recovery failed error_type=%s",
                            type(exc).__name__,
                            extra={"event": "daemon.transcript_recovery_failed"},
                        )

            first_pass = False
            time.sleep(interval)
        except KeyboardInterrupt:
            log.info("daemon watch stopped", extra={"event": "daemon.stop"})
            typer.echo("\nagent: shutting down")
            break
        except Exception as exc:
            log.exception(
                "daemon loop failed error_type=%s", type(exc).__name__,
                extra={"event": "daemon.loop_failed"},
            )
            typer.echo(f"agent: error: {exc}")
            time.sleep(interval)

    pid_file.unlink(missing_ok=True)


def _do_auto_resolve(
    out_dir: Path,
    pending_dir: Path,
    wiki_dir: Path | None = None,
    schema_path: Path | None = None,
    replay_accepted: bool = False,
) -> bool:
    from lorekeep.journal import resolve_lock

    with resolve_lock(pending_dir):
        return _do_auto_resolve_unlocked(
            out_dir,
            pending_dir,
            wiki_dir,
            schema_path,
            replay_accepted,
        )


def _do_auto_resolve_unlocked(
    out_dir: Path,
    pending_dir: Path,
    wiki_dir: Path | None = None,
    schema_path: Path | None = None,
    replay_accepted: bool = False,
) -> bool:
    """Merge pending journal entries into facts.jsonl.

    Extracted as a helper so both the pending/ watch loop and the
    post-compile re-merge path share the same logic.

    Returns True if facts.jsonl was rewritten (merge happened).
    """
    try:
        from lorekeep.facts_io import read_facts
        from lorekeep.compile.resolve import resolve as resolve_facts, merge_journals
        from lorekeep.compile.writer import write_graph
        from lorekeep.journal import load_journals, update_journal_status
        from lorekeep.models import Edge, Manifest, Node
        from lorekeep.pipeline import measure_content_quality

        facts_path = out_dir / "facts.jsonl"
        existing_nodes = []
        existing_edges = []
        if facts_path.exists():
            for f in read_facts(facts_path):
                if isinstance(f, Node):
                    existing_nodes.append(f)
                else:
                    existing_edges.append(f)

        journals = load_journals(pending_dir)
        candidate_entries = [
            j for j in journals
            if j.status == "pending"
            or (replay_accepted and j.status in {"merged", "flagged"})
        ]
        if candidate_entries:
            schema = load_schema(schema_path) if schema_path else None
            merged = merge_journals(
                existing_nodes,
                existing_edges,
                candidate_entries,
                replay_accepted=replay_accepted,
                schema=schema,
            )
            resolved = resolve_facts(merged.nodes, merged.edges, schema=schema)
            manifest = Manifest(
                schema_version=schema.version if schema else 0, chunk_count=0,
                node_count=len(resolved.nodes),
                edge_count=len(resolved.edges),
                run_id="auto-resolve", facts_hash="",
                compiled_at=now_iso(),
                merged_count=merged.merge_count,
                quarantined_count=merged.quarantine_count,
                flagged_count=merged.flagged_count,
                content_quality=(
                    measure_content_quality(resolved.nodes, resolved.edges, schema)
                    if schema else None
                ),
            )
            write_graph(out_dir, resolved.nodes, resolved.edges, manifest)

            for ns in set(entry.ns for entry, _ in merged.merged):
                entry_keys = {
                    e.entry_id or e.proposed_at
                    for e, _ in merged.merged if e.ns == ns
                }
                if entry_keys:
                    update_journal_status(pending_dir, ns, entry_keys, "merged")
            for ns in set(entry.ns for entry, _ in merged.flagged):
                entry_keys = {
                    e.entry_id or e.proposed_at
                    for e, _ in merged.flagged if e.ns == ns
                }
                existing = {
                    e.entry_id or e.proposed_at
                    for e, _ in merged.merged if e.ns == ns
                }
                to_flag = entry_keys - existing
                if to_flag:
                    update_journal_status(pending_dir, ns, to_flag, "flagged")

            typer.echo(f"agent: resolve done — {merged.merge_count} merged, "
                       f"{merged.flagged_count} flagged, {merged.quarantine_count} quarantined")
            log.info(
                "auto-resolve completed merged=%s flagged=%s quarantined=%s",
                merged.merge_count, merged.flagged_count, merged.quarantine_count,
                extra={"event": "resolve.complete"},
            )

            if wiki_dir:
                _auto_generate_wiki(out_dir, wiki_dir, schema_path)
            return True
    except Exception as exc:
        log.exception(
            "auto-resolve failed error_type=%s", type(exc).__name__,
            extra={"event": "resolve.failed"},
        )
        typer.echo(f"agent: resolve error: {exc}")
    return False


def _auto_generate_wiki(
    graph_dir: Path,
    wiki_dir: Path,
    schema_path: Path | None = None,
) -> None:
    """Regenerate wiki after compile or resolve. Best-effort, never blocks."""
    try:
        from lorekeep.wiki import generate_wiki
        schema = load_schema(schema_path) if schema_path and schema_path.exists() else None
        generate_wiki(graph_dir, wiki_dir, schema=schema)
        log.info("wiki generated", extra={"event": "wiki.complete"})
    except Exception as exc:
        log.warning(
            "wiki generation skipped error_type=%s", type(exc).__name__,
            extra={"event": "wiki.failed"},
        )
        typer.echo(f"wiki: auto-gen skipped: {exc}")


def _try_backup(
    home: Path,
    *,
    reason: str = "",
    enabled: bool = True,
    provider: object | None = None,
) -> bool:
    """Best-effort backup sync from the daemon loop. Never raises."""
    if not enabled:
        return False
    try:
        from lorekeep.backup import (
            _resolve_durable_conflicts,
            has_remote,
            sync_backup,
        )
        from lorekeep.config import load_config
        from lorekeep.paths import resolve_paths
        if not has_remote(home):
            return False
        cfg = load_config(resolve_paths()["config"])
        bcfg = cfg.backup
        durable_resolver = None
        if bcfg.auto_resolve_durable:
            prov = provider or _make_provider(cfg)
            durable_resolver = lambda h, paths: _resolve_durable_conflicts(
                h, paths, prov,
            )
        if sync_backup(
            home, auto_fix=True, branch=bcfg.branch,
            durable_resolver=durable_resolver,
        ):
            typer.echo(f"agent: backup synced ({reason})")
            log.info(
                "backup synced reason=%s", reason,
                extra={"event": "daemon.backup_synced"},
            )
            return True
        return False
    except Exception as exc:
        log.warning(
            "backup failed reason=%s error_type=%s", reason, type(exc).__name__,
            extra={"event": "daemon.backup_failed"},
        )
        return False


def _do_self_heal(
    out_dir: Path,
    schema_path: Path | None = None,
    *,
    enabled: bool = True,
) -> bool:
    """Run autonomous graph self-heal after compile/resolve.

    Removes dangling edges, merges duplicate nodes, deduplicates edges.
    Returns True if facts.jsonl was rewritten. Best-effort, never blocks.
    """
    if not enabled:
        return False
    try:
        facts_path = out_dir / "facts.jsonl"
        if not facts_path.exists():
            return False
        from lorekeep.store.graph import GraphStore
        from lorekeep.agent import self_heal
        from lorekeep.models import Manifest
        from lorekeep.compile.writer import write_graph

        schema = load_schema(schema_path) if schema_path and schema_path.exists() else None
        store = GraphStore.from_jsonl(facts_path)
        healed, report = self_heal(store, schema)
        if not report.changes_made:
            return False

        manifest = Manifest(
            schema_version=schema.version if schema else 0,
            chunk_count=0,
            node_count=len(healed.all_nodes()),
            edge_count=len(healed.all_edges()),
            run_id="auto-heal", facts_hash="",
            compiled_at=now_iso(),
        )
        write_graph(out_dir, healed.all_nodes(), healed.all_edges(), manifest)
        typer.echo(
            f"agent: self-heal — removed {len(report.edges_removed)} dangling, "
            f"deduped {len(report.edges_deduped)} edges"
        )
        log.info(
            "self-heal completed removed=%s deduped=%s flagged=%s",
            len(report.edges_removed),
            len(report.edges_deduped), len(report.flagged),
            extra={"event": "self_heal.complete"},
        )
        return True
    except Exception as exc:
        log.warning(
            "self-heal failed error_type=%s", type(exc).__name__,
            extra={"event": "self_heal.failed"},
        )
        return False


if __name__ == "__main__":
    app()
