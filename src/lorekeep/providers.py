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


def _normalize_model_name(name: str, provider: str) -> str:
    """Normalize a litellm model name to ``{provider}/{model}`` format.

    litellm's ``models_by_provider`` may contain both prefixed (``deepseek/deepseek-chat``)
    and non-prefixed (``deepseek-chat``) variants of the same model.  Non-prefixed
    names fail at runtime for providers that litellm cannot auto-detect by pattern
    (e.g. deepseek, gemini).  Always storing the prefixed form produces a valid
    litellm model string regardless of provider.

    For runtime validation of a user-supplied model string (no known provider),
    see :func:`validate_model_prefix`.
    """
    prefix = f"{provider}/"
    if name.startswith(prefix):
        return name
    return f"{provider}/{name}"


# Bare model names mapped to their canonical ``{provider}/{model}`` form, used
# only to *suggest* a fix when a user writes an unambiguous bare name. Kept
# conservative: ``qwen-*`` is intentionally absent (DashScope uses ``openai/`` +
# ``api_base`` while native litellm uses ``dashscope/`` — ambiguous, so we emit
# the rule instead of guessing).
_BARE_ALIASES: dict[str, str] = {
    "deepseek-chat": "deepseek/deepseek-chat",
    "deepseek-reasoner": "deepseek/deepseek-reasoner",
    "gpt-4o-mini": "openai/gpt-4o-mini",
    "gpt-4o": "openai/gpt-4o",
    "gpt-4-turbo": "openai/gpt-4-turbo",
    "gpt-3.5-turbo": "openai/gpt-3.5-turbo",
    "claude-sonnet-4-20250514": "anthropic/claude-sonnet-4-20250514",
    "claude-3-5-sonnet-latest": "anthropic/claude-3-5-sonnet-latest",
    "claude-3-5-sonnet-20241022": "anthropic/claude-3-5-sonnet-20241022",
    "claude-3-haiku-20240307": "anthropic/claude-3-haiku-20240307",
    "claude-3-opus-20240229": "anthropic/claude-3-opus-20240229",
}


def suggest_model_prefix(model: str) -> str | None:
    """Return the canonical ``{provider}/{model}`` form for a known bare name.

    Returns ``None`` for already-prefixed or unknown names so callers never
    silently rewrite the user's config — they surface this as a *suggestion*.
    """
    if "/" in model:
        return None
    return _BARE_ALIASES.get(model)


def validate_model_prefix(model: str) -> None:
    """Raise ``ValueError`` unless *model* is a ``{provider}/{model}`` litellm string.

    litellm routes by the prefix (``openai/``, ``deepseek/``, ``anthropic/`` …);
    a bare name like ``deepseek-chat`` fails deep inside litellm with the opaque
    ``LLM Provider NOT provided`` error. We fail fast here with an actionable
    message, including a suggestion when the bare name is a known alias.
    """
    if "/" in model:
        return
    suggestion = suggest_model_prefix(model)
    if suggestion:
        raise ValueError(
            f"provider model must be '{{provider}}/{{model}}' (got {model!r}). "
            f"litellm routes by the prefix. Did you mean {suggestion!r}?"
        )
    raise ValueError(
        f"provider model must be '{{provider}}/{{model}}' (got {model!r}). "
        "litellm routes by the prefix — e.g. 'openai/gpt-4o-mini', "
        "'anthropic/claude-sonnet-4-20250514', 'deepseek/deepseek-chat', "
        "'ollama/llama3'."
    )



def list_models(provider: str) -> list[ModelInfo]:
    """Return chat models for a provider, sorted by cost (cheapest first).

    All model names are normalized to ``{provider}/{model}`` format and
    de-duplicated, so non-prefixed and prefixed variants of the same model
    never both appear.
    """
    import litellm

    old, devnull = _suppress_stderr()
    try:
        raw = litellm.models_by_provider.get(provider, set())
        seen: set[str] = set()
        models: list[ModelInfo] = []
        for m in sorted(raw):
            try:
                info = litellm.get_model_info(m)
            except Exception:
                continue
            if info.get("mode") != "chat":
                continue
            normalized = _normalize_model_name(m, provider)
            if normalized in seen:
                continue
            seen.add(normalized)
            models.append(ModelInfo(
                model=normalized,
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
