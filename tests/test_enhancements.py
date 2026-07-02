"""Tests for enhancements: prefix cache prompt, observability config, config CLI."""
from __future__ import annotations

import yaml
from pathlib import Path

import pytest
from typer.testing import CliRunner

runner = CliRunner()


# ── Prefix cache: system prompt structure ─────────────────────────────────

class TestSystemPromptStructure:
    def test_system_prompt_includes_schema(self):
        from lorekeep.compile.extract import build_system_prompt
        from lorekeep.models import Schema

        schema = Schema.load({
            "version": 1,
            "node_types": {"service": {"props": {"name": "string"}}},
            "edge_types": {"depends_on": {"from": "service", "to": "service"}},
        })
        prompt = build_system_prompt(schema)
        assert "service" in prompt
        assert "depends_on" in prompt
        assert "knowledge-graph extractor" in prompt

    def test_user_prompt_is_chunk_text_only(self):
        from lorekeep.compile.extract import build_prompt
        from lorekeep.models import DocChunk

        chunk = DocChunk(
            path="raw/test.md", start_line=1, end_line=5,
            text="The payments-api is a Go service.", namespace="backend",
        )
        prompt = build_prompt(chunk, None)
        assert prompt == "The payments-api is a Go service."

    def test_system_prompt_constant_across_chunks(self):
        """System prompt should be identical for different chunks (same schema)."""
        from lorekeep.compile.extract import build_system_prompt
        from lorekeep.models import DocChunk, Schema

        schema = Schema.load({"version": 1, "node_types": {"x": {}}, "edge_types": {}})
        c1 = DocChunk(path="a.md", start_line=1, end_line=1, text="chunk1", namespace="ns")
        c2 = DocChunk(path="b.md", start_line=1, end_line=1, text="chunk2", namespace="ns")

        sys1 = build_system_prompt(schema)
        sys2 = build_system_prompt(schema)
        assert sys1 == sys2


# ── Observability config ──────────────────────────────────────────────────

class TestObservabilityConfig:
    def test_default_no_observability(self):
        from lorekeep.config import Config
        c = Config()
        assert c.observability.provider is None

    def test_langfuse_config(self):
        from lorekeep.config import Config
        data = {
            "provider": {"model": "gpt-4o"},
            "observability": {
                "provider": "langfuse",
                "api_key_env": "LANGFUSE_PUBLIC_KEY",
                "api_url": "https://langfuse.example.com",
            },
        }
        c = Config.model_validate(data)
        assert c.observability.provider == "langfuse"
        assert c.observability.api_key_env == "LANGFUSE_PUBLIC_KEY"

    def test_langsmith_config(self):
        from lorekeep.config import Config
        data = {
            "provider": {"model": "gpt-4o"},
            "observability": {
                "provider": "langsmith",
                "project": "lorekeep-prod",
            },
        }
        c = Config.model_validate(data)
        assert c.observability.provider == "langsmith"
        assert c.observability.project == "lorekeep-prod"

    def test_setup_observability_noop_when_no_provider(self):
        from lorekeep.compile.providers import setup_observability
        setup_observability(provider=None)
        # Should not raise

    def test_setup_observability_langfuse(self, monkeypatch):
        import litellm
        from lorekeep.compile.providers import setup_observability

        monkeypatch.delenv("LANGFUSE_HOST", raising=False)
        setup_observability(
            provider="langfuse",
            api_url="https://lf.example.com",
        )
        import os
        assert os.environ.get("LANGFUSE_HOST") == "https://lf.example.com"
        assert "langfuse" in litellm.success_callback

    def test_setup_observability_langsmith(self, monkeypatch):
        import litellm
        from lorekeep.compile.providers import setup_observability

        monkeypatch.delenv("LANGCHAIN_PROJECT", raising=False)
        setup_observability(
            provider="langsmith",
            project="test-project",
        )
        import os
        assert os.environ.get("LANGCHAIN_PROJECT") == "test-project"
        assert "langsmith" in litellm.success_callback


# ── Config CLI ────────────────────────────────────────────────────────────

class TestConfigCLI:
    def test_config_show(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        home.mkdir()
        config = home / "config.yaml"
        config.write_text("provider:\n  model: gpt-4o\n")

        monkeypatch.setenv("LOREKEEP_HOME", str(home))
        from lorekeep.cli import app
        result = runner.invoke(app, ["config", "show"])
        assert result.exit_code == 0
        assert "gpt-4o" in result.stdout

    def test_config_set_string(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        home.mkdir()
        config = home / "config.yaml"
        config.write_text("provider:\n  model: gpt-4o\n")

        monkeypatch.setenv("LOREKEEP_HOME", str(home))
        from lorekeep.cli import app
        result = runner.invoke(app, ["config", "set", "provider.model", "deepseek/deepseek-chat"])
        assert result.exit_code == 0

        data = yaml.safe_load(config.read_text())
        assert data["provider"]["model"] == "deepseek/deepseek-chat"

    def test_config_set_nested_new_key(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        home.mkdir()
        config = home / "config.yaml"
        config.write_text("provider:\n  model: gpt-4o\n")

        monkeypatch.setenv("LOREKEEP_HOME", str(home))
        from lorekeep.cli import app
        result = runner.invoke(app, ["config", "set", "observability.provider", "langfuse"])
        assert result.exit_code == 0

        data = yaml.safe_load(config.read_text())
        assert data["observability"]["provider"] == "langfuse"

    def test_config_set_list(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        home.mkdir()
        config = home / "config.yaml"
        config.write_text("ns:\n  default: [public]\n")

        monkeypatch.setenv("LOREKEEP_HOME", str(home))
        from lorekeep.cli import app
        result = runner.invoke(app, ["config", "set", "ns.default", "backend,frontend"])
        assert result.exit_code == 0

        data = yaml.safe_load(config.read_text())
        assert data["ns"]["default"] == ["backend", "frontend"]

    def test_config_set_int(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        home.mkdir()
        config = home / "config.yaml"
        config.write_text("compile:\n  chunk_lines: 60\n")

        monkeypatch.setenv("LOREKEEP_HOME", str(home))
        from lorekeep.cli import app
        result = runner.invoke(app, ["config", "set", "compile.chunk_lines", "30"])
        assert result.exit_code == 0

        data = yaml.safe_load(config.read_text())
        assert data["compile"]["chunk_lines"] == 30

    def test_config_show_no_config(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("LOREKEEP_HOME", str(home))
        from lorekeep.cli import app
        result = runner.invoke(app, ["config", "show"])
        assert result.exit_code == 1
