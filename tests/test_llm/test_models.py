"""Tests for src/text_rpg/llm/models.py provider inference."""
from __future__ import annotations

import pytest

from text_rpg.llm.models import MODELS, get_model_by_id, infer_provider


@pytest.mark.parametrize("model_id, expected", [
    ("ollama/gemma3:12b", "ollama"),
    ("gemma3:12b", "ollama"),       # bare Ollama name:tag id
    ("mistral:7b", "ollama"),
    ("anthropic/claude-sonnet-4-5", "anthropic"),
    ("claude-haiku-4-5", "anthropic"),
    ("gpt-4o-mini", "openai"),
    ("gpt-5-nano", "openai"),
])
def test_infer_provider(model_id, expected):
    assert infer_provider(model_id) == expected


def test_catalog_ids_resolve_to_their_own_provider():
    for m in MODELS:
        assert infer_provider(m.id) == m.provider, m.id


def test_get_model_by_id():
    assert get_model_by_id("gpt-5-nano") is not None
    assert get_model_by_id("nonexistent-model") is None
