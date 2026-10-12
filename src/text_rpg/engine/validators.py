"""Validates state mutations before applying them."""
from __future__ import annotations

from text_rpg.mechanics.conditions import can_take_actions
from text_rpg.models.action import Action, StateMutation
from text_rpg.systems.base import GameContext
from text_rpg.utils import safe_json


def validate_mutations(mutations: list[StateMutation], context: GameContext) -> list[StateMutation]:
    """Filter and clamp invalid mutations."""
    validated = []
    for m in mutations:
        if m.field == "hp_current" and isinstance(m.new_value, (int, float)):
            # Clamp HP between 0 and max
            target = context.character if not m.target_id or m.target_id == context.character.get("id") else next((e for e in context.entities if e.get("id") == m.target_id), {})
            max_hp = target.get("hp_max", 100)
            m.new_value = max(0, min(int(m.new_value), max_hp))
        validated.append(m)
    return validated


def validate_action(action: Action, context: GameContext) -> tuple[bool, str]:
    """Validate whether an action can be taken."""
    if action.action_type in {"attack", "talk", "interact"} and action.target_id:
        import re
        target = re.sub(r"^(the|a|an)\s+", "", action.target_id.lower()).strip()
        exact = [e for e in context.entities if target in {e.get("id",""), e.get("name", "").lower()}]
        matches = exact or [e for e in context.entities if target and target in e.get("name", "").lower() and e.get("is_alive",True)]
        if len(matches)>1:
            return False, "Which do you mean? " + ", ".join(e.get("name", e["id"]) for e in matches)
    char = context.character
    conditions = safe_json(char.get("conditions"), [])

    if not can_take_actions(conditions):
        return False, "You are incapacitated and cannot take actions."

    if context.combat_state and context.combat_state.get("is_active"):
        turn_order = context.combat_state.get("turn_order", [])
        current_idx = context.combat_state.get("current_turn_index", 0)
        if turn_order and current_idx < len(turn_order):
            current_id = turn_order[current_idx]
            if current_id != char.get("id"):
                return False, "It's not your turn."

    return True, ""
