"""Closed-vocabulary effect menu for creative (freeform) actions.

Mirrors the trait_effects pattern: the LLM proposes typed effects from this
menu, the engine validates them against a budget and the actual world state,
and only validated effects are applied — narration then describes what really
happened. Pure functions, no I/O.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from text_rpg.mechanics.conditions import Condition
from text_rpg.models.action import StateMutation
from text_rpg.utils import safe_json

# Effect type -> budget cost. Effects that only hurt or constrain the player
# are free; benefits consume budget so a single action can't shower rewards.
EFFECT_COSTS: dict[str, int] = {
    "narrative_only": 0,
    "gain_item": 2,
    "lose_item": 0,
    "gain_gold": 2,
    "lose_gold": 0,
    "hp_change": 1,       # cost applies to healing; damage is free
    "condition_add": 0,
    "condition_remove": 2,
    "disposition_change": 1,  # cost applies to positive deltas; negative free
    "reveal_exit": 2,
    "spawn_entity": 2,
}

# Effects that benefit the player — stripped when the roll failed.
_BENEFICIAL = {"gain_item", "gain_gold", "condition_remove", "reveal_exit"}

_KNOWN_CONDITIONS = {c.value for c in Condition}

_DIRECTIONS = {
    "north", "south", "east", "west", "northeast", "northwest",
    "southeast", "southwest", "up", "down",
}


@dataclass
class WorldFacts:
    """Allowed-id sets the validator checks proposed effects against."""
    catalog_item_ids: set[str] = field(default_factory=set)
    location_item_ids: set[str] = field(default_factory=set)
    inventory_item_ids: set[str] = field(default_factory=set)
    present_entity_ids: set[str] = field(default_factory=set)
    existing_exit_directions: set[str] = field(default_factory=set)
    allowed_effects: set[str] | None = None


def creative_budget(level: int) -> int:
    """Total effect budget for one creative action at a given level."""
    return 2 + level // 3


def effect_menu_text(level: int) -> str:
    """Render the effect menu for the LLM prompt, with live caps."""
    gold_cap = 10 * max(1, level)
    hp_cap = 2 * max(1, level)
    return (
        f"- narrative_only (cost 0): no mechanical change. params: {{}}\n"
        f"- gain_item (cost 2): params {{\"item_id\": <existing item id>, \"quantity\": 1-3}}\n"
        f"- lose_item (cost 0): params {{\"item_id\": <item id from the player's inventory>}}\n"
        f"- gain_gold (cost 2): params {{\"amount\": 1-{gold_cap}}}\n"
        f"- lose_gold (cost 0): params {{\"amount\": <gold lost>}}\n"
        f"- hp_change (cost 1 if healing, 0 if damage): params {{\"amount\": -{hp_cap} to {hp_cap}}}\n"
        f"- condition_add (cost 0): params {{\"condition\": <condition on the player>}}\n"
        f"- condition_remove (cost 2): params {{\"condition\": <condition removed from the player>}}\n"
        f"- disposition_change (cost 1 if positive, 0 if negative): params {{\"entity_id\": <present entity id>, \"delta\": -20 to 20}}\n"
        f"- reveal_exit (cost 2): params {{\"direction\": <compass direction with no existing exit>}}\n"
        f"- spawn_entity (cost 2): params {{\"name\": <name>, \"description\": <one line>, \"is_hostile\": true/false}}\n"
    )


def _effect_cost(etype: str, params: dict) -> int:
    cost = EFFECT_COSTS.get(etype, 99)
    if etype == "hp_change" and _as_int(params.get("amount")) < 0:
        return 0
    if etype == "disposition_change" and _as_int(params.get("delta")) < 0:
        return 0
    return cost


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def validate_effects(
    effects: list[dict],
    facts: WorldFacts,
    level: int,
    roll_succeeded: bool,
) -> tuple[list[dict], list[str]]:
    """Filter and clamp proposed effects. Returns (kept_effects, drop_reasons)."""
    kept: list[dict] = []
    reasons: list[str] = []
    budget = creative_budget(level)
    spent = 0
    gold_cap = 10 * max(1, level)
    hp_cap = 2 * max(1, level)

    for effect in effects[:8]:
        if not isinstance(effect, dict):
            continue
        etype = str(effect.get("type", "")).strip()
        params = dict(effect.get("params") or {}) if isinstance(effect.get("params") or {}, dict) else {}
        if not isinstance(params, dict):
            params = {}

        if facts.allowed_effects is not None and etype not in facts.allowed_effects:
            reasons.append(f"{etype}: no causal permission for this action")
            continue
        if any(e["type"] == etype for e in kept):
            reasons.append(f"{etype}: duplicate effect")
            continue

        if etype not in EFFECT_COSTS:
            reasons.append(f"unknown effect type '{etype}'")
            continue

        if not roll_succeeded and etype in _BENEFICIAL:
            reasons.append(f"'{etype}' not allowed on a failed roll")
            continue

        if etype == "gain_item":
            item_id = str(params.get("item_id", ""))
            if item_id not in facts.location_item_ids:
                reasons.append(f"gain_item: unknown item '{item_id}'")
                continue
            params["quantity"] = 1
        elif etype == "lose_item":
            item_id = str(params.get("item_id", ""))
            if item_id not in facts.inventory_item_ids:
                reasons.append(f"lose_item: '{item_id}' not in inventory")
                continue
        elif etype == "gain_gold":
            amount = _as_int(params.get("amount"))
            if amount <= 0:
                reasons.append("gain_gold: non-positive amount")
                continue
            params["amount"] = min(amount, gold_cap)
        elif etype == "lose_gold":
            amount = _as_int(params.get("amount"))
            if amount <= 0:
                reasons.append("lose_gold: non-positive amount")
                continue
            params["amount"] = amount
        elif etype == "hp_change":
            amount = _as_int(params.get("amount"))
            if amount == 0:
                reasons.append("hp_change: zero amount")
                continue
            if not roll_succeeded and amount > 0:
                reasons.append("hp_change: healing not allowed on a failed roll")
                continue
            params["amount"] = max(-hp_cap, min(hp_cap, amount))
        elif etype in ("condition_add", "condition_remove"):
            condition = str(params.get("condition", "")).strip().lower()
            if condition not in _KNOWN_CONDITIONS:
                reasons.append(f"{etype}: unknown condition '{condition}'")
                continue
            params["condition"] = condition
        elif etype == "disposition_change":
            entity_id = str(params.get("entity_id", ""))
            if entity_id not in facts.present_entity_ids:
                reasons.append(f"disposition_change: entity '{entity_id}' not present")
                continue
            delta = _as_int(params.get("delta"))
            if delta == 0:
                reasons.append("disposition_change: zero delta")
                continue
            if not roll_succeeded and delta > 0:
                reasons.append("disposition_change: positive delta not allowed on a failed roll")
                continue
            params["delta"] = max(-20, min(20, delta))
        elif etype == "reveal_exit":
            direction = str(params.get("direction", "")).strip().lower()
            if direction not in _DIRECTIONS:
                reasons.append(f"reveal_exit: invalid direction '{direction}'")
                continue
            if direction in facts.existing_exit_directions:
                reasons.append(f"reveal_exit: exit '{direction}' already exists")
                continue
            params["direction"] = direction
        elif etype == "spawn_entity":
            name = str(params.get("name", "")).strip()
            if not name:
                reasons.append("spawn_entity: missing name")
                continue
            if not roll_succeeded and not params.get("is_hostile"):
                reasons.append("spawn_entity: only hostile spawns allowed on a failed roll")
                continue

        cost = _effect_cost(etype, params)
        if spent + cost > budget:
            reasons.append(f"'{etype}' dropped: over budget ({spent + cost} > {budget})")
            continue
        spent += cost
        kept.append({"type": etype, "params": params})

    return kept, reasons


def effects_to_mutations(
    effects: list[dict],
    character: dict,
    entities: dict[str, dict],
) -> list[StateMutation]:
    """Convert validated effects into StateMutations the turn loop can apply.

    reveal_exit and spawn_entity are handled by the Director (they create
    content, not field mutations) and are skipped here.
    """
    char_id = character.get("id", "")
    gold = _as_int(character.get("gold"))
    hp = _as_int(character.get("hp_current"))
    conditions = list(safe_json(character.get("conditions"), []))
    mutations: list[StateMutation] = []

    for effect in effects:
        etype = effect["type"]
        params = effect.get("params", {})

        if etype == "gain_item":
            mutations.append(StateMutation(
                target_type="inventory", target_id=char_id, field="items_add",
                new_value={"item_id": params["item_id"], "quantity": params.get("quantity", 1)},
            ))
        elif etype == "lose_item":
            mutations.append(StateMutation(
                target_type="inventory", target_id=char_id, field="items_remove_one",
                new_value=params["item_id"],
            ))
        elif etype == "gain_gold":
            new_gold = gold + params["amount"]
            mutations.append(StateMutation(
                target_type="character", target_id=char_id, field="gold",
                old_value=gold, new_value=new_gold,
            ))
            gold = new_gold
        elif etype == "lose_gold":
            new_gold = max(0, gold - params["amount"])
            mutations.append(StateMutation(
                target_type="character", target_id=char_id, field="gold",
                old_value=gold, new_value=new_gold,
            ))
            gold = new_gold
        elif etype == "hp_change":
            new_hp = hp + params["amount"]
            mutations.append(StateMutation(
                target_type="character", target_id=char_id, field="hp_current",
                old_value=hp, new_value=new_hp,
            ))
            hp = new_hp
        elif etype == "condition_add":
            if params["condition"] not in conditions:
                new_conditions = conditions + [params["condition"]]
                mutations.append(StateMutation(
                    target_type="character", target_id=char_id, field="conditions",
                    old_value=list(conditions), new_value=new_conditions,
                ))
                conditions = new_conditions
        elif etype == "condition_remove":
            if params["condition"] in conditions:
                new_conditions = [c for c in conditions if c != params["condition"]]
                mutations.append(StateMutation(
                    target_type="character", target_id=char_id, field="conditions",
                    old_value=list(conditions), new_value=new_conditions,
                ))
                conditions = new_conditions
        elif etype == "disposition_change":
            entity = entities.get(params["entity_id"])
            if not entity:
                continue
            props = dict(safe_json(entity.get("properties"), {}))
            relationships = dict(props.get("relationships", {}))
            player_rel = dict(relationships.get("player", {}))
            old_disposition = _as_int(player_rel.get("disposition"))
            player_rel["disposition"] = max(-100, min(100, old_disposition + params["delta"]))
            relationships["player"] = player_rel
            props["relationships"] = relationships
            mutations.append(StateMutation(
                target_type="entity", target_id=params["entity_id"], field="properties",
                old_value=entity.get("properties"), new_value=props,
            ))

    return mutations


def effects_to_summary(
    effects: list[dict],
    item_names: dict[str, str] | None = None,
    entity_names: dict[str, str] | None = None,
) -> str:
    """Build a factual one-line summary of the applied effects."""
    item_names = item_names or {}
    entity_names = entity_names or {}

    def item_name(item_id: str) -> str:
        return item_names.get(item_id, item_id.replace("_", " ").title())

    parts: list[str] = []
    for effect in effects:
        etype = effect["type"]
        params = effect.get("params", {})
        if etype == "gain_item":
            qty = params.get("quantity", 1)
            prefix = f"{qty}x " if qty > 1 else ""
            parts.append(f"Gained {prefix}{item_name(params['item_id'])}.")
        elif etype == "lose_item":
            parts.append(f"Lost {item_name(params['item_id'])}.")
        elif etype == "gain_gold":
            parts.append(f"Gained {params['amount']} gold.")
        elif etype == "lose_gold":
            parts.append(f"Lost {params['amount']} gold.")
        elif etype == "hp_change":
            amount = params["amount"]
            parts.append(f"Recovered {amount} HP." if amount > 0 else f"Took {-amount} damage.")
        elif etype == "condition_add":
            parts.append(f"You are now {params['condition']}.")
        elif etype == "condition_remove":
            parts.append(f"No longer {params['condition']}.")
        elif etype == "disposition_change":
            name = entity_names.get(params["entity_id"], "Someone")
            delta = params["delta"]
            parts.append(
                f"{name}'s opinion of you {'improves' if delta > 0 else 'worsens'}."
            )
        elif etype == "reveal_exit":
            parts.append(f"A way {params['direction']} is revealed.")
        elif etype == "spawn_entity":
            parts.append(f"{params.get('name', 'Something')} appears.")
    return " ".join(parts)
