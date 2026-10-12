"""Social system — NPC dialogue, conversation memory, and quest negotiation."""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from text_rpg.mechanics.ability_scores import modifier
from text_rpg.mechanics.skills import skill_check
from text_rpg.models.action import Action, ActionResult, DiceRoll
from text_rpg.systems.base import GameContext, GameSystem
from text_rpg.utils import safe_json, safe_props

logger = logging.getLogger(__name__)

# Keywords that indicate the player is trying to negotiate a quest.
# These must be specific to negotiation — generic phrases like "can i" cause false positives.
_NEGOTIATE_KEYWORDS = (
    "negotiate", "bargain", "persuade", "convince", "counter-offer",
    "what if i", "how about i", "instead i", "instead of",
    "can i do fewer", "can i bring fewer", "can i bring less",
    "let me offer", "make a deal", "strike a deal",
    "fewer", "lower the", "reduce the",
)

# Keywords that indicate the player is explicitly asking for a quest/task.
_QUEST_ASK_KEYWORDS = (
    "quest", "task", "job", "work for", "help with", "need help",
    "need anything", "need something", "anything for me",
    "any tasks", "any jobs", "any work", "something to do",
    "things to do", "anything to do", "got anything", "have anything",
    "need me to", "can i help", "how can i help", "what can i do",
    "hire me", "mission", "assignment", "errand",
)

# Regex to strip the "talk to ..." command prefix from raw input.
_TALK_PREFIX = re.compile(
    r"^(?:can\s+i\s+|let\s+me\s+|i\s+want\s+to\s+|i(?:'d|\s+would)\s+like\s+to\s+)?"
    r"(?:talk|speak|chat)\s+(?:to|with)\s+",
    re.I,
)


class SocialSystem(GameSystem):
    def __init__(self, director: Any | None = None, repos: dict[str, Any] | None = None):
        self._director = director
        self._repos = repos or {}

    def inject(self, *, director: Any = None, repos: dict | None = None, **kwargs: Any) -> None:
        if director is not None:
            self._director = director
        if repos is not None:
            self._repos = repos

    @property
    def system_id(self) -> str:
        return "social"

    @property
    def handled_action_types(self) -> set[str]:
        return {"talk", "accept_quest", "decline_quest"}

    def can_handle(self, action: Action, context: GameContext) -> bool:
        return action.action_type.lower() in self.handled_action_types

    def resolve(self, action: Action, context: GameContext) -> ActionResult:
        if action.action_type == "accept_quest":
            return self._resolve_accept_quest(action, context)
        if action.action_type == "decline_quest":
            return self._resolve_decline_quest(action, context)

        target_name = (action.target_id or "").lower()
        npc = None
        for e in context.entities:
            if e.get("entity_type") == "npc" and e.get("is_alive", True):
                if e["id"].lower() == target_name or e["name"].lower() == target_name or target_name in e["name"].lower():
                    npc = e
                    break

        if not npc:
            from text_rpg.engine.nearby_people import nearby_people
            return ActionResult(
                action_id=action.id, success=False,
                outcome_description=f"There's nobody called '{action.target_id}' to talk to here.\n{nearby_people(context)}",
            )

        # Check NPC availability based on time of day
        from text_rpg.mechanics.world_sim import is_npc_available, get_npc_activity
        from text_rpg.mechanics.world_clock import get_period
        period = get_period(context.world_time)
        if not is_npc_available(npc, period):
            activity = get_npc_activity(npc, period)
            return ActionResult(
                action_id=action.id, success=False,
                outcome_description=f"{npc['name']} is {activity}. They're not available right now.",
            )

        # Extract actual dialogue content (strip "talk to [name]" command prefix)
        dialogue_content = self._extract_dialogue(action.raw_input, npc["name"])
        is_greeting = not dialogue_content

        # Check if this is a quest negotiation attempt (only when player says something specific)
        if not is_greeting:
            raw_lower = action.raw_input.lower()
            if self._director and self._repos and any(kw in raw_lower for kw in _NEGOTIATE_KEYWORDS):
                negotiation_result = self._try_negotiate_quest(action, npc, context)
                if negotiation_result:
                    return negotiation_result

        # Build enhanced dialogue event with NPC memory
        npc_history = self._get_npc_history(npc, context)
        relationship = self._get_relationship(npc)
        quest_hook = self._get_quest_hook(npc)

        # Detect if the player is explicitly asking for a quest
        asking_for_quest = False
        if not is_greeting and dialogue_content:
            input_lower = dialogue_content.lower()
            asking_for_quest = any(kw in input_lower for kw in _QUEST_ASK_KEYWORDS)

        # If player is asking for a quest and NPC has a hook but no active quest, generate one now
        if asking_for_quest and quest_hook and self._director and self._repos:
            from text_rpg.systems.director.triggers import should_offer_quest
            if should_offer_quest(npc, context):
                try:
                    quest_event = self._director._try_prepare_quest(npc, context, self._repos)
                    if quest_event:
                        # Record the quest event so it exists before dialogue renders
                        self._record_quest_event(quest_event, context)
                except Exception as e:
                    logger.debug(f"Failed to generate quest on ask: {e}")

        events = [{
            "event_type": "DIALOGUE",
            "description": f"Spoke with {npc['name']}.",
            "actor_id": context.character["id"],
            "target_id": npc["id"],
            "mechanical_details": {
                "npc_name": npc["name"],
                "npc_description": npc.get("description", ""),
                "npc_dialogue_tags": npc.get("dialogue_tags") or [],
                "player_input": dialogue_content if not is_greeting else "",
                "is_greeting": is_greeting,
                "asking_for_quest": asking_for_quest,
                "quest_hook": quest_hook,
                "npc_personality": self._get_personality(npc),
                "npc_history": npc_history,
                "relationship": relationship,
                "active_quests": self._get_npc_quests(npc, context),
            },
        }]

        # Store conversation summary as NPC fact in RAG
        if not is_greeting:
            self._record_conversation(npc, dialogue_content, context)
        else:
            self._record_conversation(npc, f"Player approached {npc['name']} to talk.", context)

        # Update interaction count
        self._update_relationship(npc, context)

        return ActionResult(
            action_id=action.id, success=True,
            outcome_description=f"You approach {npc['name']} to speak with them.",
            events=events,
        )

    def get_available_actions(self, context: GameContext) -> list[dict]:
        npcs = [e for e in context.entities if e.get("entity_type") == "npc" and e.get("is_alive", True)]
        return [{"action_type": "talk", "target": npc["name"], "description": f"Talk to {npc['name']}"} for npc in npcs]

    # -- Dialogue Extraction --

    def _extract_dialogue(self, raw_input: str, npc_name: str) -> str:
        """Extract actual dialogue content from raw input, stripping command prefix and NPC name.

        Returns empty string if the input is just a conversation initiation (e.g. "talk to tom").
        Returns the dialogue topic/content if present (e.g. "talk to tom about wolves" → "wolves").
        """
        text = raw_input.strip()
        # Remove talk command prefix ("talk to ", "speak with ", etc.)
        text = _TALK_PREFIX.sub("", text)
        # Remove NPC name at the start (case-insensitive)
        if text.lower().startswith(npc_name.lower()):
            text = text[len(npc_name):].strip()
        # Strip leading connecting words
        text = re.sub(r"^(?:about|regarding|concerning|that)\s+", "", text, flags=re.I)
        return text.strip(" ?.!")

    # -- NPC Memory --

    def _get_quest_hook(self, npc: dict) -> str:
        """Get the NPC's quest hook from properties, falling back to TOML data."""
        props = safe_props(npc)
        hook = props.get("quest_hook", "")
        if hook:
            return hook
        # Fallback: load from static TOML content (covers existing games
        # where properties weren't saved to the DB)
        try:
            from text_rpg.content.loader import load_all_npcs
            static_npcs = load_all_npcs()
            static = static_npcs.get(npc.get("id", ""), {})
            return static.get("properties", {}).get("quest_hook", "")
        except Exception:
            return ""

    def _get_npc_history(self, npc: dict, context: GameContext) -> list[str]:
        """Retrieve conversation history with this NPC from RAG."""
        from text_rpg.storage.memory import MemoryStore
        from text_rpg.storage.database import Database
        db = getattr(self._repos.get("save_game"),"db",None) if self._repos else None
        if not isinstance(db,Database):
            return []
        history = [f"{row['speaker_id']} said: {row['text']}" for row in MemoryStore(db).conversation(context.game_id,npc['id'])]
        # Engine-recorded events witnessed by this NPC are stronger evidence
        # than generated recollection and remain available without embeddings.
        ledger = self._repos.get('event_ledger') if self._repos else None
        if ledger:
            witnessed = []
            for event in ledger.get_recent(context.game_id, limit=50):
                details = safe_json(event.get('mechanical_details'), {})
                saw_event = event.get('target_id') == npc['id'] or npc['id'] in details.get('witness_ids', [])
                if saw_event and event.get('event_type') in {
                    'SOCIAL_OFFENSE', 'PERSONAL_ACTION', 'DEATH', 'REVIVAL',
                    'CREATIVE_ACTION', 'SCENE_CHANGE', 'VANDALISM',
                }:
                    witnessed.append(event)
            history.extend(f"You witnessed: {event['description']}" for event in reversed(witnessed[-5:]))
        return history

    def _get_personality(self, npc: dict) -> str:
        """Get NPC personality from properties."""
        props = safe_props(npc)
        return props.get("personality", ", ".join(npc.get("dialogue_tags") or []))

    def _get_relationship(self, npc: dict) -> dict:
        """Get the NPC's relationship with the player."""
        props = safe_props(npc)
        relationships = props.get("relationships", {})
        return relationships.get("player", {
            "disposition": "neutral",
            "trust": 0,
            "interactions": 0,
        })

    def _get_npc_quests(self, npc: dict, context: GameContext) -> list[str]:
        """Get this NPC's quests with objective progress, for dialogue grounding."""
        from text_rpg.mechanics.quest_progress import is_ready_for_turn_in, objective_progress_text

        npc_id = npc.get("id", "")
        quests = []
        for q in (context.active_quests or []):
            if q.get("quest_giver_id") != npc_id:
                continue
            parts = [f"{q.get('name', 'Unknown quest')} ({q.get('status', 'active')})"]
            for obj in q.get("objectives", []):
                state = "done" if obj.get("is_complete") else "pending"
                parts.append(f"{objective_progress_text(obj)} [{state}]")
            if is_ready_for_turn_in(q):
                parts.append("READY TO TURN IN — the player has finished the work")
            quests.append("; ".join(parts))
        return quests

    def _find_available_quest(self, context: GameContext, npc_name: str | None) -> dict | None:
        """Newest available quest, preferring one whose giver matches npc_name or is present."""
        ws = self._repos.get("world_state")
        if not ws:
            return None
        available = [q for q in ws.get_all_quests(context.game_id) if q.get("status") == "available"]
        if not available:
            return None

        def giver_entity(quest):
            giver = quest.get("quest_giver_id", "")
            return next((e for e in context.entities if e.get("id") == giver), None)

        if npc_name:
            wanted = npc_name.lower()
            for quest in available:
                entity = giver_entity(quest)
                if entity and wanted in entity.get("name", "").lower():
                    return quest
        # Only quests whose giver is actually here can be accepted — never
        # activate an offer from an NPC the player hasn't met.
        for quest in available:
            if giver_entity(quest):
                return quest
        return None

    def _resolve_accept_quest(self, action: Action, context: GameContext) -> ActionResult:
        quest = self._find_available_quest(context, action.target_id)
        if not quest:
            return ActionResult(
                action_id=action.id, success=False,
                outcome_description="There's no quest on offer to accept right now.",
            )
        self._repos["world_state"].update_quest_status(quest["id"], context.game_id, "active")
        return ActionResult(
            action_id=action.id, success=True,
            outcome_description=f"You accept the quest: {quest.get('name', '')}.",
            events=[{
                "event_type": "QUEST_START",
                "description": f"Accepted quest: {quest.get('name', '')}.",
                "actor_id": context.character.get("id"),
                "mechanical_details": {
                    "quest_id": quest.get("id", ""),
                    "quest_name": quest.get("name", ""),
                },
            }],
        )

    def _resolve_decline_quest(self, action: Action, context: GameContext) -> ActionResult:
        quest = self._find_available_quest(context, action.target_id)
        if not quest:
            return ActionResult(
                action_id=action.id, success=False,
                outcome_description="There's no quest on offer to decline.",
            )
        return ActionResult(
            action_id=action.id, success=True,
            outcome_description=(
                f"You decline the quest: {quest.get('name', '')}. "
                "The offer remains open should you change your mind."
            ),
        )

    def _record_quest_event(self, quest_event: dict, context: GameContext) -> None:
        """Record a quest generation event to the event ledger."""
        if not self._repos:
            return
        try:
            import uuid
            from datetime import datetime, timezone
            event_dict = {
                "id": str(uuid.uuid4()),
                "game_id": context.game_id,
                "event_type": quest_event.get("event_type", "DIRECTOR_QUEST_AVAILABLE"),
                "turn_number": context.turn_number,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "actor_id": context.character.get("id"),
                "target_id": quest_event.get("target_id"),
                "location_id": quest_event.get("location_id", context.location.get("id")),
                "description": quest_event.get("description", ""),
                "mechanical_details": json.dumps(quest_event.get("mechanical_details", {})),
                "is_canonical": True,
            }
            self._repos["event_ledger"].append(event_dict)
        except Exception as e:
            logger.debug(f"Failed to record quest event: {e}")

    def _record_conversation(self, npc: dict, player_input: str, context: GameContext) -> None:
        """Store a conversation fact about this NPC in RAG."""
        # The turn renderer records both complete utterances after commit.
        return

    def _update_relationship(self, npc: dict, context: GameContext) -> None:
        """Increment the interaction count in NPC properties."""
        if not self._repos:
            return
        try:
            props = safe_props(npc)

            relationships = props.get("relationships", {})
            player_rel = relationships.get("player", {
                "disposition": "neutral",
                "trust": 0,
                "interactions": 0,
            })
            player_rel["interactions"] = player_rel.get("interactions", 0) + 1
            relationships["player"] = player_rel
            props["relationships"] = relationships

            self._repos["entity"].update_field(npc["id"], "properties", json.dumps(props))
        except Exception as e:
            logger.debug(f"Failed to update NPC relationship: {e}")

    # -- Quest Negotiation --

    def _try_negotiate_quest(
        self, action: Action, npc: dict, context: GameContext
    ) -> ActionResult | None:
        """Attempt to negotiate quest terms with an NPC."""
        # Find an active or available quest from this NPC
        npc_id = npc.get("id", "")
        target_quest = None
        for q in (context.active_quests or []):
            if q.get("quest_giver_id") == npc_id:
                target_quest = q
                break

        # Also check available quests
        if not target_quest and self._repos:
            try:
                from text_rpg.storage.repos.world_state_repo import WorldStateRepo
                with self._repos["world_state"].db.get_connection() as conn:
                    rows = conn.execute(
                        "SELECT * FROM quests WHERE game_id = ? AND quest_giver_id = ? AND status IN ('active', 'available')",
                        (context.game_id, npc_id),
                    ).fetchall()
                for row in rows:
                    target_quest = dict(row)
                    # Deserialize JSON fields
                    for field in ("objectives", "item_rewards"):
                        target_quest[field] = safe_json(target_quest.get(field), [])
                    break
            except Exception:
                pass

        if not target_quest:
            return None  # No quest to negotiate — fall through to normal dialogue

        # Run a Persuasion check
        char = context.character
        scores = safe_json(char.get("ability_scores"), {})
        cha_score = scores.get("charisma", 10)

        skill_profs = safe_json(char.get("skill_proficiencies"), [])
        is_prof = "persuasion" in skill_profs or "deception" in skill_profs

        prof_bonus = char.get("proficiency_bonus", 2)

        # DC based on NPC trust/disposition
        relationship = self._get_relationship(npc)
        trust = relationship.get("trust", 0)
        base_dc = 15
        dc = max(8, base_dc - trust)

        persuasion_skill = "persuasion" if "persuasion" in skill_profs else "deception"
        success, roll_result = skill_check(cha_score, prof_bonus, is_prof, dc)

        dice_rolls = [DiceRoll(
            dice_expression="1d20",
            rolls=roll_result.individual_rolls,
            modifier=roll_result.modifier,
            total=roll_result.total,
            purpose=f"persuasion_check (DC {dc})",
        )]

        self._persuasion_skill_event = {
            "event_type": "SKILL_CHECK",
            "description": f"{persuasion_skill} check (DC {dc}) — {'success' if success else 'failure'}",
            "actor_id": char.get("id", ""),
            "mechanical_details": {
                "skill": persuasion_skill, "dc": dc,
                "success": success, "roll": roll_result.total,
            },
        }

        # Ask LLM to evaluate the negotiation
        from text_rpg.systems.director.generators import negotiate_quest

        try:
            negotiation = negotiate_quest(
                self._director.llm,
                target_quest,
                npc,
                action.raw_input,
                roll_result.total,
                dc,
                success,
            )
        except Exception as e:
            logger.warning(f"Quest negotiation LLM call failed: {e}")
            return None

        npc_response = negotiation.get("npc_response", "The NPC considers your words.")

        if negotiation.get("accepted", False) and success:
            # Merge negotiated terms onto the real objectives — negotiation may
            # only soften counts/descriptions, never rewrite the quest schema.
            proposed = negotiation.get("modified_objectives", [])
            if proposed and isinstance(proposed, list) and self._repos:
                from text_rpg.mechanics.quest_progress import merge_negotiated_objectives

                try:
                    original = safe_json(target_quest.get("objectives"), [])
                    merged = merge_negotiated_objectives(original, proposed)
                    self._repos["world_state"].save_quest({
                        **target_quest,
                        "game_id": context.game_id,
                        "objectives": merged,
                    })
                except Exception as e:
                    logger.warning(f"Failed to save negotiated quest: {e}")
                    raise

            # Update disposition
            disposition_change = negotiation.get("disposition_change", 0)
            self._adjust_disposition(npc, disposition_change)

            return ActionResult(
                action_id=action.id, success=True,
                outcome_description=f"You negotiate with {npc['name']}.",
                dice_rolls=dice_rolls,
                events=[
                    self._persuasion_skill_event,
                    {
                        "event_type": "QUEST_NEGOTIATION",
                        "description": f"Successfully negotiated quest terms with {npc['name']}.",
                        "actor_id": context.character.get("id"),
                        "target_id": npc["id"],
                        "mechanical_details": {
                            "npc_name": npc["name"],
                            "npc_response": npc_response,
                            "quest_id": target_quest.get("id", ""),
                            "quest_name": target_quest.get("name", ""),
                            "accepted": True,
                            "player_input": action.raw_input,
                        },
                    },
                ],
            )
        else:
            # Negotiation failed
            self._adjust_disposition(npc, negotiation.get("disposition_change", 0))

            return ActionResult(
                action_id=action.id, success=False,
                outcome_description=f"You try to negotiate with {npc['name']}, but they aren't convinced.",
                dice_rolls=dice_rolls,
                events=[
                    self._persuasion_skill_event,
                    {
                        "event_type": "QUEST_NEGOTIATION",
                        "description": f"Failed to negotiate quest terms with {npc['name']}.",
                        "actor_id": context.character.get("id"),
                        "target_id": npc["id"],
                        "mechanical_details": {
                            "npc_name": npc["name"],
                            "npc_response": npc_response,
                            "quest_id": target_quest.get("id", ""),
                            "quest_name": target_quest.get("name", ""),
                            "accepted": False,
                            "player_input": action.raw_input,
                        },
                    },
                ],
            )

    def _adjust_disposition(self, npc: dict, change: int) -> None:
        """Adjust the NPC's disposition toward the player."""
        if not change or not self._repos:
            return
        try:
            props = safe_props(npc)

            relationships = props.get("relationships", {})
            player_rel = relationships.get("player", {
                "disposition": "neutral",
                "trust": 0,
                "interactions": 0,
            })

            trust = player_rel.get("trust", 0) + change
            trust = max(-5, min(5, trust))
            player_rel["trust"] = trust

            # Update disposition label based on trust
            if trust >= 3:
                player_rel["disposition"] = "friendly"
            elif trust >= 1:
                player_rel["disposition"] = "warm"
            elif trust <= -3:
                player_rel["disposition"] = "hostile"
            elif trust <= -1:
                player_rel["disposition"] = "suspicious"
            else:
                player_rel["disposition"] = "neutral"

            relationships["player"] = player_rel
            props["relationships"] = relationships
            self._repos["entity"].update_field(npc["id"], "properties", json.dumps(props))
        except Exception as e:
            logger.debug(f"Failed to adjust disposition: {e}")
