"""Provider/model enumeration via litellm for interactive setup.

Lists all providers with chat models, lets the user search and pick,
then lists chat models for the selected provider with cost info.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class ModelInfo:
    """Display info for a single model."""
    model: str
    provider: str
    mode: str
    input_cost: float  # per token
    output_cost: float
    max_input_tokens: int | None
    supports_function_calling: bool


def _suppress_stderr():
    """Redirect stderr to suppress litellm's 'Provider List' warnings."""
    old = os.dup(2)
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, 2)
    return old, devnull


def _restore_stderr(old, devnull):
    os.dup2(old, 2)
    os.close(old)
    os.close(devnull)


def list_providers() -> list[tuple[str, int]]:
    """Return (provider_name, chat_model_count) for all providers with chat models.

    Sorted alphabetically.
    """
    import litellm

    old, devnull = _suppress_stderr()
    try:
        result: list[tuple[str, int]] = []
        for provider in sorted(litellm.models_by_provider.keys()):
            models = litellm.models_by_provider[provider]
            chat_count = 0
            for m in models:
                try:
                    info = litellm.get_model_info(m)
                    if info.get("mode") == "chat":
                        chat_count += 1
                except Exception:
                    pass
            if chat_count > 0:
                result.append((provider, chat_count))
        return result
    finally:
        _restore_stderr(old, devnull)


def list_models(provider: str) -> list[ModelInfo]:
    """Return chat models for a provider, sorted by cost (cheapest first)."""
    import litellm

    old, devnull = _suppress_stderr()
    try:
        raw = litellm.models_by_provider.get(provider, set())
        models: list[ModelInfo] = []
        for m in sorted(raw):
            try:
                info = litellm.get_model_info(m)
            except Exception:
                continue
            if info.get("mode") != "chat":
                continue
            models.append(ModelInfo(
                model=m,
                provider=provider,
                mode=info.get("mode", "chat"),
                input_cost=info.get("input_cost_per_token", 0) or 0,
                output_cost=info.get("output_cost_per_token", 0) or 0,
                max_input_tokens=info.get("max_input_tokens"),
                supports_function_calling=info.get("supports_function_calling", False),
            ))
        models.sort(key=lambda x: x.input_cost)
        return models
    finally:
        _restore_stderr(old, devnull)


def format_cost(cost_per_token: float) -> str:
    """Format per-token cost as $X/M-tokens."""
    per_million = cost_per_token * 1_000_000
    if per_million == 0:
        return "free"
    if per_million < 1:
        return f"${per_million:.2f}/M"
    return f"${per_million:.0f}/M"


# Providers that allow free-text model names (local runtimes)
DYNAMIC_PROVIDERS = {"ollama", "vllm", "lm_studio", "openai_compat", "aleph_alpha"}


def is_dynamic(provider: str) -> bool:
    """True for providers where model names are free-text (ollama, vllm, etc.)."""
    return provider in DYNAMIC_PROVIDERS


def search_providers(query: str, providers: list[tuple[str, int]] | None = None) -> list[tuple[str, int]]:
    """Fuzzy search providers by name."""
    if providers is None:
        providers = list_providers()
    q = query.lower()
    return [(p, c) for p, c in providers if q in p.lower()]


# Popular providers shown first in the default list
POPULAR = [
    "openai", "anthropic", "deepseek", "dashscope", "gemini",
    "groq", "mistral", "xai", "ollama", "together_ai",
    "fireworks_ai", "openrouter", "perplexity", "cohere", "ai21",
]
