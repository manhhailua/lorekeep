"""Lorekeep CLI."""
from __future__ import annotations

import json
import os
from pathlib import Path

import typer

from lorekeep import __version__
from lorekeep.compile.providers import FakeProvider, LiteLLMProvider
from lorekeep.config import Config, load_config
from lorekeep.pipeline import compile_graph
from lorekeep.paths import resolve_paths
from lorekeep.defaults import DEFAULT_CONFIG_YAML, DEFAULT_SCHEMA
from lorekeep.schema_io import load_schema

app = typer.Typer(help="Lorekeep — compile team docs into a temporal knowledge graph.")


# Empty callback forces multi-command mode so subcommands are not auto-promoted.
@app.callback()
def _main() -> None:
    """Lorekeep — compile team docs into a temporal knowledge graph."""


def _build_provider(config: Config) -> LiteLLMProvider:
    """Create a real LLM provider from config.  Shared by compile + import."""
    api_key = None
    if config.provider.api_key_env:
        api_key = os.environ.get(config.provider.api_key_env)
    if not api_key:
        api_key = config.provider.api_key
    if api_key is config.provider.api_key and api_key:
        typer.echo(
            "warning: using inline api_key from config.yaml — prefer api_key_env "
            "(env var). config.yaml is gitignored but env is safer."
        )
    return LiteLLMProvider(
        model=config.provider.model,
        api_base=config.provider.api_base,
        temperature=config.provider.temperature,
        api_key=api_key,
    )


@app.command()
def version() -> None:
    """Print the Lorekeep version."""
    typer.echo(f"lorekeep {__version__}")


@app.command()
def compile() -> None:
    """Compile raw/ into graph/facts.jsonl."""
    p = resolve_paths()
    schema = load_schema(p["schema"])
    config = load_config(p["config"])

    if os.environ.get("LOREKEEP_PROVIDER") == "fake":
        canned = json.dumps({
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
        provider = FakeProvider(responses=[canned])
    else:
        provider = _build_provider(config)

    manifest = compile_graph(
        raw_root=p["raw"], out_dir=p["out"], schema=schema,
        provider=provider, cache_path=p["cache"], chunk_lines=config.compile.chunk_lines,
    )
    typer.echo(f"compiled: {manifest.node_count} nodes, {manifest.edge_count} edges, "
               f"run_id={manifest.run_id}, facts_hash={manifest.facts_hash}")


@app.command(name="eval")
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


@app.command()
def check() -> None:
    """Validate the compiled graph: loads, no dangling edges."""
    p = resolve_paths()
    from lorekeep.eval.construction import structure_report
    struct = structure_report(p["out"])
    if struct["dangling_edge_rate"] > 0:
        typer.echo(f"check: FAIL — {struct['dangling_edge_rate']} dangling edges")
        raise typer.Exit(code=1)
    typer.echo(f"check: ok — {struct['node_count']} nodes, {struct['edge_count']} edges, 0 dangling")


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
    configure(graph_dir=p["out"], allowed_ns=allowed, schema_path=p["schema"])
    mcp.run(transport=transport)


mcp_app = typer.Typer(help="Coding-agent integration.")
app.add_typer(mcp_app, name="mcp")


@mcp_app.command("add")
def mcp_add(
    agent: str = typer.Option(..., "--agent", help="claude | cursor | codex"),
    scope: str = typer.Option("project", "--scope", help="project | user"),
    ns: str = typer.Option(None, "--ns", help="namespace to scope the agent to"),
) -> None:
    """Write the agent's MCP config + print an agent-memory snippet."""
    from lorekeep.integrations import claude_code, codex, cursor
    from lorekeep.integrations.common import agent_memory_snippet, resolve_command

    p = resolve_paths()
    config = load_config(p["config"])
    command, args = resolve_command(config.install_source)

    target = Path.cwd() if scope == "project" else Path.home()
    writers = {"claude": claude_code, "cursor": cursor, "codex": codex}
    if agent not in writers:
        typer.echo(f"unknown agent: {agent} (choose claude|cursor|codex)")
        raise typer.Exit(code=1)
    written = writers[agent].write_config(target, command, args, ns)
    typer.echo(f"wrote {agent} config -> {written}")
    typer.echo("\n" + agent_memory_snippet())


@app.command()
def doctor() -> None:
    """Verify install: graph loads, schema valid, ns resolves, a tool responds."""
    p = resolve_paths()
    problems = []

    facts_path = p["out"] / "facts.jsonl"
    if not facts_path.exists():
        typer.echo(f"FAIL: facts.jsonl not found at {facts_path}")
        raise typer.Exit(code=1)

    try:
        from lorekeep.store.graph import GraphStore
        store = GraphStore.from_jsonl(facts_path)
    except Exception as exc:
        typer.echo(f"FAIL: cannot load graph: {exc}")
        raise typer.Exit(code=1)

    if not p["schema"].exists():
        problems.append("schema.json missing")
    else:
        try:
            load_schema(p["schema"])
        except Exception as exc:
            problems.append(f"schema invalid: {exc}")

    raw_ns = os.environ.get("LOREKEEP_NS")
    allowed = [x.strip() for x in raw_ns.split(",")] if raw_ns else load_config(p["config"]).ns.default

    try:
        from lorekeep.mcp_server import configure, list_namespaces
        configure(graph_dir=p["out"], allowed_ns=allowed, schema_path=p["schema"])
        ns = list_namespaces()
    except Exception as exc:
        problems.append(f"mcp configure/tool failed: {exc}")
        ns = []

    if problems:
        typer.echo("FAIL: " + "; ".join(problems))
        raise typer.Exit(code=1)

    typer.echo(
        f"all checks passed: {len(store.node_ids())} nodes, "
        f"{len(store.all_edges())} edges, namespaces={ns}"
    )


@app.command()
def init() -> None:
    """Bootstrap the data home: config + schema + raw/graph dirs."""
    p = resolve_paths()
    created = []
    p["config"].parent.mkdir(parents=True, exist_ok=True)
    if not p["config"].exists():
        p["config"].write_text(DEFAULT_CONFIG_YAML)
        created.append(str(p["config"]))
    p["schema"].parent.mkdir(parents=True, exist_ok=True)
    if not p["schema"].exists():
        p["schema"].write_text(json.dumps(DEFAULT_SCHEMA, indent=2))
        created.append(str(p["schema"]))
    p["raw"].mkdir(parents=True, exist_ok=True)
    p["out"].mkdir(parents=True, exist_ok=True)
    typer.echo(f"home ready: config={p['config']}")
    typer.echo(f"  schema={p['schema']}  raw={p['raw']}  graph={p['out']}")
    if created:
        typer.echo(f"  wrote defaults: {created}")
    else:
        typer.echo("  (existing config/schema preserved)")


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
      claude   Claude Code sessions. --quick copies memory/*.md only (no LLM);
               default (deep) adds LLM-summarized transcript analysis.
      cursor   Cursor composer conversations (GLOBAL state.vscdb). Deep-only --
               --quick is rejected (Cursor has no curated memory files).
    """
    if from_source not in ("claude", "cursor"):
        typer.echo(f"unknown source: {from_source} (claude | cursor)")
        raise typer.Exit(code=1)

    p = resolve_paths()
    config = load_config(p["config"])

    def _build_import_provider():
        """Deep-mode provider (fake under LOREKEEP_PROVIDER=fake, else config)."""
        if os.environ.get("LOREKEEP_PROVIDER") == "fake":
            from lorekeep.compile.providers import FakeProvider
            # Provide enough canned responses for deep mode batches
            canned = "# Knowledge Summary\n\n## Decisions\n- No real session data imported (fake provider).\n"
            return FakeProvider(responses=[canned] * 50)
        return _build_provider(config)

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
            provider=_build_import_provider(), dry_run=dry_run,
        )
        ses_count = len(result.get("session", []))
        if dry_run:
            typer.echo(f"dry-run: would import {ses_count} cursor session files")
        else:
            typer.echo(f"imported: {ses_count} session files -> raw/{ns}/")
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

    provider = None if quick else _build_import_provider()

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
        typer.echo(f"imported: {mem_count} memories -> raw/{memory_ns}/, "
                   f"{ses_count} session files -> raw/{session_ns}/")
        if not quick:
            typer.echo("next: lorekeep compile")


if __name__ == "__main__":
    app()
