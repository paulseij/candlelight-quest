"""Tests for src/text_rpg/llm/context_packer.py."""
from __future__ import annotations

import pytest

from text_rpg.llm.context_packer import ContextPacker
from text_rpg.llm.token_budget import TokenBudget


@pytest.fixture
def packer():
    return ContextPacker()


@pytest.fixture
def char():
    return {
        "name": "Thorin",
        "level": 3,
        "race": "dwarf",
        "char_class": "fighter",
        "hp_current": 25,
        "hp_max": 30,
        "ac": 16,
        "conditions": [],
    }


@pytest.fixture
def location():
    return {
        "name": "Thornfield Square",
        "description": "A bustling town square with a fountain.",
        "connections": [
            {"direction": "north", "target_location_id": "market", "description": "Market District"},
        ],
    }


class TestPackActionContext:
    def test_includes_player_input(self, packer, char, location):
        ctx = packer.pack_action_context("go north", char, location, ["move", "attack"])
        assert 'Player input: "go north"' in ctx

    def test_includes_character_info(self, packer, char, location):
        ctx = packer.pack_action_context("test", char, location, ["move"])
        assert "Thorin" in ctx
        assert "Level 3" in ctx
        assert "dwarf" in ctx
        assert "fighter" in ctx

    def test_includes_location(self, packer, char, location):
        ctx = packer.pack_action_context("test", char, location, ["move"])
        assert "Thornfield Square" in ctx

    def test_includes_available_actions(self, packer, char, location):
        ctx = packer.pack_action_context("test", char, location, ["move", "attack", "look"])
        assert "move, attack, look" in ctx

    def test_truncates_long_description(self, packer, char):
        loc = {"name": "Place", "description": "A" * 200}
        ctx = packer.pack_action_context("test", char, loc, ["move"])
        # Description should be truncated to 100 chars
        assert len(ctx.split("Place - ")[1].split("\n")[0]) <= 100


class TestPackNarrativeContext:
    def test_includes_character_section(self, packer, char, location):
        ctx = packer.pack_narrative_context(char, location, [])
        assert "## Current Character" in ctx
        assert "Thorin" in ctx

    def test_includes_location_section(self, packer, char, location):
        ctx = packer.pack_narrative_context(char, location, [])
        assert "## Current Location" in ctx
        assert "Thornfield Square" in ctx

    def test_includes_time_when_provided(self, packer, char, location):
        ctx = packer.pack_narrative_context(char, location, [], world_time=480)
        assert "## Time" in ctx
        assert "Morning" in ctx

    def test_no_time_section_without_world_time(self, packer, char, location):
        ctx = packer.pack_narrative_context(char, location, [])
        assert "## Time" not in ctx

    def test_includes_nearby_entities(self, packer, char, location):
        entities = [{"name": "Goblin", "description": "A sneaky creature"}]
        ctx = packer.pack_narrative_context(char, location, [], nearby_entities=entities)
        assert "## Nearby" in ctx
        assert "Goblin" in ctx

    def test_includes_combat_when_active(self, packer, char, location):
        combat = {
            "is_active": True,
            "round_number": 2,
            "combatants": [{"name": "Goblin", "is_active": True, "hp": {"current": 5, "max": 7}}],
        }
        ctx = packer.pack_narrative_context(char, location, [], combat_state=combat)
        assert "## Combat" in ctx
        assert "Round 2" in ctx

    def test_no_combat_when_inactive(self, packer, char, location):
        combat = {"is_active": False}
        ctx = packer.pack_narrative_context(char, location, [], combat_state=combat)
        assert "## Combat" not in ctx

    def test_includes_recent_events(self, packer, char, location):
        events = [{"description": "You found a key"}]
        ctx = packer.pack_narrative_context(char, location, events)
        assert "## Recent Events" in ctx
        assert "found a key" in ctx

    def test_includes_rag_context(self, packer, char, location):
        rag = {
            "relevant_lore": ["The ancient temple holds secrets"],
            "past_events": ["Previously explored the forest"],
        }
        ctx = packer.pack_narrative_context(char, location, [], rag_context=rag)
        assert "## World Lore" in ctx
        assert "## Relevant History" in ctx

    def test_includes_narrator_hints(self, packer, char, location):
        ctx = packer.pack_narrative_context(char, location, [], narrator_hints=["A storm brews"])
        assert "## Ambient Details" in ctx
        assert "A storm brews" in ctx

    def test_limits_entities_to_5(self, packer, char, location):
        entities = [{"name": f"NPC_{i}", "description": "..."} for i in range(10)]
        ctx = packer.pack_narrative_context(char, location, [], nearby_entities=entities)
        # Should only include first 5
        assert "NPC_4" in ctx
        assert "NPC_5" not in ctx

    def test_limits_events_to_5(self, packer, char, location):
        events = [{"description": f"Event {i}"} for i in reversed(range(10))]
        ctx = packer.pack_narrative_context(char, location, events)
        # Ledger is newest first; retain its first five, present chronologically.
        assert "Event 9" in ctx
        assert "Event 0" not in ctx


class TestTokenBudgetIntegration:
    def test_trimming_respects_budget(self):
        budget = TokenBudget(max_context_tokens=50, chars_per_token=1.0)
        packer = ContextPacker(token_budget=budget)
        char = {"name": "A" * 100, "level": 1, "race": "x", "char_class": "y",
                "hp_current": 10, "hp_max": 10, "ac": 10, "conditions": []}
        loc = {"name": "B" * 100, "description": "C" * 100, "connections": []}
        ctx = packer.pack_narrative_context(char, loc, [])
        assert len(ctx) <= 50


class TestFormatHelpers:
    def test_format_character(self, packer):
        char = {"name": "Hero", "level": 5, "race": "elf", "char_class": "wizard",
                "hp_current": 20, "hp_max": 25, "ac": 14, "conditions": ["poisoned"]}
        result = packer._format_character(char)
        assert "Hero" in result
        assert "poisoned" in result

    def test_format_location_with_exits(self, packer):
        loc = {
            "name": "Town",
            "description": "A town.",
            "connections": [{"direction": "north", "description": "Gate"}],
        }
        result = packer._format_location(loc)
        assert "north -> Gate" in result

    def test_format_location_no_exits(self, packer):
        loc = {"name": "Cave", "description": "Dark.", "connections": []}
        result = packer._format_location(loc)
        assert "None visible" in result

    def test_format_combat(self, packer):
        combat = {
            "round_number": 3,
            "combatants": [
                {"name": "Goblin", "is_active": True, "hp": {"current": 3, "max": 7}},
            ],
        }
        result = packer._format_combat(combat)
        assert "Round 3" in result
        assert "Goblin" in result
        assert "HP:3/7" in result
