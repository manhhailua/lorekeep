"""Lorekeep CLI."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import typer

from lorekeep import __version__
from lorekeep.compile.providers import LiteLLMProvider
from lorekeep.config import Config, load_config
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


@app.command(hidden=True)
def hook() -> None:
    """Session lifecycle hook: quick-import memories from all agents.

    Agent-agnostic — tries Claude and Codex memory imports (idempotent
    via manifest). Each is a no-op if nothing changed. Daemon picks up
    raw/ changes for compile.
    """
    p = resolve_paths()
    total = 0

    try:
        from lorekeep.importer.claude import find_current_session as find_claude
        from lorekeep.importer.claude import import_memories as import_claude_mem
        session_dir = find_claude()
        if session_dir and (session_dir / "memory").is_dir():
            written = import_claude_mem(session_dir, p["raw"], "claude-memory")
            total += len(written)
    except Exception:
        pass

    try:
        from lorekeep.importer.codex import import_memories as import_codex_mem
        written = import_codex_mem(p["raw"], "codex-memory")
        total += len(written)
    except Exception:
        pass

    if total:
        typer.echo(f"lorekeep: imported {total} memory file(s)")


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
        log.error("compile error %s:%s: %s", e.path, e.line, e.message)
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
def compile() -> None:
    """Compile raw/ → facts.jsonl + merge pending + generate wiki (all-in-one)."""
    from lorekeep.output import ok
    p = resolve_paths()
    schema = load_schema(p["schema"])
    config = load_config(p["config"])
    provider = _make_provider(config)

    with _progress_ctx(p["raw"], config.compile.chunk_lines) as handle:
        manifest = compile_graph(
            raw_root=p["raw"], out_dir=p["out"], schema=schema,
            provider=provider, cache_path=p["cache"], chunk_lines=config.compile.chunk_lines,
            on_progress=_progress_cb(handle),
        )

    ok(f"compiled: {manifest.node_count} nodes, {manifest.edge_count} edges, "
       f"run_id={manifest.run_id}, facts_hash={manifest.facts_hash}")

    _report_compile_errors(manifest)

    pending_dir = p.get("pending")
    resolved = False
    if pending_dir and pending_dir.exists():
        resolved = _do_auto_resolve(p["out"], pending_dir, p.get("wiki"))

    if not resolved:
        _auto_generate_wiki(p["out"], p["wiki"])


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
    result = generate_wiki(p["out"], p["wiki"])
    if "error" in result:
        error(f"wiki: {result['error']}")
        raise typer.Exit(code=1)
    ok(f"wiki: {result['pages']} pages written to {p['wiki']}")
    if open:
        _open_in_obsidian(p["wiki"])


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
        )
        typer.echo(f"eval-locomo: compiled {manifest.node_count} nodes, {manifest.edge_count} edges")

    raw_ns = os.environ.get("LOREKEEP_NS")
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


@app.command()
def check() -> None:
    """Validate the compiled graph: loads, no dangling edges."""
    p = resolve_paths()
    from lorekeep.eval.construction import structure_report
    struct = structure_report(p["out"])
    if struct["dangling_edge_rate"] > 0:
        from lorekeep.output import error
        error(f"check: FAIL — {struct['dangling_edge_rate']} dangling edges")
        raise typer.Exit(code=1)
    from lorekeep.output import ok
    ok(f"check: ok — {struct['node_count']} nodes, {struct['edge_count']} edges, 0 dangling")


@app.command()
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

    # Merge journals
    merged = merge_journals(existing_nodes, existing_edges, pending_entries)

    # Run standard resolve over merged facts
    resolved = resolve_facts(merged.nodes, merged.edges)

    # Build manifest
    manifest = Manifest(
        schema_version=0,
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
    )
    write_graph(p["out"], resolved.nodes, resolved.edges, manifest)

    # Update journal status per namespace
    ns_to_merged: dict[str, set[str]] = {}
    ns_to_flagged: dict[str, set[str]] = {}
    ns_to_quarantined: dict[str, set[str]] = {}
    for entry, _ in merged.merged:
        ns_to_merged.setdefault(entry.ns, set()).add(entry.proposed_at)
    for entry, _ in merged.flagged:
        ns_to_flagged.setdefault(entry.ns, set()).add(entry.proposed_at)
    for entry, _ in merged.quarantined:
        ns_to_quarantined.setdefault(entry.ns, set()).add(entry.proposed_at)

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
        _auto_generate_wiki(p["out"], p["wiki"])


@app.command()
def serve(
    transport: str = typer.Option("stdio", "--transport", help="stdio (default) | http"),
) -> None:
    """Serve the scoped graph over MCP."""
    p = resolve_paths()
    raw_ns = os.environ.get("LOREKEEP_NS")
    if raw_ns:
        allowed = [x.strip() for x in raw_ns.split(",") if x.strip()]
    else:
        allowed = load_config(p["config"]).ns.default
    from lorekeep.mcp_server import configure, mcp
    configure(graph_dir=p["out"], allowed_ns=allowed, schema_path=p["schema"], pending_dir=p.get("pending"))
    mcp.run(transport=transport)


mcp_app = typer.Typer(help="Coding-agent integration.")
app.add_typer(mcp_app, name="mcp")

config_app = typer.Typer(help="View and edit lorekeep config.")
app.add_typer(config_app, name="config")


@config_app.command("show")
def config_show() -> None:
    """Print the current config.yaml."""
    p = resolve_paths()
    if not p["config"].exists():
        typer.echo("No config.yaml found — run `lorekeep init` first.")
        raise typer.Exit(code=1)
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

    data = yaml.safe_load(p["config"].read_text(encoding="utf-8")) or {}

    keys = key.split(".")
    target = data
    for k in keys[:-1]:
        target = target.setdefault(k, {})

    final_key = keys[-1]
    if isinstance(target.get(final_key), list):
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

    p["config"].write_text(
        yaml.dump(data, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
    typer.echo(f"  {key} = {value}")


@mcp_app.command("add")
def mcp_add(
    agent: str = typer.Option(..., "--agent", help="claude | cursor | codex | opencode"),
    scope: str = typer.Option("project", "--scope", help="project | user"),
    ns: str = typer.Option(None, "--ns", help="namespace to scope the agent to"),
) -> None:
    """Write the agent's MCP config + print an agent-memory snippet."""
    from lorekeep.integrations.common import agent_memory_snippet, resolve_command

    p = resolve_paths()
    config = load_config(p["config"])
    command, args = resolve_command(config.install_source)
    hook_cmd, hook_args = resolve_command(config.install_source, ["hook"])

    target = Path.cwd() if scope == "project" else Path.home()
    writers = _agent_writers()
    if agent not in writers:
        typer.echo(f"unknown agent: {agent} (choose claude|cursor|codex|opencode)")
        raise typer.Exit(code=1)
    written = writers[agent].write_config(target, command, args, ns)
    typer.echo(f"wrote {agent} config -> {written}")
    if hasattr(writers[agent], "write_hook"):
        hook_path = writers[agent].write_hook(target, hook_cmd, hook_args)
        typer.echo(f"wrote session-end hook -> {hook_path}")
    typer.echo("\n" + agent_memory_snippet())


@app.command()
def doctor() -> None:
    """Verify install: graph loads, schema valid, ns resolves, a tool responds,
    and the configured LLM provider is reachable."""
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

    raw_ns = os.environ.get("LOREKEEP_NS")
    allowed = [x.strip() for x in raw_ns.split(",")] if raw_ns else config.ns.default

    try:
        from lorekeep.mcp_server import configure, list_namespaces
        configure(graph_dir=p["out"], allowed_ns=allowed, schema_path=p["schema"], pending_dir=p.get("pending"))
        ns = list_namespaces()
    except Exception as exc:
        problems.append(f"mcp configure/tool failed: {exc}")
        ns = []

    # Hint: api_base is redundant for native providers — litellm already knows
    # their endpoint. Surfaced as a non-fatal note (a user may intentionally
    # point a native provider at a mirror/proxy).
    if config.provider.api_base:
        prefix = model_provider(config.provider.model)
        if prefix in NATIVE_PROVIDERS:
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
    for note in notes:
        _info(note)


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
        help="Start the daemon (agent watch) in background after setup",
    ),
) -> None:
    """Bootstrap the data home, wire agents, import sessions, compile, and start daemon."""
    p = resolve_paths()
    created = []
    p["config"].parent.mkdir(parents=True, exist_ok=True)
    config_existed = p["config"].exists()
    ns = "public"
    name = ""
    bio = ""

    if not config_existed:
        if not yes and _is_interactive():
            ns, name, bio = _interactive_init(p)
        else:
            p["config"].write_text(DEFAULT_CONFIG_YAML)
        created.append(str(p["config"]))
    elif p["config"].exists():
        try:
            ns = load_config(p["config"]).ns.default[0]
        except Exception:
            pass

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
        ns_dir = p["raw"] / ns
        ns_dir.mkdir(parents=True, exist_ok=True)
        about_path = ns_dir / "about.md"
        if not about_path.exists():
            about_md = (
                f"# {name or '(your name)'}\n\n"
                f"{bio or '(your bio — a one-line intro about you)'}\n"
            )
            about_path.write_text(about_md)
            typer.echo(f"  wrote: {about_path}")

    # --- One-click chain: wire → import → compile → daemon -----------------
    if not config_existed:
        _auto_wire_agents(p, ns)

        config = load_config(p["config"])
        if _has_provider(config):
            typer.echo("\n  Compiling your docs into the knowledge graph...")
        _auto_import_and_compile(p)

        # Show graph/wiki status after compile
        facts_path = p["out"] / "facts.jsonl"
        wiki_path = p["wiki"] / "index.md"
        if facts_path.exists():
            from lorekeep.store.graph import GraphStore
            store = GraphStore.from_jsonl(facts_path)
            typer.echo(
                f"  graph: {len(store.all_nodes())} nodes, {len(store.all_edges())} edges"
            )
            if wiki_path.exists():
                typer.echo(f"  wiki: {p['wiki']} (open in Obsidian to browse)")
        else:
            typer.echo(
                "  graph: empty — add docs under raw/ then run `lorekeep compile`"
            )

        typer.echo("\nRestart your agent → lorekeep tools are available.")

    # Daemon: start on fresh init or revive if dead (regardless of config_existed)
    if watch and _is_interactive():
        _start_daemon(p)
    elif watch and not _is_interactive():
        typer.echo("\n  (skipped daemon start in non-interactive mode — run `lorekeep agent watch` manually)")
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
    """Walk the user through provider, model, API key, namespace, name, and bio.

    Returns ``(ns, name, bio)`` — the namespace plus the user's profile answers,
    so the caller can write the first file ``raw/<ns>/about.md``.
    """
    import yaml
    from lorekeep.providers import (
        list_models, search_providers,
        format_cost, is_dynamic, POPULAR,
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
        typer.echo(f"  {i}. {prov}")
    typer.echo(f"  {len(POPULAR) + 1}. [Search all providers]")
    typer.echo(f"  {len(POPULAR) + 2}. [Skip — configure later]")

    choice = typer.prompt("\nChoice", default="1")

    idx = int(choice) if choice.isdigit() else 0
    if idx == len(POPULAR) + 2 or choice.lower() == "skip":
        typer.echo("  → Skipped (edit config.yaml to add a provider later)\n")
        ns = typer.prompt("Default namespace", default="private")
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
                typer.echo(f"  {i}. {prov} ({count} models)")
            sub = typer.prompt("Choice", default="1")
            sub_idx = int(sub) if sub.isdigit() else 1
            provider_name = results[min(sub_idx - 1, len(results) - 1)][0]
    elif 1 <= idx <= len(POPULAR):
        provider_name = POPULAR[idx - 1]
    else:
        provider_name = "openai"

    typer.echo(f"  → {provider_name}\n")

    # ── Model selection ────────────────────────────────────────────────
    typer.echo(f"Select a model for {provider_name} (used for knowledge extraction):\n")
    if is_dynamic(provider_name):
        model = typer.prompt(
            f"Model name (free-text for {provider_name})",
            default="llama3.2" if provider_name == "ollama" else "",
        )
        api_base = typer.prompt(
            "API base URL", default="http://localhost:11434" if provider_name == "ollama" else "",
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

    # Prefix a bare model name with the explicitly-selected provider so the
    # written config is always a valid litellm string. (Not a guess — the user
    # picked this provider; only bare names get prefixed.)
    if "/" not in model:
        from lorekeep.providers import _normalize_model_name
        model = _normalize_model_name(model, provider_name)

    typer.echo(f"  → {model}\n")

    # ── API key (skip for local providers) ─────────────────────────────
    env_var = None
    if is_dynamic(provider_name):
        typer.echo("  → No API key needed for local provider.\n")
        api_key = None
    else:
        api_key = typer.prompt(
            "API key (saved into the gitignored config.yaml)",
            default="",
            hide_input=True,
        ) or None
        if api_key:
            typer.echo("  → key stored in config.yaml\n")
        else:
            env_var = typer.prompt(
                "API key env var name (or skip)",
                default=f"{provider_name.upper().replace('-', '_')}_API_KEY",
            )
            if env_var.lower() not in ("skip", ""):
                typer.echo(f"  → set {env_var} before compiling\n")
            else:
                env_var = None
                typer.echo("  → skipped (add key to config.yaml later)\n")

    # ── Namespace + profile ────────────────────────────────────────────
    ns = typer.prompt("Default namespace", default="private")
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
        },
        "compile": {"chunk_lines": 60},
        "ns": {"default": [ns]},
        "install_source": install_source,
    }
    p["config"].write_text(yaml.dump(config, default_flow_style=False, sort_keys=False))


def _auto_wire_agents(p: dict, ns: str) -> None:
    """Detect coding agents and write their MCP configs automatically.

    If running inside a coding agent (env var set), wires only that agent.
    Otherwise scans the filesystem for all installed agents and wires each.
    """
    from lorekeep.integrations.detect import detect_agents
    from lorekeep.integrations.common import agent_memory_snippet, resolve_command

    detected = detect_agents()
    if not detected:
        typer.echo("\n  No coding agents detected — run `lorekeep mcp add --agent <name>` after install.")
        return

    config = load_config(p["config"])
    command, args = resolve_command(config.install_source)
    hook_cmd, hook_args = resolve_command(config.install_source, ["hook"])

    writers = _agent_writers()
    target = Path.cwd()

    typer.echo(f"\n  Detected agents: {', '.join(detected)}")
    for agent_name in detected:
        writer = writers.get(agent_name)
        if not writer:
            continue
        try:
            written = writer.write_config(target, command, args, ns)
            typer.echo(f"  wired {agent_name} -> {written}")
            if hasattr(writer, "write_hook"):
                hook_path = writer.write_hook(target, hook_cmd, hook_args)
                typer.echo(f"  hooked {agent_name} session-end -> {hook_path}")
        except Exception as exc:
            typer.echo(f"  {agent_name}: failed ({exc})")

    typer.echo("\n  " + agent_memory_snippet().replace("\n", "\n  ").strip())


def _agent_writers() -> dict:
    """Return the agent-name → writer-module mapping (lazy import)."""
    from lorekeep.integrations import claude_code, codex, cursor, opencode
    return {
        "claude": claude_code,
        "cursor": cursor,
        "codex": codex,
        "opencode": opencode,
    }


def _auto_import_and_compile(p: dict) -> None:
    """Quick-import Claude memory files, then compile if provider is available."""
    imported = 0

    # --- Quick import: Claude memory files (zero LLM cost) ----------------
    try:
        from lorekeep.importer.claude import find_current_session, import_memories
        session_dir = find_current_session()
        if session_dir is not None and (session_dir / "memory").is_dir():
            written = import_memories(session_dir, p["raw"], "claude-memory")
            imported = len(written)
            if imported:
                typer.echo(f"  imported {imported} memory file(s) from Claude session")
    except Exception as exc:
        if os.environ.get("LOREKEEP_DEBUG"):
            typer.echo(f"  import error: {exc}")

    # --- Compile (if provider is usable) ----------------------------------
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
            )
        _report_compile_errors(manifest, exit_on_total_failure=False)
        pending_dir = p.get("pending")
        resolved = False
        if pending_dir and pending_dir.exists():
            resolved = _do_auto_resolve(p["out"], pending_dir, p.get("wiki"))
        if not resolved:
            _auto_generate_wiki(p["out"], p["wiki"])
        typer.echo(f"  compiled: {manifest.node_count} nodes, {manifest.edge_count} edges")
    except Exception as exc:
        typer.echo(f"  compile skipped: {exc}")


def _start_daemon(p: dict) -> None:
    """Start agent watch as a background process with PID + log files."""
    import subprocess
    import sys

    pid_path = p["home"] / "agent.pid"
    log_path = p["home"] / "agent.log"

    # Check if already running
    if pid_path.exists():
        old_pid = pid_path.read_text().strip()
        try:
            os.kill(int(old_pid), 0)
            typer.echo(f"  daemon already running (pid={old_pid})")
            return
        except (ProcessLookupError, ValueError):
            pass

    cmd = [sys.executable, "-m", "lorekeep.cli", "agent", "watch", "--interval", "60"]
    log_file = open(log_path, "a")
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
    typer.echo(f"  daemon started (pid={proc.pid}, log={log_path})")


@app.command()
def backup(
    init_remote: str = typer.Option(
        None, "--init", help="remote URL; sets up the backup repo + initial push"
    ),
) -> None:
    """Commit + push the data home to your private backup repo."""
    from lorekeep.backup import BackupError, backup as backup_home, init_backup
    from lorekeep.output import dim, error, info, ok

    home = resolve_paths()["home"]
    try:
        if init_remote:
            init_backup(home, init_remote)
            info(f"backup: repo ready at {home} -> {init_remote}")
        else:
            pushed = backup_home(home)
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
        help="Source to import from (claude | cursor)",
    ),
    quick: bool = typer.Option(
        False, "--quick",
        help="Quick mode: only import memory files, no LLM transcript analysis",
    ),
    session_path: str | None = typer.Option(
        None, "--session-path",
        help="Path to Claude session dir (auto-detect if omitted)",
    ),
    memory_ns: str = typer.Option(
        "claude-memory", "--memory-ns",
        help="Namespace for imported memory files",
    ),
    session_ns: str | None = typer.Option(
        None, "--session-ns",
        help="Namespace for imported session files (default: claude-session | cursor-session)",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would be imported without writing files",
    ),
) -> None:
    """Import knowledge from an agent's sessions into raw/.

    Sources:
      claude    Claude Code sessions. --quick copies memory/*.md only (no LLM);
                default (deep) adds LLM-summarized transcript analysis.
      cursor    Cursor composer conversations (GLOBAL state.vscdb). Deep-only.
      codex     Codex CLI rollout transcripts ($CODEX_HOME/sessions/).
                --quick copies memories/*.md only; default (deep) summarizes.
      opencode  opencode sessions (SQLite DB). Deep-only — no memory dir.
    """
    from lorekeep.output import ok
    if from_source not in ("claude", "cursor", "codex", "opencode"):
        typer.echo(f"unknown source: {from_source} (claude | cursor | codex | opencode)")
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


# --- Agent subcommand group -----------------------------------------------

agent_app = typer.Typer(help="Autonomous agent operations: ingest, lint, suggest, status, watch, daemon.")
app.add_typer(agent_app, name="agent")

daemon_app = typer.Typer(help="Install/uninstall daemon as persistent OS service.")
agent_app.add_typer(daemon_app, name="daemon")


@daemon_app.command("install")
def daemon_install() -> None:
    """Install daemon as a persistent OS service (survives restart).

    Linux: systemd user service. macOS: launchd LaunchAgent. Windows: Startup folder.
    """
    from lorekeep.daemon_service import install as svc_install
    p = resolve_paths()
    try:
        platform_name, config_path = svc_install(p["home"])
        typer.echo(f"daemon: installed as {platform_name} service → {config_path}")
        typer.echo(f"daemon: will auto-start on login/restart")
    except RuntimeError as exc:
        typer.echo(f"daemon: {exc}")
        raise typer.Exit(code=1)


@daemon_app.command("uninstall")
def daemon_uninstall() -> None:
    """Remove the persistent daemon service."""
    from lorekeep.daemon_service import uninstall as svc_uninstall
    removed = svc_uninstall()
    if removed:
        typer.echo("daemon: service removed")
    else:
        typer.echo("daemon: no service found")


@daemon_app.command("status")
def daemon_status() -> None:
    """Check if the persistent daemon service is installed and running."""
    from lorekeep.daemon_service import status as svc_status
    typer.echo(svc_status())


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

    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    entry_count = 0

    for n in approved_nodes:
        n["src"] = list(n.get("src", []))
        if result.source_path not in n["src"]:
            n["src"].append(result.source_path)
        entry = JournalEntry(
            fact=n,
            agent="cli-ingest",
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
    """Run semantic health checks on the graph."""
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
        typer.echo("auto-fix: not yet implemented (planned)")

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


def _discover_watchable_sessions() -> list[tuple[str, Path, Path]]:
    """Find agent session memory dirs that support quick import.

    Returns [(agent_name, session_dir, memory_dir), ...].
    Only Claude + Codex have memory dirs for zero-LLM quick import.
    Cursor/opencode are deep-only — handled by session-end hooks.
    """
    sessions: list[tuple[str, Path, Path]] = []

    try:
        from lorekeep.importer.claude import find_current_session as find_claude
        sd = find_claude()
        if sd and (sd / "memory").is_dir() and any((sd / "memory").glob("*.md")):
            sessions.append(("claude", sd, sd / "memory"))
    except Exception:
        pass

    try:
        from lorekeep.importer.codex import _codex_home
        mem_dir = _codex_home() / "memories"
        if mem_dir.is_dir() and any(mem_dir.glob("*.md")):
            sessions.append(("codex", mem_dir.parent, mem_dir))
    except Exception:
        pass

    return sessions


def _quick_import_session(agent: str, session_dir: Path, memory_dir: Path, raw_dir: Path) -> int:
    """Quick-import memory files for one agent. Returns file count."""
    if agent == "claude":
        from lorekeep.importer.claude import import_memories
        written = import_memories(session_dir, raw_dir, "claude-memory")
        return len(written)
    if agent == "codex":
        from lorekeep.importer.codex import import_memories
        written = import_memories(raw_dir, "codex-memory")
        return len(written)
    return 0


@agent_app.command()
def watch(
    interval: int = typer.Option(
        60, "--interval",
        help="Polling interval in seconds",
    ),
    watch_sessions: bool = typer.Option(
        True, "--watch-sessions/--no-watch-sessions",
        help="Watch agent session dirs for live continuous ingest",
    ),
) -> None:
    """Run the autonomous agent daemon: watch raw/, pending/, and agent sessions.

    Watches raw/ for new/changed markdown → auto-compile.
    Watches pending/ for new journal entries → auto-resolve.
    Watches Claude + Codex memory dirs → delta quick import → raw/.
    Cursor/opencode are handled by session-end hooks (`lorekeep hook`).
    """
    import time
    p = resolve_paths()
    raw_dir = p["raw"]
    pending_dir = p.get("pending")

    typer.echo(f"agent watch: monitoring raw={raw_dir}, pending={pending_dir}, interval={interval}s")
    typer.echo("agent: auto-compile (raw/) and auto-resolve (pending/) enabled")
    if watch_sessions:
        typer.echo("agent: session watch enabled (Claude + Codex memory dirs)")
    typer.echo("agent: MCP server lazy-reloads facts.jsonl — no reconnect needed")

    pid_file = p["home"] / ".daemon.pid"
    if pid_file.exists():
        try:
            old_pid = int(pid_file.read_text().strip())
            import os as _os
            _os.kill(old_pid, 0)
            typer.echo(f"agent: daemon already running (PID {old_pid}), exiting")
            raise typer.Exit(code=1)
        except (ProcessLookupError, ValueError, PermissionError):
            pass
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()))

    last_raw_mtime = 0.0
    last_raw_count = -1
    last_pending_mtime = 0.0
    session_state: dict[str, float] = {}
    session_import_time: dict[str, float] = {}

    # One-time resolve of pending journals present at startup
    if pending_dir and pending_dir.exists():
        from lorekeep.journal import load_journals
        journals = load_journals(pending_dir)
        if any(j.status == "pending" for j in journals):
            typer.echo("agent: resolving pending journals at startup...")
            _do_auto_resolve(p["out"], pending_dir, p.get("wiki"))

    # Sync from remote at startup (pull changes from other machines)
    try:
        from lorekeep.backup import sync_backup, has_remote
        if has_remote(p["home"]):
            typer.echo("agent: syncing backup from remote...")
            sync_backup(p["home"])
    except Exception:
        pass

    while True:
        try:
            # Re-check existence each cycle (raw/ or pending/ may be created after start)
            has_raw = raw_dir.exists()
            has_pending = pending_dir and pending_dir.exists()
            # --- raw/ watch → auto-compile ----------------------------------
            raw_files = sorted(raw_dir.rglob("*.md")) if has_raw else []
            raw_mtime = max((f.stat().st_mtime for f in raw_files), default=0.0)
            raw_count = len(raw_files)
            compiled = False

            should_compile = False
            if last_raw_count >= 0:
                if raw_count != last_raw_count:
                    should_compile = True
                elif raw_mtime > last_raw_mtime:
                    should_compile = True

            if should_compile:
                typer.echo(f"agent: raw/ changed ({raw_count} files) — compiling...")
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
                        )
                    _report_compile_errors(dm, exit_on_total_failure=False)
                    typer.echo("agent: compile done")
                    compiled = True
                except Exception as exc:
                    typer.echo(f"agent: compile error: {exc}")
            last_raw_mtime = raw_mtime
            last_raw_count = raw_count

            if compiled and has_pending:
                _do_auto_resolve(p["out"], pending_dir, p.get("wiki"))

            # --- auto-backup + sync after compile ---------------------------
            if compiled:
                try:
                    from lorekeep.backup import sync_backup
                    if sync_backup(p["home"]):
                        typer.echo("agent: backup synced")
                except Exception:
                    pass

            # --- pending/ watch → auto-resolve ------------------------------
            if has_pending:
                journal_files = sorted(pending_dir.rglob("journal.jsonl"))
                pending_mtime = max((f.stat().st_mtime for f in journal_files), default=0.0)
                if pending_mtime > last_pending_mtime and last_pending_mtime > 0:
                    typer.echo("agent: pending/ changed — resolving...")
                    _do_auto_resolve(p["out"], pending_dir, p.get("wiki"))
                last_pending_mtime = pending_mtime

            # --- session watch → delta quick import → raw/ ------------------
            # Re-discover every cycle (cheap — just directory scans).
            # Detects new sessions opened after daemon start.
            if watch_sessions:
                now = time.monotonic()
                sessions = _discover_watchable_sessions()
                for agent_name, session_dir, memory_dir in sessions:
                    mem_files = sorted(memory_dir.glob("*.md"))
                    mem_mtime = max((f.stat().st_mtime for f in mem_files), default=0.0)
                    prev = session_state.get(agent_name, 0.0)
                    last_import = session_import_time.get(agent_name, 0.0)

                    if (mem_mtime > prev and prev > 0
                            and now - last_import >= 30):
                        typer.echo(f"agent: {agent_name} memory changed ({len(mem_files)} files) — importing...")
                        try:
                            count = _quick_import_session(agent_name, session_dir, memory_dir, raw_dir)
                            if count:
                                typer.echo(f"agent: {agent_name} import done — {count} files → raw/{agent_name}-memory/")
                                session_import_time[agent_name] = now
                        except Exception as exc:
                            typer.echo(f"agent: {agent_name} import error: {exc}")
                    session_state[agent_name] = mem_mtime

            time.sleep(interval)
        except KeyboardInterrupt:
            typer.echo("\nagent: shutting down")
            break
        except Exception as exc:
            typer.echo(f"agent: error: {exc}")
            time.sleep(interval)

    pid_file.unlink(missing_ok=True)


def _do_auto_resolve(out_dir: Path, pending_dir: Path, wiki_dir: Path | None = None) -> bool:
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
        pending_entries = [j for j in journals if j.status == "pending"]
        if pending_entries:
            merged = merge_journals(existing_nodes, existing_edges, pending_entries)
            resolved = resolve_facts(merged.nodes, merged.edges)
            manifest = Manifest(
                schema_version=0, chunk_count=0,
                node_count=len(resolved.nodes),
                edge_count=len(resolved.edges),
                run_id="auto-resolve", facts_hash="",
                compiled_at=now_iso(),
                merged_count=merged.merge_count,
                quarantined_count=merged.quarantine_count,
                flagged_count=merged.flagged_count,
            )
            write_graph(out_dir, resolved.nodes, resolved.edges, manifest)

            for ns in set(entry.ns for entry, _ in merged.merged):
                timestamps = {e.proposed_at for e, _ in merged.merged if e.ns == ns}
                if timestamps:
                    update_journal_status(pending_dir, ns, timestamps, "merged")
            for ns in set(entry.ns for entry, _ in merged.flagged):
                timestamps = {e.proposed_at for e, _ in merged.flagged if e.ns == ns}
                existing = {e.proposed_at for e, _ in merged.merged if e.ns == ns}
                to_flag = timestamps - existing
                if to_flag:
                    update_journal_status(pending_dir, ns, to_flag, "flagged")

            typer.echo(f"agent: resolve done — {merged.merge_count} merged, "
                       f"{merged.flagged_count} flagged, {merged.quarantine_count} quarantined")

            if wiki_dir:
                _auto_generate_wiki(out_dir, wiki_dir)
            return True
    except Exception as exc:
        typer.echo(f"agent: resolve error: {exc}")
    return False


def _auto_generate_wiki(graph_dir: Path, wiki_dir: Path) -> None:
    """Regenerate wiki after compile or resolve. Best-effort, never blocks."""
    try:
        from lorekeep.wiki import generate_wiki
        generate_wiki(graph_dir, wiki_dir)
    except Exception as exc:
        typer.echo(f"wiki: auto-gen skipped: {exc}")


if __name__ == "__main__":
    app()
