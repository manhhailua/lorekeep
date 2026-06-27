"""Default config + schema used by `lorekeep init` to bootstrap a fresh home."""
from __future__ import annotations

DEFAULT_SCHEMA = {
    "version": 2,
    "node_types": {
        "service": {"props": {"name": "string", "lang": "string"}},
        "team": {"props": {"name": "string"}},
        "decision": {"props": {"title": "string"}},
        "project": {"props": {"name": "string", "status": "string"}},
        "person": {"props": {"name": "string", "role": "string"}},
        "tool": {"props": {"name": "string", "category": "string"}},
        "command": {"props": {"name": "string", "platform": "string"}},
        "concept": {"props": {"name": "string", "domain": "string"}},
        "note": {"props": {"title": "string", "topic": "string"}},
        "document": {"props": {"title": "string", "kind": "string"}},
    },
    "edge_types": {
        "depends_on": {"from": "service", "to": "service"},
        "decided_by": {"from": "decision", "to": "team"},
        "owns": {"from": "team", "to": "service"},
        "part_of": {"from": "service", "to": "project"},
        "uses": {"from": "service", "to": "tool"},
        "mentions": {"from": "note", "to": "concept"},
        "documents": {"from": "document", "to": "concept"},
        "describes": {"from": "note", "to": "service"},
        "relates_to": {"from": "concept", "to": "concept"},
    },
}

DEFAULT_CONFIG_YAML = """\
provider:
  backend: openai
  model: openai/gpt-4o-mini
  api_base: null
  api_key_env: OPENAI_API_KEY
  api_key: null
  temperature: 0.0
compile:
  chunk_lines: 60
ns:
  default: [public]
install_source: pypi
"""

PROVIDER_PRESETS: dict[str, dict] = {
    "1": {
        "label": "OpenAI (gpt-4o-mini)",
        "backend": "openai",
        "model": "openai/gpt-4o-mini",
        "api_base": None,
        "api_key_env": "OPENAI_API_KEY",
    },
    "2": {
        "label": "Anthropic (claude-sonnet-4-20250514)",
        "backend": "anthropic",
        "model": "anthropic/claude-sonnet-4-20250514",
        "api_base": None,
        "api_key_env": "ANTHROPIC_API_KEY",
    },
    "3": {
        "label": "DashScope/Qwen (qwen-plus)",
        "backend": "openai",
        "model": "openai/qwen-plus",
        "api_base": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "api_key_env": "DASHSCOPE_API_KEY",
    },
    "4": {
        "label": "Ollama (local, no API key needed)",
        "backend": "openai",
        "model": "ollama/llama3.2",
        "api_base": "http://localhost:11434",
        "api_key_env": None,
    },
    "5": {
        "label": "Skip — offline/fake mode (set LOREKEEP_PROVIDER=fake to compile)",
        "backend": "openai",
        "model": "openai/gpt-4o-mini",
        "api_base": None,
        "api_key_env": None,
    },
}

SAMPLE_DOC = """\
# Lorekeep sample

This file demonstrates how raw markdown becomes a knowledge graph.
Delete it once you add your own docs.

## Services

**api-gateway** is the main entry point, written in Go.

**auth-service** handles authentication, written in Python.

The api-gateway depends on auth-service for token validation.

## Decisions

ADR-001: Adopt api-gateway pattern for all client traffic.
This was decided by the backend team.
"""
