"""Quest progress tracking — turns emitted events into quest state changes.

UI-agnostic: returns events and mutations, never prints. Hooked into the turn
loop right after a turn's events are recorded.
"""
from __future__ import annotations

import logging
from typing import Any

from text_rpg.mechanics.quest_progress import (
    apply_progress,
    compute_rewards,
    handle_quest_giver_death,
    match_events_to_objectives,
)
from text_rpg.models.action import StateMutation

logger = logging.getLogger(__name__)

# Event types that can advance quest objectives
_RELEVANT_EVENTS = {"DEATH", "MOVE", "DISCOVERY", "DIALOGUE", "ITEM_PICKUP", "CAMPAIGN_EVIDENCE", "CAMPAIGN_CHOICE"}


class QuestTracker:
    def __init__(self, repos: dict[str, Any]):
        self.repos = repos

    def process(
        self, events: list[dict], context: Any, game_id: str,
    ) -> tuple[list[dict], int, list[StateMutation]]:
        """Match events against active quests.

        Returns (quest_events, bonus_xp, reward_mutations).
        """
        relevant = [e for e in events if e.get("event_type") in _RELEVANT_EVENTS]
        if not relevant:
            return [], 0, []

        ws = self.repos["world_state"]
        quests = ws.get_active_quests(game_id)

        quest_events: list[dict] = []
        bonus_xp = 0
        mutations: list[StateMutation] = []

        # Quest-giver deaths first: fail unaccepted quests, orphan active ones.
        death_ids = [e.get("target_id", "") for e in relevant if e.get("event_type") == "DEATH"]
        if death_ids:
            all_quests = [q for q in ws.get_all_quests(game_id) if q.get("status") in ("active", "available")]
            for dead_id in death_ids:
                dead_entity = self.repos["entity"].get(dead_id) if dead_id else None
                # Try both the raw id and the entity's name-derived id
                for outcome_id, action_name in handle_quest_giver_death(all_quests, dead_id):
                    quest = next((q for q in all_quests if q.get("id") == outcome_id), None)
                    if not quest:
                        continue
                    if action_name == "fail":
                        ws.update_quest_status(outcome_id, game_id, "failed")
                        quest["status"] = "failed"
                        quest_events.append({
                            "event_type": "QUEST_FAILED",
                            "description": f"Quest lost: {quest.get('name', '')} — its giver is dead.",
                            "mechanical_details": {"quest_id": outcome_id, "quest_name": quest.get("name", "")},
                        })
                    elif action_name == "orphan":
                        # Complete the turn-in objective now; the quest finishes
                        # through normal progress once the rest is done.
                        changed = False
                        for objective in quest.get("objectives", []):
                            if (objective.get("objective_type") == "talk"
                                    and not objective.get("is_complete", False)
                                    and objective.get("target_npc_id") == quest.get("quest_giver_id")):
                                objective["is_complete"] = True
                                objective["description"] = (
                                    objective.get("description", "") + " (the quest giver has died)"
                                ).strip()
                                changed = True
                        if changed:
                            ws.save_quest(quest)
                            quest_events.append({
                                "event_type": "QUEST_UPDATE",
                                "description": f"{quest.get('name', '')}: the quest giver is dead — no one awaits your return.",
                                "mechanical_details": {"quest_id": outcome_id, "quest_name": quest.get("name", "")},
                            })
            # Refresh the active list after giver-death handling
            quests = ws.get_active_quests(game_id)

        if quests:
            entity_lookup: dict[str, dict] = {}
            for event in relevant:
                target_id = event.get("target_id") or ""
                if target_id and target_id not in entity_lookup:
                    entity = self.repos["entity"].get(target_id)
                    if entity:
                        entity_lookup[target_id] = entity

            progresses = match_events_to_objectives(relevant, quests, entity_lookup)
            touched_ids = {p.quest_id for p in progresses}
            total_gold = 0
            reward_items: list[str] = []

            for quest in quests:
                if quest.get("id") not in touched_ids:
                    continue
                quest, completed_objectives, quest_complete = apply_progress(quest, progresses)
                ws.save_quest(quest)

                for description in completed_objectives:
                    quest_events.append({
                        "event_type": "QUEST_UPDATE",
                        "description": f"{quest.get('name', '')}: {description} — complete.",
                        "mechanical_details": {
                            "quest_id": quest.get("id", ""),
                            "quest_name": quest.get("name", ""),
                            "objective": description,
                        },
                    })

                if quest_complete:
                    ws.update_quest_status(quest.get("id", ""), game_id, "completed")
                    rewards = compute_rewards(quest)
                    bonus_xp += rewards["xp"]
                    total_gold += rewards["gold"]
                    reward_items.extend(rewards["items"])
                    quest_events.append({
                        "event_type": "QUEST_COMPLETE",
                        "description": f"Quest complete: {quest.get('name', '')}.",
                        "mechanical_details": {
                            "quest_id": quest.get("id", ""),
                            "quest_name": quest.get("name", ""),
                            "xp": rewards["xp"],
                            "gold": rewards["gold"],
                            "items": rewards["items"],
                        },
                    })

            mutations.extend(self._reward_mutations(total_gold, reward_items, context))

        return quest_events, bonus_xp, mutations

    def _reward_mutations(
        self, gold: int, items: list[str], context: Any,
    ) -> list[StateMutation]:
        """Build gold/item payout mutations for completed quests."""
        mutations: list[StateMutation] = []
        char = context.character or {}
        char_id = char.get("id", "")
        if gold > 0:
            # Read fresh gold so the payout stacks with this turn's changes.
            current = self.repos["character"].get(char_id) or char
            old_gold = int(current.get("gold") or 0)
            mutations.append(StateMutation(
                target_type="character", target_id=char_id, field="gold",
                old_value=old_gold, new_value=old_gold + gold,
            ))
        for item_id in items:
            mutations.append(StateMutation(
                target_type="inventory", target_id=char_id, field="items_add",
                new_value={"item_id": item_id, "quantity": 1},
            ))
        return mutations
