"""Tests for src/text_rpg/mechanics/item_invention.py."""
from __future__ import annotations

from text_rpg.mechanics.item_invention import (
    calculate_item_dc,
    generate_crafting_mishap,
    max_rarity_for_level,
    validate_item_proposal,
)


class TestRarityGates:
    def test_max_rarity_by_level(self):
        assert max_rarity_for_level(1) == "common"
        assert max_rarity_for_level(3) == "uncommon"
        assert max_rarity_for_level(6) == "rare"

    def test_crafting_skill_substitutes_for_level(self):
        assert max_rarity_for_level(1, crafting_skill_level=3) == "uncommon"
        assert max_rarity_for_level(1, crafting_skill_level=6) == "rare"

    def test_rarity_beyond_skill_rejected(self):
        proposal = {"rarity": "rare", "effects": []}
        valid, reason = validate_item_proposal(proposal, character_level=1)
        assert not valid
        assert "beyond your skill" in reason

    def test_unknown_rarity_downgraded_to_common(self):
        proposal = {"rarity": "legendary", "effects": []}
        valid, _ = validate_item_proposal(proposal, character_level=1)
        assert valid
        assert proposal["rarity"] == "common"


class TestEffectBudgets:
    def test_unknown_effects_removed(self):
        proposal = {"rarity": "common", "effects": [
            {"type": "instant_death", "params": {}},
            {"type": "light", "params": {}},
        ]}
        valid, _ = validate_item_proposal(proposal, character_level=1)
        assert valid
        assert [e["type"] for e in proposal["effects"]] == ["light"]

    def test_common_cannot_grant_ac(self):
        proposal = {"rarity": "common", "effects": [
            {"type": "ac_bonus", "params": {"amount": 2}},
        ]}
        validate_item_proposal(proposal, character_level=1)
        assert proposal["effects"] == []

    def test_ac_clamped_by_rarity(self):
        proposal = {"rarity": "rare", "effects": [
            {"type": "ac_bonus", "params": {"amount": 5}},
        ]}
        validate_item_proposal(proposal, character_level=10)
        assert proposal["effects"][0]["params"]["amount"] == 2

    def test_damage_dice_clamped_by_rarity(self):
        proposal = {"rarity": "common", "effects": [
            {"type": "damage_dice", "params": {"dice": "3d8"}},
        ]}
        validate_item_proposal(proposal, character_level=1)
        assert proposal["effects"][0]["params"]["dice"] == "1d4"

    def test_budget_enforced(self):
        # common budget = 2: damage_dice (2) fits, second costed effect doesn't
        proposal = {"rarity": "common", "effects": [
            {"type": "damage_dice", "params": {"dice": "1d4"}},
            {"type": "consumable_heal", "params": {"dice": "2d4"}},
        ]}
        validate_item_proposal(proposal, character_level=1)
        assert [e["type"] for e in proposal["effects"]] == ["damage_dice"]

    def test_utility_text_free(self):
        proposal = {"rarity": "common", "effects": [
            {"type": "damage_dice", "params": {"dice": "1d4"}},
            {"type": "utility_text", "params": {"text": "opens bottles"}},
        ]}
        validate_item_proposal(proposal, character_level=1)
        assert len(proposal["effects"]) == 2


class TestDC:
    def test_rarity_raises_dc(self):
        common = calculate_item_dc(0.5, "common", None, False)
        rare = calculate_item_dc(0.5, "rare", None, False)
        assert rare == common + 14

    def test_location_and_tools_lower_dc(self):
        base = calculate_item_dc(0.5, "common", None, False)
        forge = calculate_item_dc(0.5, "common", "forge", True)
        assert forge == base - 6

    def test_clamped(self):
        assert calculate_item_dc(0.001, "rare", None, False) <= 45
        assert calculate_item_dc(1.0, "common", "forge", True) >= 5


class TestMishaps:
    def test_minor_mishap_only_loses_materials(self):
        mishap = generate_crafting_mishap("common", 3)
        assert mishap.materials_lost is True
        assert mishap.damage_to_crafter == 0
        assert mishap.cursed_quirk is None

    def test_moderate_mishap_damages(self):
        mishap = generate_crafting_mishap("common", 8)
        assert 1 <= mishap.damage_to_crafter <= 4
        assert mishap.cursed_quirk is None

    def test_severe_mishap_produces_cursed_item(self):
        mishap = generate_crafting_mishap("rare", 15)
        assert mishap.cursed_quirk is not None
        assert mishap.materials_lost is False
        assert mishap.damage_to_crafter > 0
