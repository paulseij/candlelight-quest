"""Pure functions for quest objective progress tracking. No I/O.

Objectives (see content/regions/*/quests.toml and director schemas):
  objective_type ∈ {travel, kill, talk, interact}
  target_location_id / target_entity_type / target_npc_id / target_item_id
  target_count (default 1), current_count, is_complete

The final `talk` objective aimed at the quest giver acts as the turn-in and
only counts once every other objective is complete.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_ID_SUFFIX = re.compile(r"[_-][0-9a-f]{4,}$", re.I)


@dataclass
class Progress:
    quest_id: str
    objective_id: str
    increment: int = 1


def _normalize_entity_key(value: str) -> str:
    """Lowercase, strip generated id suffixes, unify separators."""
    value = (value or "").strip().lower()
    value = _ID_SUFFIX.sub("", value)
    return value.replace("-", "_").replace(" ", "_")


def _entity_matches(objective: dict, entity: dict | None, target_id: str) -> bool:
    """Does a dead/talked-to entity match a kill objective's target_entity_type?"""
    wanted = _normalize_entity_key(objective.get("target_entity_type", ""))
    if not wanted:
        return False
    candidates = {_normalize_entity_key(target_id)}
    if entity:
        candidates.add(_normalize_entity_key(entity.get("id", "")))
        candidates.add(_normalize_entity_key(entity.get("name", "")))
        candidates.add(_normalize_entity_key(entity.get("entity_type", "")))
    candidates.discard("")
    # "wolf" matches "wolf", "dire_wolf" and "wolf_pack_alpha"
    return any(wanted == c or wanted in c.split("_") for c in candidates)


def _npc_matches(objective: dict, entity: dict | None, target_id: str) -> bool:
    wanted = _normalize_entity_key(objective.get("target_npc_id", ""))
    if not wanted:
        return False
    candidates = {_normalize_entity_key(target_id)}
    if entity:
        candidates.add(_normalize_entity_key(entity.get("id", "")))
        candidates.add(_normalize_entity_key(entity.get("name", "")))
    candidates.discard("")
    return wanted in candidates


def _is_turn_in(objective: dict, quest: dict) -> bool:
    """The talk objective aimed at the quest giver is the turn-in step."""
    if objective.get("objective_type") != "talk":
        return False
    giver = quest.get("quest_giver_id", "")
    return bool(giver) and _normalize_entity_key(objective.get("target_npc_id", "")) == _normalize_entity_key(giver)


def _others_complete(quest: dict, objective: dict) -> bool:
    return all(
        obj.get("is_complete", False)
        for obj in quest.get("objectives", [])
        if obj is not objective
    )


def match_events_to_objectives(
    events: list[dict],
    quests: list[dict],
    entity_lookup: dict[str, dict] | None = None,
) -> list[Progress]:
    """Match this turn's events against active quests' incomplete objectives."""
    entity_lookup = entity_lookup or {}
    progresses: list[Progress] = []

    for event in events:
        event_type = event.get("event_type", "")
        target_id = event.get("target_id") or ""
        location_id = event.get("location_id") or ""
        details = event.get("mechanical_details") or {}
        if not isinstance(details, dict):
            details = {}

        for quest in quests:
            for objective in quest.get("objectives", []):
                if objective.get("is_complete", False):
                    continue
                obj_type = objective.get("objective_type", "")

                matched = False
                if obj_type in {'campaign_evidence', 'campaign_choice'}:
                    matched = (event_type == obj_type.upper() and details.get('quest_id') == quest.get('id')
                               and details.get('objective_id') == objective.get('id'))
                if obj_type == "kill" and event_type == "DEATH":
                    matched = _entity_matches(
                        objective, entity_lookup.get(target_id), target_id)
                elif obj_type == "travel" and event_type in ("MOVE", "DISCOVERY"):
                    matched = bool(location_id) and location_id == objective.get("target_location_id", "")
                elif obj_type == "talk" and event_type == "DIALOGUE":
                    matched = _npc_matches(
                        objective, entity_lookup.get(target_id), target_id)
                    # Turn-in only counts once the rest of the quest is done.
                    if matched and _is_turn_in(objective, quest) and not _others_complete(quest, objective):
                        matched = False
                elif obj_type == "interact" and event_type == "ITEM_PICKUP":
                    matched = bool(details.get("item_id")) and details.get("item_id") == objective.get("target_item_id", "")

                if matched:
                    progresses.append(Progress(
                        quest_id=quest.get("id", ""),
                        objective_id=objective.get("id", ""),
                    ))

    return progresses


def apply_progress(
    quest: dict, progresses: list[Progress],
) -> tuple[dict, list[str], bool]:
    """Apply matched progress to one quest (mutates and returns it).

    Returns (quest, newly_completed_objective_descriptions, quest_now_complete).
    """
    completed_descriptions: list[str] = []
    quest_id = quest.get("id", "")

    for progress in progresses:
        if progress.quest_id != quest_id:
            continue
        for objective in quest.get("objectives", []):
            if objective.get("id") != progress.objective_id or objective.get("is_complete", False):
                continue
            target = int(objective.get("target_count") or 1)
            current = int(objective.get("current_count") or 0) + progress.increment
            objective["current_count"] = min(current, target)
            if objective["current_count"] >= target:
                objective["is_complete"] = True
                completed_descriptions.append(objective.get("description", "Objective complete"))

    quest_complete = bool(quest.get("objectives")) and all(
        obj.get("is_complete", False) for obj in quest.get("objectives", [])
    )
    return quest, completed_descriptions, quest_complete


def objective_progress_text(objective: dict) -> str:
    """Short progress string, e.g. 'Defeat the wolf pack (2/3)'."""
    description = objective.get("description", "")
    target = int(objective.get("target_count") or 1)
    if target > 1:
        current = int(objective.get("current_count") or 0)
        description = re.sub(r"\s*\(\d+/\d+\)\s*$", "", description)
        return f"{description} ({current}/{target})"
    return description


def is_ready_for_turn_in(quest: dict) -> bool:
    """All non-turn-in objectives complete, turn-in still pending."""
    objectives = quest.get("objectives", [])
    for objective in objectives:
        if objective.get("is_complete", False):
            continue
        if _is_turn_in(objective, quest):
            continue
        return False
    return any(not obj.get("is_complete", False) for obj in objectives)


def compute_rewards(quest: dict) -> dict:
    """Extract the reward payout for a completed quest."""
    items = quest.get("item_rewards") or []
    if not isinstance(items, list):
        items = []
    return {
        "xp": int(quest.get("xp_reward") or 0),
        "gold": int(quest.get("gold_reward") or 0),
        "items": [i for i in items if isinstance(i, str) and i],
    }


def merge_negotiated_objectives(
    original: list[dict], proposed: list[dict],
) -> list[dict]:
    """Safely apply LLM-negotiated changes onto a quest's real objectives.

    Negotiation may only soften terms on negotiable objectives: lower the
    target_count (never below 1, never above the original). Descriptions
    are rendered from accepted fields. Objectives are matched by id (or by position as a fallback);
    canonical fields (objective_type, target_*) are never touched, and
    objectives are never added or removed.
    """
    proposed_by_id = {
        str(p.get("id", "")): p for p in proposed
        if isinstance(p, dict) and p.get("id")
    }

    merged: list[dict] = []
    for index, obj in enumerate(original):
        obj = dict(obj)
        proposal = proposed_by_id.get(str(obj.get("id", "")))
        if proposal is None and len(proposed) == len(original):
            candidate = proposed[index]
            proposal = candidate if isinstance(candidate, dict) else None
        if proposal is not None and obj.get("negotiable", False):
            original_count = int(obj.get("target_count") or 1)
            raw_count = proposal.get("target_count", proposal.get("required_count"))
            try:
                new_count = int(raw_count)
            except (TypeError, ValueError):
                new_count = original_count
            obj["target_count"] = max(1, min(original_count, new_count))
            if obj["target_count"] < original_count:
                obj["current_count"] = min(int(obj.get("current_count") or 0), obj["target_count"])
            from text_rpg.llm.grounding import objective_text
            obj["description"] = objective_text(obj)
        merged.append(obj)
    return merged


def handle_quest_giver_death(
    quests: list[dict], dead_npc_id: str,
) -> list[tuple[str, str]]:
    """Decide what happens to quests whose giver just died.

    Returns [(quest_id, action)] where action is "fail" (never accepted) or
    "orphan" (active: complete remaining turn-in automatically when ready).
    """
    outcomes: list[tuple[str, str]] = []
    dead_key = _normalize_entity_key(dead_npc_id)
    for quest in quests:
        giver = _normalize_entity_key(quest.get("quest_giver_id", ""))
        if not giver or giver != dead_key:
            continue
        status = quest.get("status", "")
        if status == "available":
            outcomes.append((quest.get("id", ""), "fail"))
        elif status == "active":
            outcomes.append((quest.get("id", ""), "orphan"))
    return outcomes
