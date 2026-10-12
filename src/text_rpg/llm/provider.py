"""Abstract LLM provider interface."""
from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Any

from text_rpg.llm.errors import LLMMalformedOutputError

logger = logging.getLogger(__name__)


class LLMProvider(ABC):
    @abstractmethod
    def generate(self, prompt: str, system_prompt: str | None = None,
                 temperature: float = 0.8, max_tokens: int = 1024) -> str:
        """Generate free text. Raises LLMUnavailableError when the provider
        cannot be reached — never returns fabricated fallback prose."""

    @abstractmethod
    def is_available(self) -> bool: ...

    @property
    @abstractmethod
    def model_name(self) -> str: ...

    def generate_structured(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 512,
        schema: type | None = None,
        retries: int = 2,
    ) -> dict[str, Any]:
        """Generate a JSON object, optionally validated against a pydantic schema.

        On invalid output the validation error is fed back to the model and the
        call is retried. Raises LLMMalformedOutputError after all retries fail
        and LLMUnavailableError when the provider cannot be reached.
        """
        json_system = (system_prompt or "") + "\n\nYou MUST respond with valid JSON only. No other text."
        current_prompt = prompt
        last_error = ""

        for attempt in range(retries + 1):
            text = self._generate_for_structured(
                current_prompt, json_system, temperature, max_tokens, schema
            )
            try:
                parsed = self._parse_json(text)
            except ValueError as e:
                last_error = str(e)
                logger.warning(f"Structured output parse failed (attempt {attempt + 1}): {last_error}")
                current_prompt = self._build_retry_prompt(prompt, last_error)
                continue

            if schema is not None:
                try:
                    validated = schema.model_validate(parsed)
                    return validated.model_dump()
                except Exception as e:
                    last_error = str(e)
                    logger.warning(f"Structured output schema validation failed (attempt {attempt + 1}): {last_error}")
                    current_prompt = self._build_retry_prompt(prompt, last_error)
                    continue

            return parsed

        raise LLMMalformedOutputError(
            f"LLM produced invalid structured output after {retries + 1} attempts: {last_error}"
        )

    def _generate_for_structured(
        self,
        prompt: str,
        system_prompt: str,
        temperature: float,
        max_tokens: int,
        schema: type | None,
    ) -> str:
        """Hook for providers that support native structured output enforcement."""
        return self.generate(prompt, system_prompt, temperature, max_tokens)

    @staticmethod
    def _build_retry_prompt(original_prompt: str, error: str) -> str:
        return (
            f"{original_prompt}\n\n"
            f"Your previous response was invalid: {error[:500]}\n"
            f"Respond again with corrected, valid JSON only."
        )

    # -- Shared utilities --

    def _parse_json(self, text: str) -> dict[str, Any]:
        """Extract a JSON object from raw model output. Raises ValueError on failure."""
        text = text.strip()
        if "```json" in text:
            start = text.index("```json") + 7
            rest = text[start:]
            end = start + rest.index("```") if "```" in rest else len(text)
            text = text[start:end].strip()
        elif "```" in text:
            start = text.index("```") + 3
            rest = text[start:]
            end = start + rest.index("```") if "```" in rest else len(text)
            text = text[start:end].strip()
        for i, c in enumerate(text):
            if c in "{[":
                text = text[i:]
                break
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"not valid JSON: {e}") from e
        if not isinstance(parsed, dict):
            raise ValueError(f"expected a JSON object, got {type(parsed).__name__}")
        return parsed
