"""General-purpose LiteLLM provider for cloud and local models."""
from __future__ import annotations

import logging
from typing import Any

from text_rpg.llm.errors import LLMUnavailableError
from text_rpg.llm.provider import LLMProvider

logger = logging.getLogger(__name__)


class LiteLLMProvider(LLMProvider):
    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        api_base: str | None = None,
        extra_params: dict[str, Any] | None = None,
    ):
        self._model = model
        self._api_key = api_key
        self._api_base = api_base
        self._extra_params = extra_params or {}

    def _build_kwargs(self, prompt: str, system_prompt: str | None,
                      temperature: float, max_tokens: int) -> dict[str, Any]:
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            **self._extra_params,
        }
        if self._api_key:
            kwargs["api_key"] = self._api_key
        if self._api_base:
            kwargs["api_base"] = self._api_base
        return kwargs

    def _completion(self, kwargs: dict[str, Any]) -> str:
        import litellm
        litellm.drop_params = True
        try:
            response = litellm.completion(**kwargs)
            return response.choices[0].message.content or ""
        except Exception as e:
            raise LLMUnavailableError(f"LLM request failed ({self._model}): {e}") from e

    def generate(self, prompt: str, system_prompt: str | None = None,
                 temperature: float = 0.8, max_tokens: int = 1024) -> str:
        return self._completion(self._build_kwargs(prompt, system_prompt, temperature, max_tokens))

    def _generate_for_structured(
        self,
        prompt: str,
        system_prompt: str,
        temperature: float,
        max_tokens: int,
        schema: type | None,
    ) -> str:
        kwargs = self._build_kwargs(prompt, system_prompt, temperature, max_tokens)
        # Ollama's response_format translation returns empty content with some
        # models — rely on the JSON prompt + parse/retry loop for local models.
        if schema is not None and not self._model.startswith("ollama/"):
            # LiteLLM converts a pydantic model into a json_schema response_format
            # for providers that support it; drop_params discards it elsewhere.
            kwargs["response_format"] = schema
            try:
                text = self._completion(kwargs)
                if text.strip():
                    return text
                # Empty content with native enforcement — fall through and
                # retry without it rather than failing the parse loop.
            except LLMUnavailableError:
                # Some providers reject response_format outright rather than
                # dropping it — retry once without native enforcement before
                # concluding the provider is down.
                pass
            kwargs.pop("response_format", None)
        return self._completion(kwargs)

    def is_available(self) -> bool:
        if self._model.startswith("ollama/"):
            # Local model — HTTP check
            try:
                import urllib.request
                base = self._api_base or "http://localhost:11434"
                req = urllib.request.Request(f"{base}/api/tags")
                with urllib.request.urlopen(req, timeout=5):
                    return True
            except Exception:
                return False
        # Cloud model — check that an API key is set
        return bool(self._api_key)

    @property
    def model_name(self) -> str:
        return self._model
