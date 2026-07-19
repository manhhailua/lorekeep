"""Load Lorekeep config. Path resolved by paths.resolve_paths() (dev .lorekeep/, LOREKEEP_HOME, or XDG)."""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class ProviderConfig(BaseModel):
    model: str = "openai/gpt-4o-mini"   # must be {provider}/{model} — litellm routes by prefix
    api_base: str | None = None      # set for ollama or openai-compatible endpoints
    api_key_env: str | None = None   # env var holding the api key (else litellm default)
    api_key: str | None = None       # inline key (gitignored config only; env is safer)
    temperature: float = 0.0


class CompileConfig(BaseModel):
    chunk_lines: int = 60


class NsConfig(BaseModel):
    default: list[str] = Field(default_factory=lambda: ["public"])
    token_map: dict[str, list[str]] = Field(default_factory=dict)


class ObservabilityConfig(BaseModel):
    """Optional observability integration via litellm callbacks."""
    provider: str | None = None      # langfuse | langsmith
    api_key_env: str | None = None   # env var name (e.g. LANGFUSE_PUBLIC_KEY)
    project: str | None = None       # project name / dataset name
    api_url: str | None = None       # self-hosted endpoint (langfuse)


class Config(BaseModel):
    provider: ProviderConfig = Field(default_factory=ProviderConfig)
    compile: CompileConfig = Field(default_factory=CompileConfig)
    ns: NsConfig = Field(default_factory=NsConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    install_source: str | None = None      # pypi | local | git+URL | path


def _validate_provider(cfg: Config) -> None:
    """Fail fast on a bare model name.

    A bare name (no ``/``) fails deep inside litellm with the opaque
    ``LLM Provider NOT provided`` error. ``validate_model_prefix`` raises
    ``ValueError`` with an actionable suggestion instead.
    """
    from lorekeep.providers import validate_model_prefix

    if cfg.provider.model:
        validate_model_prefix(cfg.provider.model)


def load_config(path: Path) -> Config:
    if not path.exists():
        return Config()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cfg = Config.model_validate(data)
    _validate_provider(cfg)
    return cfg
