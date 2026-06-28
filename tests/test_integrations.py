import json
from pathlib import Path
from lorekeep.integrations.common import resolve_command, agent_memory_snippet
from lorekeep.integrations import claude_code, cursor, codex, opencode


def test_resolve_command_pypi():
    assert resolve_command(None) == ("uvx", ["lorekeep", "serve", "--transport", "stdio"])
    assert resolve_command("pypi") == ("uvx", ["lorekeep", "serve", "--transport", "stdio"])


def test_resolve_command_local():
    assert resolve_command("local") == ("lorekeep", ["serve", "--transport", "stdio"])


def test_resolve_command_git():
    cmd, args = resolve_command("git+https://github.com/x/lorekeep.git")
    assert cmd == "uvx"
    assert args[:2] == ["--from", "git+https://github.com/x/lorekeep.git"]
    assert "serve" in args


def test_claude_writes_mcp_json(tmp_path: Path):
    claude_code.write_config(tmp_path, "uvx", ["lorekeep", "serve", "--transport", "stdio"],
                             ns="teams/backend")
    data = json.loads((tmp_path / ".mcp.json").read_text())
    assert data["mcpServers"]["lorekeep"]["command"] == "uvx"
    assert data["mcpServers"]["lorekeep"]["env"]["LOREKEEP_NS"] == "teams/backend"


def test_cursor_writes_mcp_json(tmp_path: Path):
    cursor.write_config(tmp_path, "uvx", ["lorekeep", "serve", "--transport", "stdio"], ns=None)
    data = json.loads((tmp_path / ".cursor" / "mcp.json").read_text())
    assert "lorekeep" in data["mcpServers"]


def test_codex_writes_toml(tmp_path: Path):
    codex.write_config(tmp_path, "uvx", ["lorekeep", "serve", "--transport", "stdio"],
                       ns="teams/backend")
    text = (tmp_path / "config.toml").read_text()
    assert "[mcp_servers.lorekeep]" in text
    assert 'command = "uvx"' in text
    assert 'LOREKEEP_NS = "teams/backend"' in text


def test_agent_memory_snippet_mentions_provenance():
    s = agent_memory_snippet()
    assert "src" in s and "namespace" in s.lower()


def test_codex_write_is_idempotent(tmp_path: Path):
    codex.write_config(tmp_path, "uvx", ["lorekeep", "serve", "--transport", "stdio"], ns="team/a")
    codex.write_config(tmp_path, "uvx", ["lorekeep", "serve", "--transport", "stdio"], ns="team/b")
    text = (tmp_path / "config.toml").read_text()
    assert text.count("[mcp_servers.lorekeep]") == 1   # replaced, not duplicated
    assert 'LOREKEEP_NS = "team/b"' in text             # updated value


def test_codex_escapes_quotes_in_ns(tmp_path: Path):
    codex.write_config(tmp_path, "uvx", ["lorekeep"], ns='team/"evil')
    text = (tmp_path / "config.toml").read_text()
    assert 'team/\\"evil' in text                      # quote escaped, TOML stays valid


def test_codex_rejects_newline_in_ns(tmp_path: Path):
    import pytest
    with pytest.raises(ValueError):
        codex.write_config(tmp_path, "uvx", ["lorekeep"], ns="team\n[malicious]")


def test_opencode_writes_json(tmp_path: Path):
    opencode.write_config(tmp_path, "uvx", ["lorekeep", "serve", "--transport", "stdio"],
                          ns="teams/backend")
    data = json.loads((tmp_path / "opencode.json").read_text())
    entry = data["mcp"]["lorekeep"]
    assert entry["type"] == "local"
    assert entry["command"] == ["uvx", "lorekeep", "serve", "--transport", "stdio"]
    assert entry["enabled"] is True
    assert entry["environment"]["LOREKEEP_NS"] == "teams/backend"


def test_opencode_no_ns(tmp_path: Path):
    opencode.write_config(tmp_path, "uvx", ["lorekeep", "serve", "--transport", "stdio"], ns=None)
    data = json.loads((tmp_path / "opencode.json").read_text())
    entry = data["mcp"]["lorekeep"]
    assert "environment" not in entry


def test_opencode_idempotent(tmp_path: Path):
    opencode.write_config(tmp_path, "uvx", ["lorekeep"], ns="team/a")
    opencode.write_config(tmp_path, "uvx", ["lorekeep"], ns="team/b")
    data = json.loads((tmp_path / "opencode.json").read_text())
    assert data["mcp"]["lorekeep"]["environment"]["LOREKEEP_NS"] == "team/b"


def test_opencode_preserves_existing_keys(tmp_path: Path):
    existing = {"$schema": "https://opencode.ai/config.json", "model": "anthropic/claude-sonnet-4-5", "mcp": {"other": {"type": "local", "command": ["foo"]}}}
    (tmp_path / "opencode.json").write_text(json.dumps(existing))
    opencode.write_config(tmp_path, "uvx", ["lorekeep", "serve", "--transport", "stdio"], ns=None)
    data = json.loads((tmp_path / "opencode.json").read_text())
    assert data["$schema"] == "https://opencode.ai/config.json"
    assert data["model"] == "anthropic/claude-sonnet-4-5"
    assert "other" in data["mcp"]
    assert "lorekeep" in data["mcp"]
