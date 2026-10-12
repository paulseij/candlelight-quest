"""Pydantic schemas for structured LLM outputs.

Passed as the `schema` argument to LLMProvider.generate_structured, these are
both the native response_format sent to providers that support json_schema and
the validation gate for everything else.
"""
from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Literal


class LocationProposal(BaseModel):
    """Small scene contract; identity, exits and rewards belong to the engine."""
    model_config = {"extra": "forbid"}
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=700)
    location_type: Literal["wilderness", "road", "forest", "mountain", "ruins", "cave"]
    atmosphere: str = Field(max_length=150)
    discovery: str = Field(min_length=1, max_length=250, description="A physical detail a search can reveal; no creatures, treasure, quests or exits.")


class ActionClassification(BaseModel):
    """LLM fallback classification of unrecognized player input."""
    action_type: str
    target: str | None = None
    parameters: dict = Field(default_factory=dict)
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)


class PlausibilityAssessment(BaseModel):
    """How plausible a freeform player action is, and how to adjudicate it.

    Deliberately contains NO outcome prose: outcomes are generated after the
    roll, from effects the engine actually applies.
    """
    plausibility: float = Field(gt=0.0, le=1.0)
    skill: str = "athletics"
    ability: str = "strength"
    reasoning: str = ""


class ProposedEffect(BaseModel):
    """One typed effect proposed for a creative action outcome."""
    type: str
    params: dict = Field(default_factory=dict)


class CreativeOutcome(BaseModel):
    """Post-roll outcome proposal: a factual summary plus typed effects.

    Effects are validated against a closed menu and budget by
    mechanics.creative_effects before anything is applied.
    """
    summary: str
    effects: list[ProposedEffect] = Field(default_factory=list)


class SpellProposal(BaseModel):
    """LLM evaluation of a player-invented spell."""
    name: str
    description: str = "A mysterious magical effect."
    level: int = Field(ge=0, le=6, default=1)
    school: str = "evocation"
    plausibility: float = Field(gt=0.0, le=1.0, default=0.5)
    elements: list[str] = Field(default_factory=list)
    mechanics: dict = Field(default_factory=dict)
    reasoning: str = ""


class ItemProposal(BaseModel):
    """LLM evaluation of a player-invented item."""
    name: str
    description: str = "A curious handcrafted object."
    item_type: str = "misc"
    rarity: str = "common"
    plausibility: float = Field(gt=0.0, le=1.0, default=0.5)
    effects: list[ProposedEffect] = Field(default_factory=list)
    reasoning: str = ""
