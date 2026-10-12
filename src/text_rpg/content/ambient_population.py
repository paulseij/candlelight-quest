"""Deterministic background residents for inhabited authored locations."""
from __future__ import annotations

import hashlib


_NAMES = (
    "Alda", "Bren", "Caro", "Dena", "Eryk", "Fara", "Galen", "Hessa",
    "Iven", "Jora", "Kell", "Lysa", "Marek", "Nera", "Orin", "Pella",
    "Quin", "Rusk", "Sera", "Tavin", "Una", "Venn", "Willa", "Yorren",
)

_ROLES = {
    "settlement": ("Resident", "resident"),
    "town": ("Resident", "resident"),
    "shop": ("Customer", "customer"),
    "tavern": ("Patron", "patron"),
    "temple": ("Worshipper", "worshipper"),
    "residence": ("Resident", "resident"),
}

_POPULATION = {
    "settlement": 2,
    "town": 3,
    "shop": 1,
    "tavern": 3,
    "temple": 2,
    "residence": 1,
}


def ambient_entities(location: dict, game_id: str) -> list[dict]:
    """Build the same modest local population for this location on every reload."""
    role = _ROLES.get(location.get("location_type"))
    if not role:
        return []
    digest = hashlib.sha256(f"{game_id}\0{location['id']}".encode()).digest()
    title, profession = role
    start = int.from_bytes(digest[:2], "big") % len(_NAMES)
    population = []
    for index in range(_POPULATION[location.get("location_type")]):
        personal_name = _NAMES[(start + index * 7) % len(_NAMES)]
        name = f"{title} {personal_name}"
        hp = 4 + digest[2 + index] % 4
        suffix = "" if index == 0 else f"__{index + 1}"
        population.append({
            "id": f"ambient__{location['id']}{suffix}",
            "game_id": game_id,
            "name": name,
            "entity_type": "npc",
            "description": (f"{name} is an ordinary local currently spending time at {location.get('name', 'this place')}. "
                            "They know public details visible here and recent events they personally witnessed, but hold no automatic quest authority."),
            "ability_scores": {"strength": 10, "dexterity": 10, "constitution": 10,
                               "intelligence": 10, "wisdom": 10, "charisma": 10},
            "hp_current": hp, "hp_max": hp, "hp_temp": 0, "ac": 10, "speed": 30,
            "level": 1, "challenge_rating": 0, "attacks": [],
            "behaviors": ["ordinary_local", "avoids_combat"],
            "dialogue_tags": ["ambient", "local_knowledge"],
            "location_id": location["id"], "loot_table": [], "is_hostile": False, "is_alive": True,
            "profession": profession, "schedule": None, "unavailable_periods": ["late_night"],
            "properties": {"ambient": True, "generated": True, "quest_authority": False,
                           "knowledge_scope": "public location facts and witnessed canonical events"},
            "generated": True,
        })
    return population


def ambient_entity(location: dict, game_id: str) -> dict | None:
    """Return the original primary ambient NPC for migration compatibility."""
    population = ambient_entities(location, game_id)
    return population[0] if population else None


def ensure_ambient_population(game_id: str, locations: list[dict], entity_repo) -> int:
    created = 0
    for location in locations:
        for entity in ambient_entities(location, game_id):
            if not entity_repo.get(entity["id"], game_id):
                entity_repo.save(entity)
                created += 1
    return created
