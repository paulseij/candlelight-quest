"""Tests for src/text_rpg/mechanics/creative_effects.py."""
from __future__ import annotations

from text_rpg.mechanics.creative_effects import (
    WorldFacts,
    creative_budget,
    effect_menu_text,
    effects_to_mutations,
    effects_to_summary,
    validate_effects,
)


FACTS = WorldFacts(
    catalog_item_ids={"rope", "healing_potion"},
    location_item_ids={"old_crate", "rope"},
    inventory_item_ids={"torch"},
    present_entity_ids={"guard_1"},
    existing_exit_directions={"north"},
)


class TestValidateEffects:
    def test_unknown_effect_type_dropped(self):
        kept, reasons = validate_effects(
            [{"type": "grant_wish", "params": {}}], FACTS, 1, True)
        assert kept == []
        assert any("unknown effect type" in r for r in reasons)

    def test_unknown_item_dropped(self):
        kept, reasons = validate_effects(
            [{"type": "gain_item", "params": {"item_id": "vorpal_sword"}}], FACTS, 5, True)
        assert kept == []
        assert any("unknown item" in r for r in reasons)

    def test_known_items_kept_and_quantity_clamped(self):
        kept, _ = validate_effects(
            [{"type": "gain_item", "params": {"item_id": "rope", "quantity": 99}}],
            FACTS, 5, True)
        assert kept[0]["params"]["quantity"] == 1

    def test_location_item_allowed(self):
        kept, _ = validate_effects(
            [{"type": "gain_item", "params": {"item_id": "old_crate"}}], FACTS, 1, True)
        assert len(kept) == 1

    def test_gold_clamped_to_level_cap(self):
        kept, _ = validate_effects(
            [{"type": "gain_gold", "params": {"amount": 5000}}], FACTS, 3, True)
        assert kept[0]["params"]["amount"] == 30

    def test_hp_clamped(self):
        kept, _ = validate_effects(
            [{"type": "hp_change", "params": {"amount": -999}}], FACTS, 4, True)
        assert kept[0]["params"]["amount"] == -8

    def test_failed_roll_strips_benefits(self):
        effects = [
            {"type": "gain_gold", "params": {"amount": 10}},
            {"type": "gain_item", "params": {"item_id": "rope"}},
            {"type": "hp_change", "params": {"amount": 3}},
            {"type": "lose_gold", "params": {"amount": 5}},
            {"type": "hp_change", "params": {"amount": -2}},
        ]
        kept, reasons = validate_effects(effects, FACTS, 5, False)
        kept_types = [(e["type"], e["params"].get("amount")) for e in kept]
        assert ("lose_gold", 5) in kept_types
        assert ("hp_change", -2) in kept_types
        assert len(kept) == 2

    def test_budget_enforced(self):
        # Level 1 budget = 2 -> only one cost-2 effect fits.
        effects = [
            {"type": "gain_gold", "params": {"amount": 5}},
            {"type": "gain_item", "params": {"item_id": "rope"}},
        ]
        kept, reasons = validate_effects(effects, FACTS, 1, True)
        assert len(kept) == 1
        assert any("over budget" in r for r in reasons)

    def test_narrative_only_always_free(self):
        kept, _ = validate_effects(
            [{"type": "narrative_only", "params": {}}] * 3, FACTS, 1, True)
        assert len(kept) == 1

    def test_disposition_requires_present_entity(self):
        kept, _ = validate_effects(
            [{"type": "disposition_change", "params": {"entity_id": "ghost", "delta": 5}}],
            FACTS, 5, True)
        assert kept == []
        kept, _ = validate_effects(
            [{"type": "disposition_change", "params": {"entity_id": "guard_1", "delta": 50}}],
            FACTS, 5, True)
        assert kept[0]["params"]["delta"] == 20

    def test_reveal_exit_rejects_existing_direction(self):
        kept, _ = validate_effects(
            [{"type": "reveal_exit", "params": {"direction": "north"}}], FACTS, 5, True)
        assert kept == []
        kept, _ = validate_effects(
            [{"type": "reveal_exit", "params": {"direction": "east"}}], FACTS, 5, True)
        assert len(kept) == 1

    def test_lose_item_requires_ownership(self):
        kept, _ = validate_effects(
            [{"type": "lose_item", "params": {"item_id": "rope"}}], FACTS, 1, True)
        assert kept == []
        kept, _ = validate_effects(
            [{"type": "lose_item", "params": {"item_id": "torch"}}], FACTS, 1, True)
        assert len(kept) == 1


class TestEffectsToMutations:
    CHARACTER = {"id": "char1", "gold": 50, "hp_current": 10, "conditions": []}

    def test_gold_and_item_mutation_shapes(self):
        effects = [
            {"type": "gain_gold", "params": {"amount": 10}},
            {"type": "gain_item", "params": {"item_id": "rope", "quantity": 2}},
            {"type": "lose_item", "params": {"item_id": "torch"}},
        ]
        muts = effects_to_mutations(effects, self.CHARACTER, {})
        gold = next(m for m in muts if m.field == "gold")
        assert (gold.target_type, gold.old_value, gold.new_value) == ("character", 50, 60)
        add = next(m for m in muts if m.field == "items_add")
        assert add.target_type == "inventory"
        assert add.new_value == {"item_id": "rope", "quantity": 2}
        rem = next(m for m in muts if m.field == "items_remove_one")
        assert rem.new_value == "torch"

    def test_gold_cannot_go_negative(self):
        muts = effects_to_mutations(
            [{"type": "lose_gold", "params": {"amount": 500}}], self.CHARACTER, {})
        assert muts[0].new_value == 0

    def test_sequential_gold_effects_chain(self):
        effects = [
            {"type": "gain_gold", "params": {"amount": 10}},
            {"type": "lose_gold", "params": {"amount": 5}},
        ]
        muts = effects_to_mutations(effects, self.CHARACTER, {})
        assert muts[-1].new_value == 55

    def test_condition_add_and_remove(self):
        char = {"id": "c", "gold": 0, "hp_current": 5, "conditions": ["poisoned"]}
        muts = effects_to_mutations(
            [{"type": "condition_remove", "params": {"condition": "poisoned"}}], char, {})
        assert muts[0].new_value == []

    def test_disposition_updates_entity_properties(self):
        entities = {"guard_1": {"id": "guard_1", "properties": {}}}
        muts = effects_to_mutations(
            [{"type": "disposition_change", "params": {"entity_id": "guard_1", "delta": -10}}],
            self.CHARACTER, entities)
        assert muts[0].target_type == "entity"
        assert muts[0].new_value["relationships"]["player"]["disposition"] == -10


class TestSummary:
    def test_summary_is_factual(self):
        effects = [
            {"type": "gain_gold", "params": {"amount": 12}},
            {"type": "gain_item", "params": {"item_id": "rope", "quantity": 1}},
        ]
        text = effects_to_summary(effects, {"rope": "Hempen Rope"})
        assert "12 gold" in text
        assert "Hempen Rope" in text


def test_budget_scales_with_level():
    assert creative_budget(1) == 2
    assert creative_budget(9) == 5


def test_menu_text_mentions_all_types():
    menu = effect_menu_text(3)
    for etype in ("gain_item", "gain_gold", "hp_change", "reveal_exit", "narrative_only"):
        assert etype in menu
