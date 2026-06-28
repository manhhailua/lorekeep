"""Shared integration helpers: resolve install command + agent-memory snippet."""
from __future__ import annotations


def resolve_command(install_source: str | None, subcommand: list[str] | None = None) -> tuple[str, list[str]]:
    """Return (command, args) to launch a lorekeep subcommand.

    Defaults to ``serve --transport stdio``.  Pass ``subcommand`` for others
    (e.g. ``["hook"]``).
    """
    cmd_args = subcommand or ["serve", "--transport", "stdio"]
    if not install_source or install_source == "pypi":
        return ("uvx", ["lorekeep", *cmd_args])
    if install_source == "local":
        return ("lorekeep", cmd_args)
    return ("uvx", ["--from", install_source, "lorekeep", *cmd_args])


def agent_memory_snippet() -> str:
    return (
        "## Lorekeep knowledge base (MCP)\n"
        "Before answering architecture/code/domain questions, query Lorekeep:\n"
        "search(q) -> get_node(id) -> neighbors / at_time / history as needed.\n"
        "Always cite `src` provenance. Knowledge is namespace-scoped - if a fact is\n"
        "missing, it may be outside your scope, not nonexistent.\n"
    )
