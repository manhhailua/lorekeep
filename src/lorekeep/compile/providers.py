"""LLM provider abstraction. litellm is the only hard dependency on a vendor."""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class LLMProvider(Protocol):
    def extract_json(self, system: str, user: str) -> str: ...
    def complete(self, system: str, user: str) -> str: ...


class FakeProvider:
    """Returns canned responses in order. Used by tests; never hits a network."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def extract_json(self, system: str, user: str) -> str:
        self.calls.append(("json", system, user))
        if not self._responses:
            raise RuntimeError("FakeProvider: no canned response left")
        return self._responses.pop(0)

    def complete(self, system: str, user: str) -> str:
        self.calls.append(("complete", system, user))
        if not self._responses:
            raise RuntimeError("FakeProvider: no canned response left")
        return self._responses.pop(0)


class LiteLLMProvider:
    """Real provider backed by litellm. Supports openai/anthropic/ollama."""

    def __init__(self, model: str, api_base: str | None = None,
                 temperature: float = 0.0, api_key: str | None = None) -> None:
        import logging
        import litellm
        import os

        litellm.suppress_debug_info = True
        litellm.set_verbose = False
        logging.getLogger("litellm").setLevel(logging.WARNING)
        logging.getLogger("LiteLLM").setLevel(logging.WARNING)
        logging.getLogger("LiteLLM Router").setLevel(logging.WARNING)
        logging.getLogger("LiteLLM Proxy").setLevel(logging.WARNING)
        os.environ["LITELLM_LOG"] = "WARNING"

        self.model = model
        self.api_base = api_base
        self.temperature = temperature
        self.api_key = api_key

    def extract_json(self, system: str, user: str) -> str:
        import litellm  # imported lazily so tests need not install it
        resp = litellm.completion(
            model=self.model,
            api_base=self.api_base,
            api_key=self.api_key,
            temperature=self.temperature,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
        )
        return resp.choices[0].message.content

    def complete(self, system: str, user: str) -> str:
        """Free-form completion (no response_format constraint).

        Used for tasks that need markdown or prose, not structured JSON.
        """
        import litellm
        resp = litellm.completion(
            model=self.model,
            api_base=self.api_base,
            api_key=self.api_key,
            temperature=self.temperature,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return resp.choices[0].message.content
