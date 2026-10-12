"""Seed authored region content (TOML) into a game's database.

Used at new-game time for the starting region and lazily whenever the player
first reaches any of the other authored regions. Idempotent: rows whose ids
already exist are skipped, so seeding a region twice is safe.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from text_rpg.content.loader import load_region

logger = logging.getLogger(__name__)

_ENTRY_TYPES = ("town", "village", "settlement", "city")


def seed_region(
    region_id: str,
    game_id: str,
    repos: dict[str, Any],
    visited_location_id: str | None = None,
) -> dict:
    """Seed one authored region into the DB for this game.

    Returns {"region": region_data, "entry_location_id": str, "seeded": bool}.
    """
    region_data = load_region(region_id)

    existing_region = repos["world_state"].get_region(region_id, game_id)
    if not existing_region:
        repos["world_state"].save_region({
            "id": region_data["id"],
            "game_id": game_id,
            "name": region_data["name"],
            "description": region_data["description"],
            "locations": json.dumps([loc["id"] for loc in region_data.get("locations", [])]),
            "level_range_min": region_data.get("level_range_min", 1),
            "level_range_max": region_data.get("level_range_max", 5),
            "climate": region_data.get("climate", "temperate"),
            "faction": region_data.get("faction"),
        })

    seeded_anything = not existing_region

    # Locations + connection rows
    for loc in region_data.get("locations", []):
        if repos["location"].get(loc["id"], game_id):
            continue
        seeded_anything = True
        repos["location"].save({
            "id": loc["id"],
            "game_id": game_id,
            "name": loc["name"],
            "region_id": region_id,
            "description": loc["description"],
            "location_type": loc.get("location_type", "wilderness"),
            "connections": json.dumps(loc.get("connections", [])),
            "entities": json.dumps(loc.get("entities", [])),
            "items": json.dumps(loc.get("items", [])),
            "visited": loc["id"] == visited_location_id,
            "properties": json.dumps(loc.get("properties", {})),
        })
        for c in loc.get("connections", []):
            if isinstance(c, dict) and c.get("target_location_id") and c.get("direction"):
                try:
                    repos["connection"].add_connection(
                        game_id=game_id,
                        source_id=loc["id"],
                        target_id=c["target_location_id"],
                        direction=c["direction"],
                        description=c.get("description", ""),
                        is_locked=c.get("is_locked", False),
                    )
                except Exception:
                    pass  # UNIQUE constraint — connection already present

    # Each save owns its authored NPC state.
    default_location = _entry_location(region_data)
    for npc in region_data.get("npcs", []):
        existing_npc = repos["entity"].get(npc["id"], game_id)
        if existing_npc and existing_npc.get("game_id") == game_id:
            continue
        seeded_anything = True
        repos["entity"].save({
            "id": npc["id"],
            "game_id": game_id,
            "name": npc["name"],
            "entity_type": npc.get("entity_type", "npc"),
            "description": npc.get("description", ""),
            "ability_scores": json.dumps(npc.get("ability_scores", {})),
            "hp_current": npc.get("hp_current", 10),
            "hp_max": npc.get("hp_max", 10),
            "hp_temp": 0,
            "ac": npc.get("ac", 10),
            "speed": npc.get("speed", 30),
            "level": npc.get("level", 1),
            "challenge_rating": npc.get("challenge_rating"),
            "attacks": json.dumps(npc.get("attacks", [])),
            "behaviors": json.dumps(npc.get("behaviors", [])),
            "dialogue_tags": json.dumps(npc.get("dialogue_tags", [])),
            "location_id": npc.get("location_id", default_location),
            "loot_table": json.dumps(npc.get("loot_table", [])),
            "is_hostile": npc.get("is_hostile", False),
            "is_alive": npc.get("is_alive", True),
            "profession": npc.get("profession"),
            "schedule": json.dumps(npc.get("schedule")) if npc.get("schedule") else None,
            "unavailable_periods": json.dumps(npc.get("unavailable_periods", [])),
            "properties": json.dumps(npc.get("properties", {})),
        })

    # Populated locations receive a modest stable supporting population. They
    # are real persistent NPCs, but have no implicit quests, rewards, or powers.
    from text_rpg.content.ambient_population import ensure_ambient_population
    if ensure_ambient_population(game_id, region_data.get("locations", []), repos["entity"]):
        seeded_anything = True

    # Quests
    for quest in region_data.get("quests", []):
        if repos["world_state"].get_quest(quest["id"], game_id):
            continue
        seeded_anything = True
        repos["world_state"].save_quest({
            "id": quest["id"],
            "game_id": game_id,
            "name": quest["name"],
            "description": quest["description"],
            "quest_giver_id": quest.get("quest_giver_id"),
            "status": quest.get("status", "available"),
            "objectives": json.dumps(quest.get("objectives", [])),
            "xp_reward": quest.get("xp_reward", 0),
            "item_rewards": json.dumps(quest.get("item_rewards", [])),
            "gold_reward": quest.get("gold_reward", 0),
            "level_requirement": quest.get("level_requirement", 1),
        })

    # Shops
    for shop in region_data.get("shops", []):
        existing_shop = repos["shop"].get_shop(shop["id"], game_id)
        if existing_shop and existing_shop.get("game_id") == game_id:
            continue
        seeded_anything = True
        repos["shop"].save_shop({
            "id": shop["id"],
            "game_id": game_id,
            "owner_entity_id": shop["owner_entity_id"],
            "location_id": shop["location_id"],
            "shop_type": shop.get("shop_type", "general"),
            "stock": shop.get("stock", []),
            "gold_reserve": shop.get("gold_reserve", 500),
            "price_modifier": shop.get("price_modifier", 1.0),
            "restock_turn": 0,
        })

    if seeded_anything:
        logger.info(f"Seeded region '{region_id}' for game {game_id}")

    return {
        "region": region_data,
        "entry_location_id": _entry_location(region_data),
        "seeded": seeded_anything,
    }


def _entry_location(region_data: dict) -> str:
    """First settlement-type location, else the first location."""
    locations = region_data.get("locations", [])
    for loc in locations:
        if loc.get("location_type") in _ENTRY_TYPES:
            return loc["id"]
    return locations[0]["id"] if locations else ""
