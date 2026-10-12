"""Validation schemas for Director-generated content."""
from __future__ import annotations

import uuid


def validate_npc(data: dict) -> dict:
    """Validate and normalize a generated NPC dict. Returns cleaned data or raises ValueError."""
    required = {"name", "description"}
    missing = required - set(data.keys())
    if missing:
        raise ValueError(f"Generated NPC missing required fields: {missing}")

    name = str(data["name"]).strip()
    if not name or len(name) > 100:
        raise ValueError(f"Invalid NPC name: '{name}'")

    # Ensure sensible stat ranges
    scores = data.get("ability_scores", {})
    for ability in ("strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma"):
        val = scores.get(ability, 10)
        if not isinstance(val, (int, float)):
            scores[ability] = 10
        else:
            scores[ability] = max(1, min(30, int(val)))
    data["ability_scores"] = scores

    hp = data.get("hp_max", 10)
    if not isinstance(hp, (int, float)) or hp < 1:
        hp = 10
    hp = int(hp)
    data["hp_max"] = hp
    data["hp_current"] = data.get("hp_current", hp)

    ac = data.get("ac", 10)
    if not isinstance(ac, (int, float)) or ac < 1 or ac > 30:
        ac = 10
    data["ac"] = int(ac)

    # Ensure required defaults
    data.setdefault("id", str(uuid.uuid4()))
    data.setdefault("entity_type", "npc")
    data.setdefault("dialogue_tags", [])
    data.setdefault("behaviors", [])
    data.setdefault("attacks", [])
    data.setdefault("loot_table", [])
    data.setdefault("is_hostile", False)
    data.setdefault("is_alive", True)
    data.setdefault("generated", True)
    data.setdefault("speed", 30)
    data.setdefault("level", 1)
    data.setdefault("hp_temp", 0)
    data.setdefault("properties", {})

    return data


def validate_location(data: dict) -> dict:
    """Validate and normalize a generated location dict."""
    required = {"name", "description"}
    missing = required - set(data.keys())
    if missing:
        raise ValueError(f"Generated location missing required fields: {missing}")

    name = str(data["name"]).strip()
    if not name or len(name) > 100:
        raise ValueError(f"Invalid location name: '{name}'")

    # Ensure connections is a list
    connections = data.get("connections", [])
    if not isinstance(connections, list):
        connections = []
    data["connections"] = connections

    data.setdefault("id", str(uuid.uuid4()))
    data.setdefault("location_type", "wilderness")
    data.setdefault("entities", [])
    data.setdefault("items", [])
    data.setdefault("visited", False)
    data.setdefault("generated", True)
    data.setdefault("properties", {})

    return data


# Maps objective_type -> (canonical target field, name of the valid-id set)
_OBJECTIVE_TARGET_FIELDS = {
    "kill": "target_entity_type",
    "travel": "target_location_id",
    "talk": "target_npc_id",
    "interact": "target_item_id",
}

# Legacy/loose type names the LLM may still produce
_OBJECTIVE_TYPE_ALIASES = {
    "fetch": "interact", "collect": "interact", "gather": "interact",
    "deliver": "talk", "investigate": "travel", "explore": "travel",
    "defeat": "kill", "slay": "kill",
}


def _normalize_id(value: str) -> str:
    return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")


def _repair_target(target: str, valid_ids: set[str] | None) -> str | None:
    """Match a proposed target against real ids (exact, normalized, then prefix)."""
    target_norm = _normalize_id(target)
    if not target_norm:
        return None
    if valid_ids is None:
        return target_norm  # No reference set available — keep as-is
    normalized_map = {_normalize_id(v): v for v in valid_ids}
    if target_norm in normalized_map:
        return normalized_map[target_norm]
    for norm, original in normalized_map.items():
        if norm.startswith(target_norm) or target_norm.startswith(norm):
            return original
        if target_norm in norm.split("_"):
            return original
    return None


def validate_quest(
    data: dict,
    *,
    valid_locations: set[str] | None = None,
    valid_npcs: set[str] | None = None,
    valid_entity_types: set[str] | None = None,
    valid_items: set[str] | None = None,
) -> dict:
    """Validate and normalize a generated quest dict.

    Objectives are normalized to the canonical schema (objective_type +
    target_location_id/target_entity_type/target_npc_id/target_item_id) so the
    quest tracker can match them. When valid-id sets are given, targets are
    repaired against them; objectives whose target matches nothing real are
    dropped, and a quest left with no valid objectives raises ValueError.
    """
    required = {"name", "description"}
    missing = required - set(data.keys())
    if missing:
        raise ValueError(f"Generated quest missing required fields: {missing}")

    valid_sets = {
        "kill": valid_entity_types,
        "travel": valid_locations,
        "talk": valid_npcs,
        "interact": valid_items,
    }
    reference_given = any(v is not None for v in valid_sets.values())

    objectives = data.get("objectives", [])
    if not isinstance(objectives, list):
        objectives = []
    cleaned_objectives = []
    for obj in objectives:
        if not isinstance(obj, dict):
            continue

        # Normalize the objective type (accept legacy "target_type" key)
        obj_type = str(obj.get("objective_type") or obj.get("target_type") or "").strip().lower()
        obj_type = _OBJECTIVE_TYPE_ALIASES.get(obj_type, obj_type)
        if obj_type not in _OBJECTIVE_TARGET_FIELDS:
            continue
        obj["objective_type"] = obj_type
        obj.pop("target_type", None)

        # Normalize the target into the canonical per-type field
        target_field = _OBJECTIVE_TARGET_FIELDS[obj_type]
        raw_target = obj.pop("target", None) or obj.pop("target_id", None) or obj.get(target_field)
        repaired = _repair_target(str(raw_target or ""), valid_sets[obj_type])
        if repaired is None:
            continue
        obj[target_field] = repaired

        obj.setdefault("id", str(uuid.uuid4()))
        obj.setdefault("description", "Complete the objective")
        obj.setdefault("is_complete", False)
        count = obj.pop("required_count", None) or obj.get("target_count") or 1
        try:
            obj["target_count"] = max(1, int(count))
        except (TypeError, ValueError):
            obj["target_count"] = 1
        obj.setdefault("current_count", 0)
        obj.setdefault("negotiable", False)
        cleaned_objectives.append(obj)

    if reference_given and not cleaned_objectives:
        raise ValueError("Generated quest has no objectives with valid targets")
    data["objectives"] = cleaned_objectives

    data.setdefault("id", str(uuid.uuid4()))
    data.setdefault("status", "available")
    data.setdefault("xp_reward", 50)
    data.setdefault("item_rewards", [])
    data.setdefault("gold_reward", 0)
    data.setdefault("level_requirement", 1)
    data.setdefault("generated", True)
    data.setdefault("npc_motivation", "")
    data.setdefault("completion_flexibility", "low")

    return data


def validate_region(data: dict) -> dict:
    """Validate and normalize a generated region dict."""
    required = {"name", "description"}
    missing = required - set(data.keys())
    if missing:
        raise ValueError(f"Generated region missing required fields: {missing}")

    name = str(data["name"]).strip()
    if not name or len(name) > 100:
        raise ValueError(f"Invalid region name: '{name}'")

    data.setdefault("id", str(uuid.uuid4()).replace("-", "_"))
    data.setdefault("climate", "temperate")
    data.setdefault("level_range_min", 1)
    data.setdefault("level_range_max", 5)

    # Clamp level ranges
    data["level_range_min"] = max(1, min(20, int(data["level_range_min"])))
    data["level_range_max"] = max(data["level_range_min"], min(20, int(data["level_range_max"])))

    # Validate locations list
    locations = data.get("locations", [])
    if not isinstance(locations, list):
        locations = []
    cleaned_locations = []
    for loc in locations:
        if not isinstance(loc, dict):
            continue
        loc.setdefault("id", str(uuid.uuid4()))
        loc.setdefault("name", "Unknown Location")
        loc.setdefault("description", "An unexplored area.")
        loc.setdefault("location_type", "wilderness")
        loc.setdefault("connections", [])
        loc.setdefault("entities", [])
        loc.setdefault("items", [])
        loc.setdefault("visited", False)
        loc.setdefault("properties", {})
        cleaned_locations.append(loc)
    data["locations"] = cleaned_locations

    # Validate NPCs list
    npcs = data.get("npcs", [])
    if not isinstance(npcs, list):
        npcs = []
    cleaned_npcs = []
    for npc in npcs:
        if not isinstance(npc, dict):
            continue
        try:
            cleaned_npcs.append(validate_npc(npc))
        except ValueError:
            continue
    data["npcs"] = cleaned_npcs

    return data


def validate_plausibility(data: dict) -> dict:
    """Validate plausibility evaluation output.

    Raises ValueError on missing/invalid plausibility — a dropped LLM call must
    never silently become a phantom DC-12 success.
    """
    if not isinstance(data, dict):
        raise ValueError("Plausibility assessment is not a dict")
    p = data.get("plausibility")
    if not isinstance(p, (int, float)):
        raise ValueError(f"Plausibility assessment missing numeric 'plausibility': {p!r}")
    data["plausibility"] = max(0.001, min(1.0, float(p)))

    data.setdefault("skill", "athletics")
    data.setdefault("ability", "strength")
    data.setdefault("reasoning", "")

    return data


def validate_spell_proposal(data: dict) -> dict:
    """Validate LLM-generated spell proposal output.

    Raises ValueError when the proposal has no name — a failed LLM call must
    not produce a phantom "Unknown Spell".
    """
    if not isinstance(data, dict):
        raise ValueError("Spell proposal is not a dict")
    name = str(data.get("name", "")).strip()
    if not name:
        raise ValueError("Spell proposal missing 'name'")
    data["name"] = name

    data.setdefault("description", "A mysterious magical effect.")
    data.setdefault("reasoning", "")

    # Clamp level to 0-6
    level = data.get("level", 1)
    if not isinstance(level, (int, float)):
        level = 1
    data["level"] = max(0, min(6, int(level)))

    # Validate school
    valid_schools = {
        "abjuration", "conjuration", "divination", "enchantment",
        "evocation", "illusion", "necromancy", "transmutation",
    }
    school = data.get("school", "evocation")
    if school not in valid_schools:
        data["school"] = "evocation"

    # Clamp plausibility
    p = data.get("plausibility", 0.5)
    if not isinstance(p, (int, float)):
        p = 0.5
    data["plausibility"] = max(0.001, min(1.0, float(p)))

    # Ensure elements is a list
    elements = data.get("elements", [])
    if not isinstance(elements, list):
        elements = []
    data["elements"] = elements

    # Ensure mechanics is a dict with a type
    mechanics = data.get("mechanics", {})
    if not isinstance(mechanics, dict):
        mechanics = {}
    mechanics.setdefault("type", "utility")
    data["mechanics"] = mechanics

    return data
