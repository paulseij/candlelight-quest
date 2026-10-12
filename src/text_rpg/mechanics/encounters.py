"""Random encounter rolling and instantiation — pure functions, rng injected."""
from __future__ import annotations

import random
from typing import Any

# Global damper on authored encounter_chance values so travel doesn't
# devolve into a grind; tune upward once pacing feels right.
ENCOUNTER_CHANCE_DAMPER = 0.5


def roll_encounter(
    encounters: list[dict],
    location_id: str,
    rng: random.Random | None = None,
) -> dict | None:
    """Roll for a random encounter at this location. Returns the encounter or None."""
    rng = rng or random.Random()
    eligible = [
        enc for enc in encounters
        if location_id in (enc.get("location_ids") or [])
    ]
    for enc in eligible:
        chance = float(enc.get("encounter_chance") or 0.0) * ENCOUNTER_CHANCE_DAMPER
        if rng.random() < chance:
            return enc
    return None


def instantiate_encounter(
    encounter: dict,
    game_id: str,
    location_id: str,
    rng: random.Random | None = None,
) -> list[dict]:
    """Build concrete hostile entity dicts (ready for the entity repo) from an encounter."""
    rng = rng or random.Random()
    entities: list[dict] = []

    for template in encounter.get("entities", []):
        count_min = int(template.get("count_min") or 1)
        count_max = int(template.get("count_max") or count_min)
        count = rng.randint(count_min, max(count_min, count_max))
        base_id = template.get("id", "enemy")
        hp = int(template.get("hp_max") or 10)

        for _ in range(count):
            suffix = f"{rng.randrange(16**6):06x}"
            entities.append({
                "id": f"{base_id}_{suffix}",
                "game_id": game_id,
                "name": template.get("name", base_id.replace("_", " ").title()),
                "entity_type": template.get("entity_type", "enemy"),
                "description": template.get("description", ""),
                "ability_scores": template.get("ability_scores", {}),
                "hp_current": hp,
                "hp_max": hp,
                "hp_temp": 0,
                "ac": int(template.get("ac") or 10),
                "speed": int(template.get("speed") or 30),
                "level": int(template.get("level") or 1),
                "challenge_rating": template.get("challenge_rating"),
                "attacks": template.get("attacks", []),
                "behaviors": template.get("behaviors", []),
                "dialogue_tags": [],
                "location_id": location_id,
                "loot_table": _loot_table(encounter, rng),
                "is_hostile": True,
                "is_alive": True,
                "properties": {
                    "generated": True,
                    "encounter_id": encounter.get("id", ""),
                    "xp_value": int(template.get("xp_value") or 25),
                },
            })

    return entities


def _loot_table(encounter: dict, rng: random.Random) -> list[dict]:
    """Roll the encounter's loot table into concrete per-entity drops."""
    loot = encounter.get("loot", {})
    drops: list[dict] = []
    for item in loot.get("items", []):
        if rng.random() < float(item.get("chance") or 0.0):
            drops.append({"item_id": item.get("id", ""), "quantity": 1})
    gold_min = int(loot.get("gold_min") or 0)
    gold_max = int(loot.get("gold_max") or 0)
    if gold_max > 0:
        gold = rng.randint(gold_min, max(gold_min, gold_max))
        if gold > 0:
            drops.append({"gold": gold})
    return drops
