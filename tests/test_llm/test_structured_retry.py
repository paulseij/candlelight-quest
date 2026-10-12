"""Tests for structured-output enforcement and retry in the provider base."""
from __future__ import annotations

import pytest
from pydantic import BaseModel, Field

from text_rpg.llm.errors import LLMMalformedOutputError, LLMUnavailableError
from text_rpg.llm.provider import LLMProvider


class _Schema(BaseModel):
    plausibility: float = Field(gt=0.0, le=1.0)
    skill: str = "athletics"


class StubProvider(LLMProvider):
    """Returns scripted responses in order; records prompts it received."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.prompts: list[str] = []

    def generate(self, prompt, system_prompt=None, temperature=0.8, max_tokens=1024):
        self.prompts.append(prompt)
        if not self._responses:
            raise AssertionError("StubProvider ran out of responses")
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def is_available(self):
        return True

    @property
    def model_name(self):
        return "stub"


def test_valid_json_first_try():
    provider = StubProvider(['{"plausibility": 0.7, "skill": "stealth"}'])
    result = provider.generate_structured("do it", schema=_Schema)
    assert result == {"plausibility": 0.7, "skill": "stealth"}
    assert len(provider.prompts) == 1


def test_retry_feeds_error_back_and_succeeds():
    provider = StubProvider([
        "utter nonsense, no json here",
        '{"plausibility": 0.5}',
    ])
    result = provider.generate_structured("do it", schema=_Schema)
    assert result["plausibility"] == 0.5
    assert len(provider.prompts) == 2
    assert "previous response was invalid" in provider.prompts[1]


def test_schema_violation_retries():
    provider = StubProvider([
        '{"plausibility": 4.2}',  # out of range
        '{"plausibility": 0.9}',
    ])
    result = provider.generate_structured("do it", schema=_Schema)
    assert result["plausibility"] == 0.9


def test_malformed_after_all_retries_raises():
    provider = StubProvider(["junk", "junk", "junk"])
    with pytest.raises(LLMMalformedOutputError):
        provider.generate_structured("do it", schema=_Schema, retries=2)
    assert len(provider.prompts) == 3


def test_unavailable_propagates():
    provider = StubProvider([LLMUnavailableError("down")])
    with pytest.raises(LLMUnavailableError):
        provider.generate_structured("do it", schema=_Schema)


def test_no_schema_returns_parsed_dict():
    provider = StubProvider(['```json\n{"a": 1}\n```'])
    assert provider.generate_structured("do it") == {"a": 1}


def test_non_dict_json_retries_then_raises():
    provider = StubProvider(["[1, 2]", "[3]"])
    with pytest.raises(LLMMalformedOutputError):
        provider.generate_structured("do it", retries=1)
