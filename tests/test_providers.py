"""Tests for provider/model enumeration."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from lorekeep.providers import (
    ModelInfo,
    format_cost,
    is_dynamic,
    search_providers,
    DYNAMIC_PROVIDERS,
)


class TestFormatCost:
    def test_free(self):
        assert format_cost(0) == "free"

    def test_sub_dollar(self):
        assert format_cost(0.00000028) == "$0.28/M"

    def test_above_dollar(self):
        assert format_cost(0.000003) == "$3/M"

    def test_high_cost(self):
        assert format_cost(0.000015) == "$15/M"


class TestIsDynamic:
    def test_ollama(self):
        assert is_dynamic("ollama")

    def test_vllm(self):
        assert is_dynamic("vllm")

    def test_openai_not_dynamic(self):
        assert not is_dynamic("openai")

    def test_deepseek_not_dynamic(self):
        assert not is_dynamic("deepseek")


class TestSearchProviders:
    def test_search_deepseek(self):
        providers = [("openai", 150), ("deepseek", 4), ("anthropic", 22)]
        results = search_providers("deep", providers)
        assert results == [("deepseek", 4)]

    def test_search_no_match(self):
        providers = [("openai", 150), ("anthropic", 22)]
        results = search_providers("nonexistent", providers)
        assert results == []

    def test_search_partial(self):
        providers = [("openai", 150), ("openrouter", 96), ("anthropic", 22)]
        results = search_providers("open", providers)
        assert len(results) == 2
        assert "openai" in [r[0] for r in results]
        assert "openrouter" in [r[0] for r in results]

    def test_search_case_insensitive(self):
        providers = [("OpenAI", 150)]
        results = search_providers("open", providers)
        assert len(results) == 1


class TestListProviders:
    """Tests that mock litellm to avoid network/import issues."""

    @patch("litellm.models_by_provider")
    @patch("litellm.get_model_info")
    def test_returns_providers_with_chat_models(self, mock_info, mock_map):
        from lorekeep.providers import list_providers

        mock_map.keys.return_value = ["openai", "anthropic", "assemblyai"]
        mock_map.__getitem__ = MagicMock(side_effect=lambda k: {
            "openai": {"gpt-4o"},
            "anthropic": {"claude-3-sonnet"},
            "assemblyai": set(),
        }[k])

        def fake_info(model):
            if "gpt" in model:
                return {"mode": "chat"}
            if "claude" in model:
                return {"mode": "chat"}
            return {"mode": "audio_transcription"}

        mock_info.side_effect = fake_info

        result = list_providers()
        names = [r[0] for r in result]
        assert "openai" in names
        assert "anthropic" in names
        assert "assemblyai" not in names

    @patch("litellm.models_by_provider")
    @patch("litellm.get_model_info")
    def test_counts_chat_models(self, mock_info, mock_map):
        from lorekeep.providers import list_providers

        mock_map.keys.return_value = ["openai"]
        mock_map.__getitem__ = MagicMock(return_value={"gpt-4o", "text-embedding-3"})

        def fake_info(model):
            return {"mode": "chat"} if "gpt" in model else {"mode": "embedding"}

        mock_info.side_effect = fake_info

        result = list_providers()
        assert result == [("openai", 1)]


class TestListModels:
    @patch("litellm.models_by_provider", {"openai": {"model-a", "model-b", "model-c"}})
    @patch("litellm.get_model_info")
    def test_returns_chat_models_sorted_by_cost(self, mock_info):
        from lorekeep.providers import list_models

        models_data = {
            "model-a": {"mode": "chat", "input_cost_per_token": 0.000003, "output_cost_per_token": 0.000015,
                        "max_input_tokens": 128000, "supports_function_calling": True},
            "model-b": {"mode": "chat", "input_cost_per_token": 0.0000005, "output_cost_per_token": 0.000001,
                        "max_input_tokens": 32000, "supports_function_calling": False},
            "model-c": {"mode": "embedding", "input_cost_per_token": 0.0, "output_cost_per_token": 0.0,
                        "max_input_tokens": None, "supports_function_calling": False},
        }
        mock_info.side_effect = lambda m: models_data[m]

        result = list_models("openai")
        assert len(result) == 2
        assert result[0].model == "model-b"  # cheaper first
        assert result[1].model == "model-a"
        assert all(r.mode == "chat" for r in result)

    @patch("litellm.models_by_provider", {"openai": {"good", "bad"}})
    @patch("litellm.get_model_info")
    def test_skips_unmapped_models(self, mock_info):
        from lorekeep.providers import list_models

        def fake_info(model):
            if model == "bad":
                raise ValueError("unmapped")
            return {"mode": "chat", "input_cost_per_token": 0, "output_cost_per_token": 0,
                    "max_input_tokens": None, "supports_function_calling": False}

        mock_info.side_effect = fake_info

        result = list_models("openai")
        assert len(result) == 1
        assert result[0].model == "good"

    @patch("litellm.models_by_provider", {})
    def test_empty_provider(self):
        from lorekeep.providers import list_models
        result = list_models("nonexistent")
        assert result == []
