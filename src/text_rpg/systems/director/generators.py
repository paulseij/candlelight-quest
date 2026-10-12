"""LLM-powered content generators for the Director."""
from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

from text_rpg.llm.provider import LLMProvider
from text_rpg.llm.schemas import (
    CreativeOutcome,
    ItemProposal,
    PlausibilityAssessment,
    SpellProposal,
)
from text_rpg.systems.base import GameContext
from text_rpg.systems.director.schemas import (
    validate_location,
    validate_npc,
    validate_plausibility,
    validate_quest,
    validate_spell_proposal,
)
from text_rpg.utils import safe_json, safe_props

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent.parent.parent / "llm" / "prompts"
_jinja_env: Environment | None = None


def _get_jinja() -> Environment:
    global _jinja_env
    if _jinja_env is None:
        _jinja_env = Environment(
            loader=FileSystemLoader(str(_PROMPTS_DIR)),
            autoescape=False,
        )
    return _jinja_env


# -- Context formatters (concise summaries for LLM prompts) --

def _format_character(char: dict) -> str:
    scores = safe_json(char.get("ability_scores"), {})
    profs = safe_json(char.get("skill_proficiencies"), [])
    return (
        f"{char.get('name', 'Unknown')} — Level {char.get('level', 1)} "
        f"{char.get('race', '?')} {char.get('char_class', '?')}\n"
        f"HP: {char.get('hp_current', 0)}/{char.get('hp_max', 0)} | AC: {char.get('ac', 10)}\n"
        f"STR {scores.get('strength', 10)} DEX {scores.get('dexterity', 10)} "
        f"CON {scores.get('constitution', 10)} INT {scores.get('intelligence', 10)} "
        f"WIS {scores.get('wisdom', 10)} CHA {scores.get('charisma', 10)}\n"
        f"Skills: {', '.join(profs) if profs else 'none'}"
    )


def _format_location(loc: dict) -> str:
    connections = safe_json(loc.get("connections"), [])
    exits = ", ".join(
        c.get("direction", "?") for c in connections if isinstance(c, dict)
    )
    return (
        f"{loc.get('name', 'Unknown')} ({loc.get('location_type', 'unknown')})\n"
        f"{loc.get('description', 'No description.')}\n"
        f"Exits: {exits if exits else 'none'}"
    )


def _format_entities(entities: list[dict]) -> str:
    if not entities:
        return "None present."
    parts = []
    for e in entities:
        if e.get("is_alive", True):
            parts.append(f"- {e['name']}: {e.get('description', 'No description.')[:100]}")
    return "\n".join(parts) if parts else "None present."


def _format_entities_with_ids(entities: list[dict]) -> str:
    if not entities:
        return "None present."
    parts = []
    for e in entities:
        if e.get("is_alive", True):
            parts.append(f"- id={e.get('id', '?')} — {e.get('name', '?')}: {e.get('description', 'No description.')[:80]}")
    return "\n".join(parts) if parts else "None present."


def _format_recent_events(events: list[dict]) -> str:
    if not events:
        return "Nothing notable has happened recently."
    parts = []
    for e in events[:5]:
        parts.append(f"- [{e.get('event_type', '?')}] {e.get('description', '')[:100]}")
    return "\n".join(parts)


def _format_inventory(inventory: dict | None) -> str:
    if not inventory:
        return "Empty pack."
    items = safe_json(inventory.get("items"), [])
    if not items:
        return "Empty pack."
    parts = [f"- {i.get('item_id', '?')} x{i.get('quantity', 1)}" for i in items[:10]]
    return "\n".join(parts)


# -- Generators --

def generate_npc(
    llm: LLMProvider,
    context: GameContext,
    location: dict,
    constraints: dict,
) -> dict:
    """Generate a new NPC fitting the given location. Returns validated NPC dict."""
    env = _get_jinja()
    template = env.get_template("director/npc_generation.j2")

    existing_npcs = _format_entities(context.entities)

    prompt = template.render(
        location_summary=_format_location(location),
        location_type=location.get("location_type", "wilderness"),
        region_name=location.get("region_id", "unknown"),
        character_summary=_format_character(context.character),
        existing_npcs=existing_npcs,
        world_context=_format_recent_events(context.recent_events),
    )

    raw = llm.generate_structured(prompt, temperature=0.9, max_tokens=512)
    npc_data = validate_npc(raw)

    # Assign faction based on location type / region
    loc_type = location.get("location_type", "wilderness")
    region = location.get("region_id", "unknown")
    if loc_type in ("town", "village", "settlement", "tavern", "shop"):
        npc_data.setdefault("faction_id", f"{region}_guard" if npc_data.get("is_hostile") else f"{region}_merchants")
    return npc_data


def generate_location(
    llm: LLMProvider,
    context: GameContext,
    direction: str,
    source_location: dict,
) -> dict:
    """Generate a new location in a given direction. Returns validated location dict."""
    env = _get_jinja()
    template = env.get_template("director/location_generation.j2")

    # Build list of existing locations from connections
    connections = safe_json(source_location.get("connections"), [])
    existing = ", ".join(
        c.get("description", c.get("target_location_id", "?"))
        for c in connections if isinstance(c, dict)
    )

    props = safe_props(source_location)

    prompt = template.render(
        source_location_summary=_format_location(source_location),
        direction=direction,
        region_description=props.get("region_description", source_location.get("description", "")),
        player_level=context.character.get("level", 1),
        existing_locations=existing if existing else "None known nearby.",
    )

    from text_rpg.llm.errors import LLMMalformedOutputError
    from text_rpg.llm.schemas import LocationProposal
    try:
        raw = llm.generate_structured(prompt, temperature=0.7, max_tokens=1024, schema=LocationProposal, retries=0)
    except LLMMalformedOutputError:
        # A short repair attempt avoids repeating the large prompt that caused
        # the original truncation. The Director has an offline fallback if even
        # this request fails.
        compact = (
            f"Create one outdoor location {direction} of {source_location.get('name', 'this place')}. "
            f"Region: {source_location.get('region_id', 'unknown')}. "
            "Return only a short JSON object with name, description (two sentences), "
            "location_type, atmosphere, and discovery (one physical detail a search reveals). No entities, items, "
            "exits, rewards or invented quest facts."
        )
        raw = llm.generate_structured(compact, temperature=0.6, max_tokens=768, schema=LocationProposal, retries=0)
    proposal = LocationProposal.model_validate(raw)
    return validate_location({
        'name': proposal.name, 'description': proposal.description,
        'location_type': proposal.location_type,
        'properties': {'atmosphere': proposal.atmosphere, 'generation_source': 'model',
                       'discoveries': [{'id': 'site_detail', 'dc': 10, 'description': proposal.discovery}]},
    })


def fallback_location(source_location: dict, direction: str) -> dict:
    """A connected, searchable wilderness place if generation is unavailable.

    The site claims no creatures, loot, quest progress or unseen world facts.
    Its stable id and connections are persisted by the Director as usual.
    """
    import uuid

    source_name = str(source_location.get('name') or 'the road')[:65]
    terrain = source_location.get('location_type', 'wilderness')
    if terrain == 'mountain':
        feature = 'a shelf of weathered rock'
        atmosphere = 'Wind sifts grit through the stone and exposes older layers beneath it.'
    elif terrain in {'coast', 'beach', 'cave'}:
        feature = 'a narrow stretch of broken ground'
        atmosphere = 'Salt and damp stone mark the edge of the route.'
    elif terrain == 'forest':
        feature = 'a clearing between dense growth'
        atmosphere = 'The canopy thins enough to show the lay of the land.'
    else:
        feature = 'a rise in the rough ground'
        atmosphere = 'The surface changes underfoot, making old paths easier to trace.'
    name = f"{direction.title()} Reach beyond {source_name}"[:100]
    return validate_location({
        'id': str(uuid.uuid4()), 'name': name, 'location_type': 'wilderness',
        'description': f"The route {direction} from {source_name} reaches {feature}. {atmosphere} You can return by the way you came or study the ground before moving on.",
        'connections': [], 'entities': [], 'items': [],
        'properties': {
            'generation_source': 'fallback',
            'atmosphere': atmosphere,
            'points_of_interest': [feature],
            'discoveries': [{
                'id': 'ground_traces', 'dc': 10,
                'description': f"The ground at {feature} shows where water and repeated passage have worn a firmer route.",
            }],
        },
    })


def _candidate_list(candidates: dict | None, key: str) -> str:
    values = sorted((candidates or {}).get(key, set()))
    return ", ".join(values) if values else "none known"


def _candidate_set(candidates: dict | None, key: str) -> set[str] | None:
    if candidates is None or key not in candidates:
        return None
    return set(candidates[key])


def generate_quest(
    llm: LLMProvider,
    context: GameContext,
    npc: dict,
    candidates: dict | None = None,
) -> dict:
    """Generate a quest from an NPC's motivation. Returns validated quest dict.

    candidates optionally holds real-id sets ("locations", "npcs",
    "entity_types", "items") that objectives are constrained to and repaired
    against — targets matching nothing real are dropped.
    """
    env = _get_jinja()
    template = env.get_template("director/quest_generation.j2")

    props = safe_props(npc)

    prompt = template.render(
        npc_name=npc.get("name", "Unknown"),
        npc_description=npc.get("description", ""),
        npc_personality=", ".join(npc.get("dialogue_tags") or []),
        npc_motivation=props.get("motivation", "unknown"),
        quest_hook=props.get("quest_hook", "has a problem"),
        character_summary=_format_character(context.character),
        location_summary=_format_location(context.location),
        recent_events_summary=_format_recent_events(context.recent_events),
        valid_locations=_candidate_list(candidates, "locations"),
        valid_npcs=_candidate_list(candidates, "npcs"),
        valid_entity_types=_candidate_list(candidates, "entity_types"),
        valid_items=_candidate_list(candidates, "items"),
    )

    raw = llm.generate_structured(prompt, temperature=0.8, max_tokens=1024)
    return validate_quest(
        raw,
        valid_locations=_candidate_set(candidates, "locations"),
        valid_npcs=_candidate_set(candidates, "npcs"),
        valid_entity_types=_candidate_set(candidates, "entity_types"),
        valid_items=_candidate_set(candidates, "items"),
    )


def generate_follow_up_quest(
    llm: LLMProvider,
    context: GameContext,
    completed_quest: dict,
    candidates: dict | None = None,
) -> dict:
    """Generate a follow-up quest after completion. Returns validated quest dict."""
    env = _get_jinja()
    template = env.get_template("director/quest_generation.j2")

    # Re-use quest_generation template with follow-up context
    prompt = template.render(
        npc_name="the quest giver",
        npc_description="The person who gave you the previous quest.",
        npc_personality="grateful, thoughtful",
        npc_motivation=f"Follow-up to completed quest: {completed_quest.get('name', 'unknown')}",
        quest_hook=f"After completing '{completed_quest.get('name', '')}', a new situation has arisen.",
        character_summary=_format_character(context.character),
        location_summary=_format_location(context.location),
        recent_events_summary=_format_recent_events(context.recent_events),
        valid_locations=_candidate_list(candidates, "locations"),
        valid_npcs=_candidate_list(candidates, "npcs"),
        valid_entity_types=_candidate_list(candidates, "entity_types"),
        valid_items=_candidate_list(candidates, "items"),
    )

    raw = llm.generate_structured(prompt, temperature=0.8, max_tokens=1024)
    data = validate_quest(
        raw,
        valid_locations=_candidate_set(candidates, "locations"),
        valid_npcs=_candidate_set(candidates, "npcs"),
        valid_entity_types=_candidate_set(candidates, "entity_types"),
        valid_items=_candidate_set(candidates, "items"),
    )

    # Track chain depth
    old_props = safe_props(completed_quest)
    chain_depth = old_props.get("chain_depth", 0) + 1
    data.setdefault("properties", {})
    data["properties"] = safe_json(data["properties"], {})
    data["properties"]["chain_depth"] = chain_depth
    data["properties"]["predecessor_quest_id"] = completed_quest.get("id")

    return data


def generate_region(
    llm: LLMProvider,
    context: GameContext,
    current_region: dict,
    target_level_min: int,
    target_level_max: int,
    existing_region_names: list[str],
) -> dict:
    """Generate a new region for the player to discover. Returns validated region dict."""
    from text_rpg.systems.director.schemas import validate_region

    env = _get_jinja()
    template = env.get_template("director/region_generation.j2")

    prompt = template.render(
        current_region_name=current_region.get("name", "Unknown"),
        current_level_min=current_region.get("level_range_min", 1),
        current_level_max=current_region.get("level_range_max", 5),
        current_region_description=current_region.get("description", ""),
        character_summary=_format_character(context.character),
        player_level=context.character.get("level", 1),
        target_level_min=target_level_min,
        target_level_max=target_level_max,
        existing_regions=", ".join(existing_region_names) if existing_region_names else "None known",
    )

    raw = llm.generate_structured(prompt, temperature=0.9, max_tokens=1024)
    return validate_region(raw)


def evaluate_plausibility(
    llm: LLMProvider,
    action_description: str,
    context: GameContext,
) -> dict:
    """Evaluate how plausible a creative player action is. Returns validated dict."""
    env = _get_jinja()
    template = env.get_template("director/plausibility_check.j2")

    prompt = template.render(
        action_description=action_description,
        character_summary=_format_character(context.character),
        location_summary=_format_location(context.location),
        entities_summary=_format_entities(context.entities),
        recent_events_summary=_format_recent_events(context.recent_events),
        inventory_summary=_format_inventory(context.inventory),
    )

    raw = llm.generate_structured(prompt, temperature=0.7, max_tokens=512,
                                  schema=PlausibilityAssessment)
    return validate_plausibility(raw)


def generate_creative_outcome(
    llm: LLMProvider,
    action_description: str,
    context: GameContext,
    skill: str,
    roll_total: int,
    dc: int,
    roll_succeeded: bool,
    available_items: str,
    effect_menu: str,
    budget: int,
) -> dict:
    """Generate the post-roll outcome of a creative action as typed effects.

    Called AFTER the dice are rolled; the model narrates and proposes effects
    from a closed menu. Raises LLMUnavailableError / LLMMalformedOutputError.
    """
    env = _get_jinja()
    template = env.get_template("director/creative_outcome.j2")

    prompt = template.render(
        action_description=action_description,
        skill=skill,
        roll_total=roll_total,
        dc=dc,
        roll_succeeded=roll_succeeded,
        margin=roll_total - dc,
        character_summary=_format_character(context.character),
        location_summary=_format_location(context.location),
        entities_summary=_format_entities_with_ids(context.entities),
        inventory_summary=_format_inventory(context.inventory),
        available_items=available_items,
        effect_menu=effect_menu,
        budget=budget,
    )

    return llm.generate_structured(prompt, temperature=0.8, max_tokens=768,
                                   schema=CreativeOutcome)


def evaluate_creative_solution(
    llm: LLMProvider,
    quest: dict,
    player_action: str,
    context: GameContext,
) -> dict:
    """Evaluate if a creative approach satisfies a quest."""
    env = _get_jinja()
    template = env.get_template("director/evaluate_creative_solution.j2")

    objectives = safe_json(quest.get("objectives"), [])

    prompt = template.render(
        quest_name=quest.get("name", "Unknown"),
        quest_description=quest.get("description", ""),
        npc_motivation=quest.get("npc_motivation", "unknown"),
        completion_flexibility=quest.get("completion_flexibility", "low"),
        objectives=objectives,
        player_action=player_action,
        context_summary=_format_location(context.location),
    )

    raw = llm.generate_structured(prompt, temperature=0.7, max_tokens=512)

    # Validate basics
    raw.setdefault("satisfies_quest", False)
    raw.setdefault("reasoning", "")
    raw.setdefault("npc_reaction", "")
    raw.setdefault("partial_credit", False)
    raw.setdefault("xp_modifier", 1.0)
    return raw


def negotiate_quest(
    llm: LLMProvider,
    quest: dict,
    npc: dict,
    player_proposal: str,
    check_total: int,
    check_dc: int,
    check_success: bool,
) -> dict:
    """Evaluate a player's quest negotiation attempt."""
    env = _get_jinja()
    template = env.get_template("director/negotiate_quest.j2")

    props = safe_props(npc)
    relationships = props.get("relationships", {})
    player_rel = relationships.get("player", {})

    objectives = safe_json(quest.get("objectives"), [])

    prompt = template.render(
        npc_name=npc.get("name", "Unknown"),
        npc_personality=", ".join(npc.get("dialogue_tags") or []),
        disposition=player_rel.get("disposition", "neutral"),
        trust_level=player_rel.get("trust", 0),
        quest_name=quest.get("name", "Unknown"),
        quest_description=quest.get("description", ""),
        npc_motivation=quest.get("npc_motivation", "unknown"),
        objectives=objectives,
        player_proposal=player_proposal,
        check_total=check_total,
        check_dc=check_dc,
        check_success=check_success,
    )

    raw = llm.generate_structured(prompt, temperature=0.7, max_tokens=512)

    # Validate basics
    raw.setdefault("accepted", False)
    raw.setdefault("modified_objectives", objectives)
    raw.setdefault("npc_response", "The NPC considers your words carefully.")
    raw.setdefault("disposition_change", 0)
    return raw


def plausibility_to_dc(plausibility: float) -> int:
    """Map a plausibility score (0.001–1.0) to a D&D-style DC (5–40).

    Uses a logarithmic scale:
    1.0  → DC 5   (trivial)
    0.8  → DC 8
    0.5  → DC 12  (standard)
    0.3  → DC 15
    0.1  → DC 20
    0.05 → DC 25
    0.01 → DC 30
    0.001→ DC 40
    """
    p = max(0.001, min(1.0, plausibility))
    # Logarithmic mapping: DC = 5 - 8.33 * ln(p)
    dc = 5 - 8.33 * math.log(p)
    return max(5, min(40, round(dc)))


def evaluate_spell_invention(
    llm: LLMProvider,
    spell_concept: str,
    context: GameContext,
) -> dict:
    """Evaluate a player's spell invention concept via LLM. Returns validated dict."""
    env = _get_jinja()
    template = env.get_template("director/spell_evaluation.j2")

    # Gather known elements from player's spell repertoire
    known_elements: set[str] = set()
    from text_rpg.content.loader import load_all_spells
    all_spells = load_all_spells()
    for spell in all_spells.values():
        mechanics = spell.get("mechanics", {})
        dt = mechanics.get("damage_type", "")
        if dt:
            known_elements.add(dt)

    prompt = template.render(
        spell_concept=spell_concept,
        character_summary=_format_character(context.character),
        location_summary=_format_location(context.location),
        known_elements=", ".join(sorted(known_elements)) if known_elements else "none",
    )

    raw = llm.generate_structured(prompt, temperature=0.8, max_tokens=512,
                                  schema=SpellProposal)
    return validate_spell_proposal(raw)


def evaluate_item_invention(
    llm: LLMProvider,
    concept: str,
    context: GameContext,
    max_rarity: str,
    crafting_context: str,
) -> dict:
    """Evaluate a player's item invention concept via LLM.

    Returns the ItemProposal dict (schema-enforced). The caller applies the
    rarity/budget clamps via mechanics.item_invention.validate_item_proposal.
    """
    env = _get_jinja()
    template = env.get_template("director/item_invention.j2")

    prompt = template.render(
        concept=concept,
        character_summary=_format_character(context.character),
        location_summary=_format_location(context.location),
        max_rarity=max_rarity,
        crafting_context=crafting_context,
    )

    return llm.generate_structured(prompt, temperature=0.8, max_tokens=512,
                                   schema=ItemProposal)
