"""Integration tests for combat spell/item/class ability resolution."""
from __future__ import annotations

import pytest

from text_rpg.cli.input_handler import InputHandler
from text_rpg.cli.combat_display import CombatDisplay


class TestCombatInputPatterns:
    """Test that combat-related inputs classify correctly."""

    @pytest.fixture
    def handler(self):
        return InputHandler()

    def test_numbered_attack(self, handler):
        result = handler.classify("1")
        assert result["action_type"] == "attack"

    def test_numbered_spell(self, handler):
        result = handler.classify("2")
        assert result["action_type"] == "combat_spell"

    def test_numbered_item(self, handler):
        result = handler.classify("3")
        assert result["action_type"] == "combat_item"

    def test_numbered_flee(self, handler):
        result = handler.classify("4")
        assert result["action_type"] == "flee"

    def test_numbered_dodge(self, handler):
        result = handler.classify("5")
        assert result["action_type"] == "dodge"

    def test_numbered_class_ability(self, handler):
        result = handler.classify("6")
        assert result["action_type"] == "class_ability"

    @pytest.mark.parametrize("text", [
        "rage", "flurry", "flurry of blows", "stunning strike",
        "lay on hands", "wild shape", "inspire", "bardic inspiration",
    ])
    def test_class_ability_keywords(self, handler, text):
        result = handler.classify(text)
        assert result["action_type"] == "class_ability"

    def test_cast_spell_in_combat(self, handler):
        result = handler.classify("cast fire bolt at goblin")
        assert result["action_type"] == "cast_spell"
        assert result["target"] == "fire bolt"
        assert result["parameters"]["spell_target"] == "goblin"

    def test_use_item_in_combat(self, handler):
        result = handler.classify("use healing potion")
        assert result["action_type"] == "use_item"
        assert result["target"] == "healing potion"

    def test_class_ability_breaks_conversation(self, handler):
        assert handler.should_break_conversation("rage")
        assert handler.should_break_conversation("6")


class TestCombatDisplayClassAbilities:
    """Test dynamic combat menu based on class."""

    def test_barbarian_gets_rage(self):
        label = CombatDisplay._get_class_ability_label({"char_class": "barbarian"})
        assert label == "Rage"

    def test_bard_gets_inspire(self):
        label = CombatDisplay._get_class_ability_label({"char_class": "bard"})
        assert label == "Inspire"

    def test_monk_gets_flurry(self):
        label = CombatDisplay._get_class_ability_label({"char_class": "monk"})
        assert label == "Flurry"

    def test_paladin_gets_lay_on_hands(self):
        label = CombatDisplay._get_class_ability_label({"char_class": "paladin"})
        assert label == "Lay on Hands"

    def test_druid_gets_wild_shape(self):
        label = CombatDisplay._get_class_ability_label({"char_class": "druid"})
        assert label == "Wild Shape"

    def test_fighter_has_no_class_ability(self):
        label = CombatDisplay._get_class_ability_label({"char_class": "fighter"})
        assert label is None

    def test_wizard_has_no_class_ability(self):
        label = CombatDisplay._get_class_ability_label({"char_class": "wizard"})
        assert label is None

    def test_missing_class_returns_none(self):
        label = CombatDisplay._get_class_ability_label({})
        assert label is None

    def test_enemy_intent_names_attack(self):
        enemy = {"hp": {"current": 10, "max": 10}, "attacks": [{"name": "Fire Breath"}]}
        assert CombatDisplay._enemy_intent(enemy, {"ac": 16}) == "Fire Breath against you (AC 16)"

    def test_wounded_enemy_telegraphs_escape(self):
        enemy = {"hp": {"current": 2, "max": 10}, "attacks": [{"name": "Claw"}]}
        assert CombatDisplay._enemy_intent(enemy, {"ac": 12}) == "trying to escape"

    def test_enemy_intent_uses_next_attack_index(self):
        enemy = {"hp": {"current": 10, "max": 10}, "next_attack_index": 1,
                 "attacks": [{"name": "Claws"}, {"name": "Fire Breath"}]}
        assert CombatDisplay._enemy_intent(enemy, {"ac": 16}) == "Fire Breath against you (AC 16)"


class TestCombatSystemCanHandle:
    """Test CombatSystem claims spell/item actions during active combat."""

    @pytest.fixture
    def combat_system(self):
        from text_rpg.systems.combat.system import CombatSystem
        return CombatSystem()

    @pytest.fixture
    def action(self):
        from text_rpg.models.action import Action
        def _make(action_type, target=None):
            return Action(action_type=action_type, actor_id="c1", target_id=target)
        return _make

    @pytest.fixture
    def no_combat_context(self):
        from text_rpg.systems.base import GameContext
        return GameContext(
            game_id="g1", character={"id": "c1"}, location={},
            entities=[], combat_state=None, inventory=None,
            recent_events=[], turn_number=0, world_time=480,
        )

    @pytest.fixture
    def active_combat_context(self):
        from text_rpg.systems.base import GameContext
        return GameContext(
            game_id="g1", character={"id": "c1"}, location={},
            entities=[], combat_state={"is_active": True, "combatants": []},
            inventory=None, recent_events=[], turn_number=0, world_time=480,
        )

    def test_always_handles_attack(self, combat_system, action, no_combat_context):
        assert combat_system.can_handle(action("attack"), no_combat_context)

    def test_always_handles_combat_spell(self, combat_system, action, no_combat_context):
        assert combat_system.can_handle(action("combat_spell"), no_combat_context)

    def test_does_not_handle_cast_spell_outside_combat(self, combat_system, action, no_combat_context):
        assert not combat_system.can_handle(action("cast_spell"), no_combat_context)

    def test_does_not_handle_use_item_outside_combat(self, combat_system, action, no_combat_context):
        assert not combat_system.can_handle(action("use_item"), no_combat_context)

    def test_handles_cast_spell_during_combat(self, combat_system, action, active_combat_context):
        assert combat_system.can_handle(action("cast_spell"), active_combat_context)

    def test_handles_use_item_during_combat(self, combat_system, action, active_combat_context):
        assert combat_system.can_handle(action("use_item"), active_combat_context)

    def test_handles_class_ability(self, combat_system, action, active_combat_context):
        assert combat_system.can_handle(action("class_ability"), active_combat_context)
