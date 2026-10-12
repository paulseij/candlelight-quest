"""Curated model catalog for the setup wizard."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelInfo:
    id: str
    provider: str
    name: str
    cost: str
    quality: str
    notes: str
    requires_key: bool
    env_var: str | None


MODELS: list[ModelInfo] = [
    ModelInfo(
        id="ollama/hf.co/google/gemma-4-12B-it-qat-q4_0-gguf:Q4_0",
        provider="ollama", name="Gemma 4 12B IT QAT",
        cost="Free (local)", quality="Recommended local candidate",
        notes="Tested on an 18 GB M3 Pro; keep one generator resident",
        requires_key=False, env_var=None,
    ),
    ModelInfo(
        id="ollama/qwen3.5:9b", provider="ollama", name="Qwen3.5 9B",
        cost="Free (local)", quality="Local fallback",
        notes="Comparison baseline; faster longer-context processing in local probes",
        requires_key=False, env_var=None,
    ),
    ModelInfo(
        id="ollama/gemma3:12b",
        provider="ollama",
        name="Gemma 3 12B",
        cost="Free (local)",
        quality="Good",
        notes="Requires ~10GB RAM, Ollama must be running",
        requires_key=False,
        env_var=None,
    ),
    ModelInfo(
        id="gpt-5-nano",
        provider="openai",
        name="GPT-5 Nano",
        cost="~$0.05/1M tokens",
        quality="Good",
        notes="Cheapest cloud option",
        requires_key=True,
        env_var="OPENAI_API_KEY",
    ),
    ModelInfo(
        id="gpt-4o-mini",
        provider="openai",
        name="GPT-4o Mini",
        cost="~$0.15/1M tokens",
        quality="Good",
        notes="Fast and cheap cloud option",
        requires_key=True,
        env_var="OPENAI_API_KEY",
    ),
    ModelInfo(
        id="gpt-4o",
        provider="openai",
        name="GPT-4o",
        cost="~$2.50/1M tokens",
        quality="Excellent",
        notes="Best OpenAI model for creative writing",
        requires_key=True,
        env_var="OPENAI_API_KEY",
    ),
    ModelInfo(
        id="anthropic/claude-haiku-4-5",
        provider="anthropic",
        name="Claude Haiku 4.5",
        cost="$1 in / $5 out per 1M",
        quality="Good",
        notes="Fast and affordable Anthropic model",
        requires_key=True,
        env_var="ANTHROPIC_API_KEY",
    ),
    ModelInfo(
        id="anthropic/claude-sonnet-5",
        provider="anthropic",
        name="Claude Sonnet 5",
        cost="$3 in / $15 out per 1M",
        quality="Excellent",
        notes="Best overall for narrative and roleplay",
        requires_key=True,
        env_var="ANTHROPIC_API_KEY",
    ),
    ModelInfo(
        id="anthropic/claude-opus-5",
        provider="anthropic",
        name="Claude Opus 5",
        cost="$5 in / $25 out per 1M",
        quality="Best",
        notes="Strongest GM: richest NPCs and judgment",
        requires_key=True,
        env_var="ANTHROPIC_API_KEY",
    ),
]


def get_model_by_id(model_id: str) -> ModelInfo | None:
    """Look up a model by its LiteLLM model ID."""
    for m in MODELS:
        if m.id == model_id:
            return m
    return None


def infer_provider(model_id: str) -> str:
    """Infer the provider from a model ID string."""
    if model_id.startswith("ollama/"):
        return "ollama"
    if model_id.startswith("anthropic/") or model_id.startswith("claude"):
        return "anthropic"
    # Ollama models use name:tag ids (gemma3:12b, mistral:7b); cloud ids don't
    if ":" in model_id:
        return "ollama"
    # Default: bare model names like gpt-4o are OpenAI
    return "openai"
