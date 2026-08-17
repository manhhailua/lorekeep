"""Load Lorekeep config. Path resolved by paths.resolve_paths() (dev .lorekeep/, LOREKEEP_HOME, or ~/.lorekeep)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator


class ProviderConfig(BaseModel):
    model: str = "openai/gpt-4o-mini"   # must be {provider}/{model} — litellm routes by prefix
    api_base: str | None = None      # set for ollama or openai-compatible endpoints
    api_key_env: str | None = None   # env var holding the api key (else litellm default)
    api_key: str | None = None       # inline key (gitignored config only; env is safer)
    temperature: float = 0.0
    timeout_seconds: float = Field(default=120.0, gt=0)
    max_retries: int = Field(default=2, ge=0)


class CompileConfig(BaseModel):
    chunk_lines: int = 60
    # ISO 639-1 alpha-2 code, normalized to lowercase by contract.
    language: str = Field(default="en", pattern=r"^[a-z]{2}$")
    # Parallel extraction: ThreadPoolExecutor worker count (1 = sequential).
    max_workers: int = Field(default=4, ge=1, le=32)
    # Streaming flush: resolve + write facts.jsonl every N completed chunks.
    # 0 = no intermediate flush (write only at the end — legacy behavior).
    flush_interval: int = Field(default=10, ge=0, le=500)
    # Fetch every props.image_links URL after resolve and drop the dead ones.
    # This is the only network access compile makes; set false to stay offline.
    check_image_links: bool = True
    image_check_timeout: float = Field(default=10.0, gt=0, le=120)
    image_check_workers: int = Field(default=8, ge=1, le=32)


class NamespacesConfig(BaseModel):
    """Read visibility and agent-write ownership."""

    read: list[str] = Field(default_factory=lambda: ["*"])
    write: str = "me"
    token_map: dict[str, list[str]] = Field(default_factory=dict)

    @field_validator("write")
    @classmethod
    def validate_write_namespace(cls, value: str) -> str:
        """Keep read patterns out of facts and journal paths."""
        value = value.strip()
        if not value or "*" in value or "," in value:
            raise ValueError("namespaces.write must be one concrete namespace")
        return value


def _migrate_namespace_config(data: Any) -> tuple[Any, bool]:
    """Translate legacy ``ns`` keys without overriding the new contract."""
    if not isinstance(data, dict) or "ns" not in data:
        return data, False

    legacy = data["ns"]
    if not isinstance(legacy, dict):
        raise ValueError("legacy ns config must be a mapping")

    migrated = dict(data)
    migrated.pop("ns")
    current = migrated.get("namespaces")
    if current is None:
        namespaces: dict[str, Any] = {}
    elif isinstance(current, dict):
        namespaces = dict(current)
    else:
        # Leave the invalid new value in place so Pydantic reports it.
        return migrated, True

    if "read" not in namespaces and "default" in legacy:
        namespaces["read"] = legacy["default"]
    if (
        "write" not in namespaces
        and legacy.get("personal") not in (None, "")
    ):
        namespaces["write"] = legacy["personal"]

    # Preserve any future/unknown legacy namespace settings during the rewrite.
    for key, value in legacy.items():
        if key not in {"default", "personal"}:
            namespaces.setdefault(key, value)

    namespaces.setdefault("read", ["*"])
    namespaces.setdefault("write", "me")
    migrated["namespaces"] = namespaces
    return migrated, True


class ObservabilityConfig(BaseModel):
    """Optional observability integration via litellm callbacks."""
    provider: str | None = None      # langfuse | langsmith
    api_key_env: str | None = None   # env var name (e.g. LANGFUSE_PUBLIC_KEY)
    project: str | None = None       # project name / dataset name
    api_url: str | None = None       # self-hosted endpoint (langfuse)


class BugReportConfig(BaseModel):
    """Automatic GitHub issue creation for runtime errors."""
    enabled: bool = True
    repo: str = "manhhailua/lorekeep"
    token_env: str = "LOREKEEP_GITHUB_TOKEN"  # env var name holding the GitHub PAT
    labels: list[str] = Field(default_factory=lambda: ["auto-reported"])


class AgentsConfig(BaseModel):
    """Detection, wiring, and session ingest for coding agents."""
    auto_wire: bool = True                   # daemon re-wires detected agents each cycle
    wire_scope: str = "user"                 # user | project
    wire_interval_seconds: int = Field(default=900, gt=0)
    enabled: list[str] = Field(
        default_factory=lambda: [
            "claude", "codex", "cursor", "opencode", "grok", "qoder",
            "copilot", "cmd",
        ]
    )
    watch_transcripts: bool = True           # zero-LLM dump → raw/<agent>-session/
    transcript_max_batches: int = Field(default=20, gt=0)
    transcript_max_chars: int = Field(default=20_000, gt=0)
    transcript_retain_sessions: int = Field(default=5, gt=0)
    # Native SessionEnd events ingest immediately. Agents that expose only a
    # turn/idle boundary are coalesced for this long before being treated as
    # an approximate session end.
    session_end_idle_seconds: int = Field(default=300, ge=1)
    deep_import: bool = False                # advanced opt-in: LLM summarization
    self_heal: bool = True                   # daemon auto-heals graph after compile
    auto_backup: bool = True                 # daemon auto-backups after graph changes


class BackupConfig(BaseModel):
    """Backup repository configuration."""
    branch: str = "main"                     # git branch for the backup repo
    auto_resolve_durable: bool = False       # LLM-assisted merge for durable conflicts


class Config(BaseModel):
    provider: ProviderConfig = Field(default_factory=ProviderConfig)
    compile: CompileConfig = Field(default_factory=CompileConfig)
    namespaces: NamespacesConfig = Field(default_factory=NamespacesConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    bugreport: BugReportConfig = Field(default_factory=BugReportConfig)
    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    backup: BackupConfig = Field(default_factory=BackupConfig)
    install_source: str | None = None      # pypi | local | git+URL | path

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_namespaces(cls, data: Any) -> Any:
        """Accept legacy dictionaries passed directly to ``model_validate``."""
        migrated, _ = _migrate_namespace_config(data)
        return migrated


def _validate_provider(cfg: Config) -> None:
    """Fail fast on a bare model name.

    A bare name (no ``/``) fails deep inside litellm with the opaque
    ``LLM Provider NOT provided`` error. ``validate_model_prefix`` raises
    ``ValueError`` with an actionable suggestion instead.
    """
    from lorekeep.providers import validate_model_prefix

    if cfg.provider.model:
        validate_model_prefix(cfg.provider.model)


def migrate_config_file(path: Path) -> Any:
    """Persist the one-time namespace rename and return parsed YAML data."""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    data, migrated = _migrate_namespace_config(data)
    if migrated:
        # Validate the migrated section before replacing the user's file.
        NamespacesConfig.model_validate(data.get("namespaces"))
        from lorekeep.integrations.common import atomic_write

        atomic_write(
            path,
            yaml.dump(data, default_flow_style=False, sort_keys=False),
        )
    return data


def load_config(path: Path) -> Config:
    if not path.exists():
        return Config()
    data = migrate_config_file(path)
    cfg = Config.model_validate(data)
    _validate_provider(cfg)
    return cfg
