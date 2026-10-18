"""Main Director class — post-turn evaluator that generates living world content."""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from text_rpg.llm.provider import LLMProvider
from text_rpg.models.action import Action, ActionResult, DiceRoll, StateMutation
from text_rpg.rag.indexer import Indexer
from text_rpg.rag.retriever import Retriever
from text_rpg.systems.base import GameContext
from text_rpg.systems.director import triggers
from text_rpg.utils import safe_json
from text_rpg.systems.director.schemas import validate_plausibility

logger = logging.getLogger(__name__)

# Cooldowns: minimum turns between generation types
_COOLDOWNS: dict[str, int] = {
    "npc": 5,
    "location": 3,
    "quest": 8,
    "enrich": 6,
    "pacing": 10,
    "story": 15,
    "faction_goals": 15,
    "world_event": 5,
    "region": 20,
    "arcane_location": 25,
    "guild": 20,
    "bounty_hunter": 15,
}


class Director:
    """Post-turn evaluator that injects procedural content into the world.

    NOT a GameSystem — called from the turn loop after mutations are applied.
    Generated content becomes visible on the *next* turn.
    """

    def __init__(
        self,
        llm: LLMProvider,
        retriever: Retriever,
        indexer: Indexer,
    ) -> None:
        self.llm = llm
        self.retriever = retriever
        self.indexer = indexer
        # Track last generation turn per type to enforce cooldowns
        self._last_generation: dict[str, int] = {}

    def evaluate(
        self,
        context: GameContext,
        action_result: ActionResult,
        repos: dict[str, Any],
    ) -> list[dict]:
        """Called every turn after mutations are applied.

        Returns a list of event dicts to record (may be empty).
        At most one generation per turn.
        """
        events: list[dict] = []

        # 1. Check for quest follow-ups on completed quests
        for event in action_result.events:
            if event.get("event_type") == "QUEST_COMPLETE":
                quest_id = event.get("target_id")
                if quest_id:
                    quest = repos["world_state"].get_quest(quest_id, context.game_id)
                    if quest and triggers.should_generate_follow_up(quest, context):
                        result = self._try_generate_follow_up(quest, context, repos)
                        if result:
                            events.append(result)
                            return events

        # 2. Should we spawn an NPC?
        if self._can_generate("npc", context.turn_number):
            if triggers.should_spawn_npc(context, repos):
                result = self._try_generate_npc(context, repos)
                if result:
                    events.append(result)
                    return events

        # 3. Should we offer a quest from an existing NPC?
        if self._can_generate("quest", context.turn_number):
            for entity in context.entities:
                if entity.get("entity_type") == "npc" and entity.get("is_alive", True):
                    if triggers.should_offer_quest(entity, context):
                        result = self._try_prepare_quest(entity, context, repos)
                        if result:
                            events.append(result)
                            return events

        # 4. Should we enrich the location?
        if self._can_generate("enrich", context.turn_number):
            if triggers.should_enrich_location(context):
                result = self._try_enrich_location(context, repos)
                if result:
                    events.append(result)
                    return events

        # 5. Pacing check — seed hooks every N turns
        if self._can_generate("pacing", context.turn_number):
            if triggers.pacing_check(context):
                result = self._try_seed_hooks(context, repos)
                if result:
                    events.append(result)
                    return events

        # 6. Should we reveal a new region?
        if self._can_generate("region", context.turn_number):
            result = self._try_reveal_region(context, repos)
            if result:
                events.append(result)
                return events

        # 6b. Should we hint at guild recruitment for skilled crafters?
        if self._can_generate("guild", context.turn_number):
            if triggers.should_offer_guild_recruitment(context, repos):
                self._last_generation["guild"] = context.turn_number
                events.append({
                    "event_type": "STORY_BEAT",
                    "description": (
                        "Your crafting skill has not gone unnoticed. "
                        "A guild representative may be looking for talented artisans like you."
                    ),
                    "mechanical_details": {"hint": "guild_recruitment_nearby"},
                })
                return events

        # 6c. Should we spawn an arcane location for spell inventors?
        if self._can_generate("arcane_location", context.turn_number):
            if triggers.should_spawn_arcane_location(context, repos):
                self._last_generation["arcane_location"] = context.turn_number
                events.append({
                    "event_type": "STORY_BEAT",
                    "description": (
                        "Your growing mastery of spell creation draws attention. "
                        "Rumors speak of a hidden arcane tower nearby, where ancient "
                        "mages once forged spells of incredible power."
                    ),
                    "mechanical_details": {"hint": "arcane_location_nearby"},
                })
                return events

        # 6d. Bounty hunters come for wanted players in the wilds
        if self._can_generate("bounty_hunter", context.turn_number):
            bounty_event = self._try_spawn_bounty_hunter(context, repos)
            if bounty_event:
                events.append(bounty_event)
                self._last_generation["bounty_hunter"] = context.turn_number
                return events

        # 7. Story progression — check story seed beats
        # The regional campaign owns long-form progression while active.
        # Legacy seed stories can still run in regions without one, but must
        # not inject a second unrelated plot into the same scene.
        campaign_active = False
        try:
            from text_rpg.engine.campaign import CampaignRuntime
            from text_rpg.storage.database import Database
            if isinstance(getattr(repos.get('save_game'), 'db', None), Database):
                story = CampaignRuntime(repos).get(context.game_id)
                campaign_active = bool(story and story['status'] == 'active')
        except Exception:
            pass
        story_events = [] if campaign_active else self._check_story_progression(context, repos)
        if story_events:
            events.extend(story_events)

        # 8. Faction goals — autonomous faction actions
        if self._can_generate("faction_goals", context.turn_number):
            faction_events = self._check_faction_goals(context, repos)
            if faction_events:
                events.extend(faction_events)
                self._last_generation["faction_goals"] = context.turn_number

        # 9. World events — random ambient events
        if self._can_generate("world_event", context.turn_number):
            world_events = self._check_world_events(context, repos)
            if world_events:
                events.extend(world_events)
                self._last_generation["world_event"] = context.turn_number

        return events

    def evaluate_plausibility(
        self,
        action: Action,
        context: GameContext,
    ) -> dict:
        """Evaluate how plausible a creative/unhandled player action is.

        Returns dict with: plausibility, skill, ability, reasoning,
        success_description, failure_description.
        """
        from text_rpg.systems.director.generators import evaluate_plausibility

        return evaluate_plausibility(self.llm, action.raw_input, context)

    def generate_creative_outcome(
        self,
        action: Action,
        context: GameContext,
        plausibility: dict,
        repos: dict[str, Any],
        roll_total: int = 0,
        dc: int = 10,
        roll_succeeded: bool = True,
    ) -> ActionResult:
        """Resolve a creative action AFTER the roll into real, applied effects.

        The LLM proposes typed effects from a closed menu; only effects that
        survive validation are applied and recorded — narration and the event
        ledger describe exactly what happened, nothing more.
        Raises LLMUnavailableError when the provider is down.
        """
        from text_rpg.content.loader import load_all_items
        from text_rpg.llm.errors import LLMMalformedOutputError
        from text_rpg.mechanics.creative_effects import (
            WorldFacts,
            creative_budget,
            effect_menu_text,
            effects_to_mutations,
            effects_to_summary,
            validate_effects,
        )
        from text_rpg.systems.director.generators import generate_creative_outcome

        def persistent_alteration():
            """Build one bounded location mutation from a successful physical act."""
            if not roll_succeeded:
                return None, None
            from text_rpg.engine.scene_detail import durable_scene_change
            from text_rpg.models.action import StateMutation
            change = durable_scene_change(
                action.raw_input,
                context.character.get("name", "The player"),
                action.id,
                context.world_time,
                [entity.get("name", "") for entity in context.entities if entity.get("is_alive", True)],
                " ".join((context.location.get("name", ""), context.location.get("description", ""))),
            )
            if not change:
                return None, None
            props = dict(safe_json(context.location.get("properties"), {}))
            changes = [entry for entry in props.get("scene_changes", [])
                       if isinstance(entry, dict) and entry.get("id") != change["id"]]
            changes.append(change)
            props["scene_changes"] = changes[-20:]
            mutation = StateMutation(
                target_type="location", target_id=context.location.get("id", ""),
                field="properties", old_value=context.location.get("properties"), new_value=props,
            )
            return change, mutation

        skill = plausibility.get("skill", "athletics")
        level = context.character.get("level", 1)
        char_id = context.character.get("id")

        try:
            catalog = load_all_items()
        except Exception:
            catalog = {}
        location_items = [
            i for i in safe_json(context.location.get("items"), []) if isinstance(i, str)
        ]
        inventory_ids = {
            e.get("item_id", "") for e in safe_json((context.inventory or {}).get("items"), [])
        }
        alive_entities = {
            e["id"]: e for e in context.entities if e.get("is_alive", True) and e.get("id")
        }
        exit_directions = {
            c.get("direction", "").lower()
            for c in safe_json(context.location.get("connections"), [])
            if isinstance(c, dict)
        }

        facts = WorldFacts(
            catalog_item_ids=set(catalog.keys()),
            location_item_ids=set(location_items),
            inventory_item_ids=inventory_ids,
            present_entity_ids=set(alive_entities.keys()),
            existing_exit_directions=exit_directions,
            allowed_effects={"narrative_only"},
        )

        # Item candidates shown to the model: what's here plus a slice of the
        # catalog (mundane items), so ids in gain_item are always real.
        candidate_ids = location_items + sorted(inventory_ids) + sorted(catalog.keys())[:40]
        available_items = ", ".join(dict.fromkeys(i for i in candidate_ids if i)) or "none"

        budget = creative_budget(level)
        # At present creative actions intentionally have no authority to mint
        # loot, exits, creatures, healing, or social state. A second model call
        # could therefore only return an effect that is discarded. Resolve the
        # roll directly and keep the common local-model path responsive.
        if facts.allowed_effects == {"narrative_only"}:
            change, mutation = persistent_alteration()
            if roll_succeeded:
                text = (f"Your work leaves a lasting change here: {change['description']}." if change
                        else f"You succeed at the attempt: {action.raw_input.strip()}")
            else:
                text = f"Your attempt fails: {action.raw_input.strip()}"
            return ActionResult(
                action_id=action.id,
                success=roll_succeeded,
                outcome_description=text,
                state_mutations=[mutation] if mutation else [],
                events=[{
                    "event_type": "CREATIVE_ACTION" if roll_succeeded else "CREATIVE_ACTION_FAIL",
                    "description": text,
                    "actor_id": char_id,
                    "location_id": context.location.get("id"),
                    "mechanical_details": {"effects": [], "dc": dc, "roll": roll_total,
                                           "scene_change": change,
                                           "witness_ids": list(alive_entities)},
                }],
                xp_gained=0,
            )
        try:
            proposal = generate_creative_outcome(
                self.llm, action.raw_input, context,
                skill=skill, roll_total=roll_total, dc=dc,
                roll_succeeded=roll_succeeded,
                available_items=available_items,
                effect_menu=effect_menu_text(level),
                budget=budget,
            )
        except LLMMalformedOutputError:
            # Safe refusal: a true statement with no fabricated state.
            change, mutation = persistent_alteration()
            text = (f"Your work leaves a lasting change here: {change['description']}." if change
                    else "No supported mechanical change occurs." if roll_succeeded
                    else "Your attempt comes to nothing.")
            return ActionResult(
                action_id=action.id,
                success=roll_succeeded,
                outcome_description=text,
                state_mutations=[mutation] if mutation else [],
                events=[{
                    "event_type": "CREATIVE_ACTION" if roll_succeeded else "CREATIVE_ACTION_FAIL",
                    "description": f"Creative action: {action.raw_input} — {text}",
                    "actor_id": char_id,
                    "location_id": context.location.get("id"),
                    "mechanical_details": {"scene_change": change} if change else {},
                }],
                xp_gained=0,
            )

        effects, dropped = validate_effects(
            proposal.get("effects", []), facts, level, roll_succeeded,
        )
        if dropped:
            logger.info(f"Creative effects dropped: {dropped}")

        mutations = effects_to_mutations(effects, context.character, alive_entities)
        change, change_mutation = persistent_alteration()
        if change_mutation:
            mutations.append(change_mutation)

        extra_events: list[dict] = []

        item_names = {iid: data.get("name", iid) for iid, data in catalog.items()}
        entity_names = {eid: e.get("name", "Someone") for eid, e in alive_entities.items()}
        factual = effects_to_summary(effects, item_names, entity_names)
        change_text = f"Your work leaves a lasting change here: {change['description']}." if change else ""
        outcome = " ".join(part for part in (factual, change_text) if part) or "No supported mechanical change occurs."
        event_desc = outcome if (factual or change) else "Creative attempt produced no mechanical change."
        events = [{
            "event_type": "CREATIVE_ACTION" if roll_succeeded else "CREATIVE_ACTION_FAIL",
            "description": event_desc,
            "actor_id": char_id,
            "location_id": context.location.get("id"),
            "mechanical_details": {"effects": effects, "dc": dc, "roll": roll_total,
                                   "scene_change": change},
        }] + extra_events

        return ActionResult(
            action_id=action.id,
            success=roll_succeeded,
            outcome_description=outcome,
            state_mutations=mutations,
            events=events,
            xp_gained=0,
        )

    def generate_location_for_direction(
        self,
        direction: str,
        context: GameContext,
        repos: dict[str, Any],
    ) -> dict | None:
        """Generate a new location when the player discovers an unknown path.

        Returns the new location dict (already saved to DB) or None on failure.
        Supports sandbox-style region crossing — when the player has explored
        deep enough past content boundaries, new locations may belong to a
        different region.
        """
        from text_rpg.systems.director.generators import generate_location, fallback_location

        try:
            location_data = generate_location(
                self.llm, context, direction, context.location,
            )
        except (ValueError, ConnectionError) as e:
            logger.warning(f"Location generation failed; using a grounded outdoor site: {e}")
            location_data = fallback_location(context.location, direction)
        except Exception as e:
            from text_rpg.llm.errors import LLMUnavailableError, LLMMalformedOutputError
            if not isinstance(e, (LLMUnavailableError, LLMMalformedOutputError)):
                raise
            logger.warning(f"Location generation failed; using a grounded outdoor site: {e}")
            location_data = fallback_location(context.location, direction)

        source_loc_id = context.location.get("id", "")
        new_loc_id = location_data["id"]

        # Determine region — check if we should cross into a new region
        current_region_id = context.location.get("region_id", "")
        new_region_id = self._maybe_cross_region(
            current_region_id, context, repos,
        )

        # Set game_id and region
        location_data["game_id"] = context.game_id
        location_data["region_id"] = new_region_id

        # Save new location (without embedded connections)
        location_data["connections"] = json.dumps([])
        repos["location"].save(_serialize_location(location_data))

        # Create bidirectional connections in dedicated table
        reverse_dir = _reverse_direction(direction)
        conn_repo = repos.get("connection")
        if conn_repo:
            conn_repo.add_bidirectional(
                game_id=context.game_id,
                source_id=source_loc_id,
                target_id=new_loc_id,
                direction=direction,
                reverse_direction=reverse_dir,
                description=location_data.get("name", "a new area"),
                back_description=context.location.get("name", "the way back"),
            )
        else:
            # Fallback: update embedded JSON (legacy path)
            back_conn = {
                "direction": reverse_dir,
                "target_location_id": source_loc_id,
                "description": context.location.get("name", "the way back"),
                "is_locked": False,
            }
            new_conns = safe_json(location_data.get("connections"), [])
            new_conns.append(back_conn)
            repos["location"].update_field(new_loc_id, context.game_id, "connections", new_conns)

            src_conns = safe_json(context.location.get("connections"), [])
            src_conns.append({
                "direction": direction,
                "target_location_id": new_loc_id,
                "description": location_data.get("name", "a new area"),
                "is_locked": False,
            })
            repos["location"].update_field(source_loc_id, context.game_id, "connections", src_conns)

        # If we crossed into a freshly seeded content region, connect this
        # border location onward to the region's entry settlement.
        pending_entry = getattr(self, "_pending_region_entry", None)
        if new_region_id != current_region_id and pending_entry and conn_repo:
            try:
                conn_repo.add_bidirectional(
                    game_id=context.game_id,
                    source_id=new_loc_id,
                    target_id=pending_entry,
                    direction="onward",
                    reverse_direction="back",
                    description="The road onward into the region",
                    back_description=location_data.get("name", "the way back"),
                )
            except Exception as e:
                logger.warning(f"Failed to connect border location to region entry: {e}")
            self._pending_region_entry = None

        # Optionally generate NPCs for the new location
        self._populate_new_location(location_data, context, repos)

        # Index to RAG
        try:
            self.indexer.index_lore(
                f"Location discovered: {location_data['name']} — {location_data.get('description', '')}",
                category="location",
                tags={"game_id": context.game_id, "location_id": new_loc_id},
            )
        except Exception:
            pass

        self._last_generation["location"] = context.turn_number
        return location_data

    # -- Private generation methods --

    def _can_generate(self, gen_type: str, turn_number: int) -> bool:
        """Check cooldown for a generation type."""
        last = self._last_generation.get(gen_type, -999)
        cooldown = _COOLDOWNS.get(gen_type, 5)
        return (turn_number - last) >= cooldown

    def _try_generate_npc(
        self, context: GameContext, repos: dict[str, Any]
    ) -> dict | None:
        """Attempt to generate and save a new NPC at the current location."""
        from text_rpg.systems.director.generators import generate_npc

        try:
            npc_data = generate_npc(self.llm, context, context.location, {})
        except Exception as e:
            logger.warning(f"NPC generation failed: {e}")
            return None

        # Scale NPC level to player level ± 2, clamped to region range
        npc_data = _scale_npc_to_player(npc_data, context)

        npc_data["game_id"] = context.game_id
        npc_data["location_id"] = context.location.get("id", "")

        repos["entity"].save(_serialize_entity(npc_data))

        # Index to RAG
        try:
            self.indexer.index_npc_fact(
                context.game_id,
                npc_data["id"],
                npc_data["name"],
                f"New NPC appeared: {npc_data.get('description', '')}",
            )
        except Exception:
            pass

        self._last_generation["npc"] = context.turn_number
        return {
            "event_type": "DIRECTOR_NPC_SPAWN",
            "description": f"A new figure appears: {npc_data['name']}.",
            "target_id": npc_data["id"],
            "location_id": context.location.get("id"),
        }

    def _quest_candidates(self, context: GameContext, repos: dict[str, Any]) -> dict:
        """Real-id sets that generated quest objectives must target."""
        from text_rpg.content.loader import load_all_items, load_region

        candidates: dict[str, set[str]] = {
            "locations": set(), "npcs": set(), "entity_types": set(), "items": set(),
        }
        try:
            for loc in repos["location"].get_all(context.game_id):
                if loc.get("id"):
                    candidates["locations"].add(loc["id"])
        except Exception:
            pass
        try:
            for entity in repos["entity"].get_by_game(context.game_id):
                if not entity.get("is_alive", True):
                    continue
                if entity.get("entity_type") == "npc":
                    candidates["npcs"].add(entity.get("id", ""))
                elif entity.get("is_hostile") and entity.get("name"):
                    candidates["entity_types"].add(entity["name"].lower().replace(" ", "_"))
        except Exception:
            pass
        try:
            region_id = context.location.get("region_id", "")
            if region_id:
                for enc in load_region(region_id).get("encounters", []):
                    for ent in enc.get("entities", []):
                        if ent.get("name"):
                            candidates["entity_types"].add(ent["name"].lower().replace(" ", "_"))
        except Exception:
            pass
        try:
            candidates["items"] = set(load_all_items().keys())
        except Exception:
            pass
        candidates["npcs"].discard("")
        candidates["locations"].discard("")
        return candidates

    def _fallback_quest(self, npc: dict, context: GameContext) -> dict:
        """Deterministic template quest built entirely from real ids."""
        objectives = []
        connections = safe_json(context.location.get("connections"), [])
        target_loc = next(
            (c.get("target_location_id") for c in connections
             if isinstance(c, dict) and c.get("target_location_id")),
            None,
        )
        if target_loc:
            objectives.append({
                "id": str(uuid.uuid4()),
                "description": f"Scout the area {target_loc.replace('_', ' ')}",
                "objective_type": "travel",
                "target_location_id": target_loc,
                "target_count": 1, "current_count": 0,
                "is_complete": False, "negotiable": False,
            })
        objectives.append({
            "id": str(uuid.uuid4()),
            "description": f"Report back to {npc.get('name', 'the quest giver')}",
            "objective_type": "talk",
            "target_npc_id": npc.get("id", ""),
            "target_count": 1, "current_count": 0,
            "is_complete": False, "negotiable": False,
        })
        return {
            "id": str(uuid.uuid4()),
            "name": f"An Errand for {npc.get('name', 'a Stranger')}",
            "description": (
                f"{npc.get('name', 'Someone')} asks you to look into a small matter "
                "nearby and report back."
            ),
            "status": "available",
            "objectives": objectives,
            "xp_reward": 50, "gold_reward": 10, "item_rewards": [],
            "level_requirement": 1, "generated": True,
            "npc_motivation": "Needs a reliable pair of hands.",
            "completion_flexibility": "low",
        }

    def _try_spawn_bounty_hunter(
        self, context: GameContext, repos: dict[str, Any]
    ) -> dict | None:
        """Spawn a hostile bounty hunter when the player's bounty runs high."""
        rep_repo = repos.get("reputation")
        if not rep_repo:
            return None
        try:
            region_id = context.location.get("region_id", "")
            if not region_id:
                return None
            bounty = rep_repo.get_bounty(context.game_id, region_id) or {}
            bounty_amount = bounty.get("amount", 0)
            if not triggers.should_spawn_bounty_hunter(context, bounty_amount):
                return None

            from text_rpg.systems.director.schemas import validate_npc

            npc_data = validate_npc({
                "name": "Bounty Hunter",
                "description": (
                    "A grim, well-armed tracker with a wanted notice bearing your "
                    "likeness tucked into their belt."
                ),
                "is_hostile": True,
                "attacks": [{"name": "Crossbow", "damage_dice": "1d8", "damage_type": "piercing"}],
            })
            npc_data = _scale_npc_to_player(npc_data, context)
            npc_data["game_id"] = context.game_id
            npc_data["location_id"] = context.location.get("id", "")
            props = safe_json(npc_data.get("properties"), {})
            props["bounty_hunter"] = True
            props["bounty_amount"] = bounty_amount
            npc_data["properties"] = props
            repos["entity"].save(_serialize_entity(npc_data))

            return {
                "event_type": "GUARD_CONFRONTATION",
                "description": (
                    f"A bounty hunter steps onto the path, crossbow leveled. "
                    f"The {bounty_amount} gold on your head has drawn the wrong kind of attention."
                ),
                "target_id": npc_data["id"],
                "location_id": context.location.get("id"),
                "mechanical_details": {"bounty_amount": bounty_amount},
            }
        except Exception as e:
            logger.warning(f"Bounty hunter spawn failed: {e}")
            return None

    def _try_prepare_quest(
        self, npc: dict, context: GameContext, repos: dict[str, Any]
    ) -> dict | None:
        """Generate a quest from an NPC's quest hook and save it."""
        from text_rpg.llm.errors import LLMUnavailableError
        from text_rpg.systems.director.generators import generate_quest

        candidates = self._quest_candidates(context, repos)
        candidates["npcs"].add(npc.get("id", ""))
        try:
            quest_data = generate_quest(self.llm, context, npc, candidates)
        except LLMUnavailableError:
            raise
        except Exception as e:
            logger.warning(f"Quest generation failed, using template fallback: {e}")
            quest_data = self._fallback_quest(npc, context)

        quest_data["game_id"] = context.game_id
        quest_data["quest_giver_id"] = npc.get("id", "")

        repos["world_state"].save_quest(_serialize_quest(quest_data))

        # Index to RAG
        try:
            self.indexer.index_lore(
                f"Quest available: {quest_data['name']} — {quest_data.get('description', '')}",
                category="quest",
                tags={"game_id": context.game_id, "quest_id": quest_data["id"]},
            )
        except Exception:
            pass

        self._last_generation["quest"] = context.turn_number
        return {
            "event_type": "DIRECTOR_QUEST_AVAILABLE",
            "description": f"{npc['name']} seems to have something on their mind.",
            "target_id": quest_data["id"],
            "location_id": context.location.get("id"),
            "mechanical_details": {
                "quest_name": quest_data.get("name", ""),
                "quest_description": quest_data.get("description", ""),
                "quest_giver": npc.get("name", ""),
            },
        }

    def _try_generate_follow_up(
        self, completed_quest: dict, context: GameContext, repos: dict[str, Any]
    ) -> dict | None:
        """Generate a follow-up quest after completion."""
        from text_rpg.systems.director.generators import generate_follow_up_quest

        try:
            candidates = self._quest_candidates(context, repos)
            quest_data = generate_follow_up_quest(self.llm, context, completed_quest, candidates)
        except Exception as e:
            logger.warning(f"Follow-up quest generation failed: {e}")
            return None

        quest_data["game_id"] = context.game_id
        quest_data["quest_giver_id"] = completed_quest.get("quest_giver_id", "")

        repos["world_state"].save_quest(_serialize_quest(quest_data))

        self._last_generation["quest"] = context.turn_number
        return {
            "event_type": "DIRECTOR_QUEST_FOLLOW_UP",
            "description": "A new opportunity arises from your completed quest.",
            "target_id": quest_data["id"],
            "location_id": context.location.get("id"),
            "mechanical_details": {
                "quest_name": quest_data.get("name", ""),
                "quest_description": quest_data.get("description", ""),
            },
        }

    def _try_enrich_location(
        self, context: GameContext, repos: dict[str, Any]
    ) -> dict | None:
        """Add flavour to an empty location — perhaps an NPC or item."""
        result = self._try_generate_npc(context, repos)
        if result:
            self._last_generation["enrich"] = context.turn_number
            return result
        return None

    def _try_seed_hooks(
        self, context: GameContext, repos: dict[str, Any]
    ) -> dict | None:
        """Pacing check — save an intent for future content."""
        # Check bounty level for tension context
        bounty_amount = 0
        rep_repo = repos.get("reputation")
        if rep_repo:
            region_id = context.location.get("region_id", "")
            if region_id:
                bounty = rep_repo.get_bounty(context.game_id, region_id)
                bounty_amount = bounty.get("amount", 0)

        intent = {
            "id": str(uuid.uuid4()),
            "game_id": context.game_id,
            "intent_type": "seed_hook",
            "description": "Director pacing review — world may need new content.",
            "data": {
                "turn_number": context.turn_number,
                "location_id": context.location.get("id"),
                "character_level": context.character.get("level", 1),
                "bounty_amount": bounty_amount,
                "loop_count": getattr(context, "loop_count", 0),
                "deja_vu": getattr(context, "loop_count", 0) > 0,
            },
            "is_active": True,
        }
        try:
            repos["intent"].save(intent)
        except Exception as e:
            logger.warning(f"Failed to save pacing intent: {e}")

        self._last_generation["pacing"] = context.turn_number
        return None  # Intents are silent — no event

    def _maybe_cross_region(
        self,
        current_region_id: str,
        context: GameContext,
        repos: dict[str, Any],
    ) -> str:
        """Determine if a newly generated location should be in a different region.

        Returns the region_id to assign. Usually the current region, but may
        return a different one for sandbox region transitions.

        Crossing happens when:
        - The player has generated 3+ locations past content boundaries in this region
        - OR the player level exceeds the current region's level_range_max
        """
        import random

        location_repo = repos.get("location")
        if not location_repo or not current_region_id:
            return current_region_id

        # Count how many generated (non-content) locations exist in this region
        try:
            region_locations = location_repo.get_by_region(context.game_id, current_region_id)
            generated_count = sum(
                1 for loc in region_locations
                if loc.get("generated", False)
            )
        except Exception:
            return current_region_id

        # Check player level vs region max
        player_level = context.character.get("level", 1)
        region_max = 5
        try:
            from text_rpg.content.loader import load_region
            region_data = load_region(current_region_id)
            region_max = region_data.get("level_range_max", 5)
        except Exception:
            pass

        # Cross region boundary if: 3+ generated locations OR player outlevels the region
        should_cross = generated_count >= 3 or player_level > region_max

        if not should_cross:
            return current_region_id

        # Only cross with 40% probability per attempt (keeps it natural, not every location)
        if random.random() > 0.4:
            return current_region_id

        # Find an unvisited content region to transition into
        try:
            from text_rpg.content.loader import load_all_regions

            all_regions = load_all_regions()
            all_game_locations = location_repo.get_all(context.game_id)
            visited_regions = {
                loc.get("region_id", "")
                for loc in all_game_locations
                if loc.get("visited")
            }

            # Prefer unvisited content regions within reach of the player level
            unvisited = [
                rid for rid in all_regions
                if rid not in visited_regions
                and rid != current_region_id
                and all_regions[rid].get("level_range_min", 1) <= player_level + 2
            ]
            if unvisited:
                chosen = random.choice(unvisited)
                logger.info(f"Region crossing: {current_region_id} -> {chosen}")
                # Seed the real region so the border leads somewhere populated
                try:
                    from text_rpg.content.region_seeder import seed_region

                    result = seed_region(chosen, context.game_id, repos)
                    self._pending_region_entry = result["entry_location_id"]
                except Exception as e:
                    logger.warning(f"Failed to seed crossed region {chosen}: {e}")
                    self._pending_region_entry = None
                return chosen

            # All content regions visited — this stays in current region
            # (The _try_reveal_region method handles LLM-generated regions as narrative hooks)
            return current_region_id
        except Exception:
            return current_region_id

    def _try_reveal_region(
        self, context: GameContext, repos: dict[str, Any]
    ) -> dict | None:
        """Check if conditions are right to reveal a new region, and generate one if so."""
        from text_rpg.content.loader import load_all_regions, load_region

        try:
            all_regions = load_all_regions()
        except Exception:
            return None

        all_region_ids = list(all_regions.keys())
        if not triggers.should_reveal_new_region(context, repos, all_region_ids):
            return None

        # Determine target tier based on player level
        player_level = context.character.get("level", 1)
        current_region_id = context.location.get("region_id", "")

        try:
            current_region = load_region(current_region_id)
        except Exception:
            return None

        current_max = current_region.get("level_range_max", 5)

        # Check for unvisited content regions first (prefer content over generated)
        location_repo = repos.get("location")
        if not location_repo:
            return None

        all_game_locations = location_repo.get_all(context.game_id)
        visited_regions = set()
        for loc in all_game_locations:
            r = loc.get("region_id", "")
            if r and loc.get("visited"):
                visited_regions.add(r)

        # Find an unvisited content region at a higher tier
        unvisited_content = [
            r for r in all_region_ids
            if r not in visited_regions and r != current_region_id
        ]

        if unvisited_content:
            # Choose the unvisited content region closest to the player's level
            def level_distance(rid: str) -> int:
                meta = all_regions.get(rid, {})
                return abs(meta.get("level_range_min", 1) - player_level)

            target_region_id = min(unvisited_content, key=level_distance)

            # Seed it into the DB and open a real road to its entry location
            try:
                from text_rpg.content.region_seeder import seed_region

                result = seed_region(target_region_id, context.game_id, repos)
                target_region = result["region"]
                entry_loc_id = result["entry_location_id"]
                conn_repo = repos.get("connection")
                current_loc_id = context.location.get("id", "")
                if conn_repo and entry_loc_id and current_loc_id:
                    if not conn_repo.find_connection(context.game_id, current_loc_id, "beyond"):
                        conn_repo.add_bidirectional(
                            game_id=context.game_id,
                            source_id=current_loc_id,
                            target_id=entry_loc_id,
                            direction="beyond",
                            reverse_direction="back",
                            description=f"Road to {target_region.get('name', 'new lands')}",
                            back_description=context.location.get("name", "the way back"),
                        )
            except Exception as e:
                logger.warning(f"Failed to seed revealed region {target_region_id}: {e}")
                return None

            self._last_generation["region"] = context.turn_number
            return {
                "event_type": "DIRECTOR_REGION_REVEAL",
                "description": (
                    f"Travelers speak of lands beyond — {target_region.get('name', 'a distant region')}. "
                    f"{target_region.get('description', '')[:200]} "
                    f"A road now leads beyond from here."
                ),
                "location_id": context.location.get("id"),
                "mechanical_details": {
                    "region_id": target_region_id,
                    "region_name": target_region.get("name", ""),
                    "level_range_min": target_region.get("level_range_min", 1),
                    "level_range_max": target_region.get("level_range_max", 5),
                },
            }

        # All content regions visited — generate a new region via LLM
        target_min = max(1, current_max - 1)
        target_max = target_min + 4  # 5-level range

        try:
            from text_rpg.systems.director.generators import generate_region

            existing_names = [r.get("name", r_id) for r_id, r in all_regions.items()]
            region_data = generate_region(
                self.llm, context, current_region,
                target_min, target_max, existing_names,
            )
        except Exception as e:
            logger.warning(f"Region generation failed: {e}")
            return None

        # Save region locations and NPCs to DB
        region_id = region_data["id"]
        region_data["game_id"] = context.game_id

        # Save each location
        locations = region_data.get("locations", [])
        for i, loc in enumerate(locations):
            loc["game_id"] = context.game_id
            loc["region_id"] = region_id
            # Connect locations within the region (linear chain)
            if i > 0:
                prev_loc = locations[i - 1]
                loc.setdefault("connections", [])
            location_repo.save(_serialize_location(loc))

        # Create inter-location connections within the region
        conn_repo = repos.get("connection")
        for i in range(len(locations) - 1):
            src = locations[i]
            dst = locations[i + 1]
            if conn_repo:
                conn_repo.add_bidirectional(
                    game_id=context.game_id,
                    source_id=src["id"],
                    target_id=dst["id"],
                    direction="forward",
                    reverse_direction="back",
                    description=dst.get("name", "ahead"),
                    back_description=src.get("name", "back"),
                )

        # Connect first location of new region to current location
        if locations:
            entry_loc = locations[0]
            if conn_repo:
                conn_repo.add_bidirectional(
                    game_id=context.game_id,
                    source_id=context.location.get("id", ""),
                    target_id=entry_loc["id"],
                    direction="beyond",
                    reverse_direction="back",
                    description=f"Path to {region_data.get('name', 'new lands')}",
                    back_description=context.location.get("name", "the way back"),
                )

        # Save NPCs
        entity_repo = repos.get("entity")
        for npc in region_data.get("npcs", []):
            npc["game_id"] = context.game_id
            npc["region_id"] = region_id
            # Place NPC at a location in the region
            if locations:
                # Place at town/settlement or first location
                town_locs = [l for l in locations if l.get("location_type") in ("town", "village", "settlement", "tavern", "shop")]
                target_loc = town_locs[0] if town_locs else locations[0]
                npc["location_id"] = target_loc["id"]
            if entity_repo:
                entity_repo.save(_serialize_entity(npc))

        # Index to RAG
        try:
            self.indexer.index_lore(
                f"New region discovered: {region_data['name']} — {region_data.get('description', '')}",
                category="location",
                tags={"game_id": context.game_id, "region_id": region_id},
            )
        except Exception:
            pass

        self._last_generation["region"] = context.turn_number
        return {
            "event_type": "DIRECTOR_REGION_REVEAL",
            "description": (
                f"Travelers speak of lands beyond — {region_data.get('name', 'a distant region')}. "
                f"{region_data.get('description', '')[:200]}"
            ),
            "location_id": context.location.get("id"),
            "mechanical_details": {
                "region_id": region_id,
                "region_name": region_data.get("name", ""),
                "level_range_min": region_data.get("level_range_min", target_min),
                "level_range_max": region_data.get("level_range_max", target_max),
                "generated": True,
                "location_count": len(locations),
                "npc_count": len(region_data.get("npcs", [])),
            },
        }

    def _check_faction_goals(
        self, context: GameContext, repos: dict[str, Any]
    ) -> list[dict]:
        """Check and resolve faction goal outcomes."""
        from text_rpg.content.loader import load_all_factions
        from text_rpg.mechanics.faction_goals import apply_goal_effects, check_faction_goals

        try:
            factions = load_all_factions()
            ws = repos.get("world_state")

            # Build per-goal last-resolved turns so goals fire on their own
            # intervals instead of all at once when the director cooldown lapses.
            last_checked: dict[str, int] = {}
            if ws:
                for faction_id, faction in factions.items():
                    for goal in faction.get("goals", []):
                        goal_key = f"{faction_id}_{goal.get('id', '')}"
                        last_checked[goal_key] = ws.get_event_cooldown(
                            context.game_id, f"faction_goal_{goal_key}"
                        )

            events = check_faction_goals(factions, context.turn_number, last_checked)

            for event in events:
                details = event.get("mechanical_details", {})
                effects = details.get("effects", [])
                if effects:
                    apply_goal_effects(effects, context.game_id, repos)

                if ws:
                    goal_key = f"{details.get('faction_id', '')}_{details.get('goal_id', '')}"
                    ws.set_event_cooldown(
                        context.game_id, f"faction_goal_{goal_key}", context.turn_number
                    )

                # Index to RAG
                try:
                    self.indexer.index_event(
                        context.game_id,
                        "FACTION_GOAL",
                        event.get("description", ""),
                        location_id=context.location.get("id"),
                        turn_number=context.turn_number,
                    )
                except Exception:
                    pass

            return events
        except Exception as e:
            logger.warning(f"Faction goals check failed: {e}")
            return []

    def _check_world_events(
        self, context: GameContext, repos: dict[str, Any]
    ) -> list[dict]:
        """Check and trigger random world events."""
        from text_rpg.content.loader import load_world_events
        from text_rpg.mechanics.faction_goals import check_world_events

        try:
            events_pool = load_world_events()
            if not events_pool:
                return []

            loc_type = context.location.get("location_type", "wilderness")

            # Get cooldowns from DB
            cooldowns: dict[str, int] = {}
            for event in events_pool:
                eid = event.get("id", "")
                if eid:
                    last = repos["world_state"].get_event_cooldown(context.game_id, eid)
                    if last:
                        cooldowns[eid] = last

            triggered = check_world_events(
                events_pool, context.turn_number, context.world_time, loc_type, cooldowns
            )

            result_events: list[dict] = []
            for event in triggered:
                eid = event.get("id", "")
                # Save cooldown
                repos["world_state"].set_event_cooldown(
                    context.game_id, eid, context.turn_number
                )

                result_events.append({
                    "event_type": "WORLD_EVENT",
                    "description": event.get("narrator_hint", event.get("description", "")),
                    "location_id": context.location.get("id"),
                    "mechanical_details": {
                        "event_id": eid,
                        "event_description": event.get("description", ""),
                    },
                })

            return result_events
        except Exception as e:
            logger.warning(f"World events check failed: {e}")
            return []

    def _check_story_progression(
        self, context: GameContext, repos: dict[str, Any]
    ) -> list[dict]:
        """Check and advance story seed progression.

        - Activates new stories if none are active (after turn 5)
        - Advances existing stories when beat triggers are met
        - Max 2 concurrent active stories
        """
        from text_rpg.mechanics.story_seeds import (
            check_beat_trigger,
            get_narrator_hints,
            load_all_seeds,
            next_beat,
            resolve_variables,
            select_seed,
        )

        events: list[dict] = []

        try:
            active_stories = repos["world_state"].get_active_stories(context.game_id)
            completed_ids = repos["world_state"].get_completed_story_ids(context.game_id)
            all_seeds = load_all_seeds()
            seed_map = {s["id"]: s for s in all_seeds}

            # -- Advance existing stories --
            for story in active_stories:
                seed_id = story.get("seed_id", "")
                seed = seed_map.get(seed_id)
                if not seed:
                    continue

                current_beat = story.get("current_beat", "hook")
                nxt = next_beat(current_beat)
                if not nxt:
                    # At resolution — finish the story once its quests resolve
                    events.extend(self._maybe_complete_story(story, seed, context, repos))
                    continue

                beat_def = seed.get(nxt, {})
                if not beat_def:
                    continue

                # Build game state for trigger check
                completed_quest_ids = []
                for q in repos["world_state"].get_all_quests(context.game_id):
                    if q.get("status") == "completed":
                        completed_quest_ids.append(q["id"])

                game_state = {
                    "turn_number": context.turn_number,
                    "character_level": context.character.get("level", 1),
                    "completed_quest_ids": completed_quest_ids,
                }

                story_state = {
                    "current_beat": current_beat,
                    "beat_turn_numbers": story.get("beat_turn_numbers") or {},
                    "quest_ids": story.get("quest_ids") or [],
                }

                if check_beat_trigger(beat_def, story_state, game_state):
                    # Advance to next beat
                    activated = safe_json(story.get("activated_beats"), [])
                    activated.append(nxt)

                    quest_ids = safe_json(story.get("quest_ids"), [])

                    beat_turns = safe_json(story.get("beat_turn_numbers"), {})
                    beat_turns[nxt] = context.turn_number

                    # Generate a quest for this beat if it has a template
                    quest_template = beat_def.get("quest_template")
                    if quest_template:
                        quest_event = self._generate_story_quest(
                            seed, nxt, story, quest_template, context, repos
                        )
                        if quest_event:
                            quest_id = quest_event.get("target_id", "")
                            if quest_id:
                                quest_ids.append(quest_id)
                            events.append(quest_event)

                    repos["world_state"].update_story_beat(
                        context.game_id, seed_id, nxt, activated, quest_ids,
                    )

                    # Update beat_turn_numbers in story data
                    story_data = repos["world_state"].get_story_state(context.game_id, seed_id)
                    if story_data:
                        data = safe_json(story_data.get("data"), {})
                        data["beat_turn_numbers"] = beat_turns
                        repos["world_state"].save_story_state({
                            **story_data,
                            "beat_turn_numbers": beat_turns,
                        })

                    seed_name = seed.get("name", seed_id)
                    events.append({
                        "event_type": "STORY_BEAT",
                        "description": f"Story '{seed_name}' advances to {nxt}.",
                        "actor_id": context.character.get("id"),
                        "location_id": context.location.get("id"),
                        "mechanical_details": {
                            "story_name": seed_name,
                            "beat_name": nxt,
                            "seed_id": seed_id,
                        },
                    })

            # -- Activate new stories --
            if len(active_stories) < 2 and context.turn_number >= 5:
                # Check cooldown: don't activate too often
                last_activation = self._last_generation.get("story", -999)
                if (context.turn_number - last_activation) >= 15:
                    active_tags = []
                    for s in active_stories:
                        sid = s.get("seed_id", "")
                        sd = seed_map.get(sid, {})
                        active_tags.extend(sd.get("tags", []))

                    game_state = {
                        "turn_number": context.turn_number,
                        "character_level": context.character.get("level", 1),
                    }

                    selected = select_seed(all_seeds, game_state, completed_ids, active_tags)
                    if selected:
                        variables = resolve_variables(selected, context)
                        story_id = f"{context.game_id}_{selected['id']}"

                        repos["world_state"].save_story_state({
                            "id": story_id,
                            "game_id": context.game_id,
                            "seed_id": selected["id"],
                            "status": "active",
                            "current_beat": "hook",
                            "resolved_variables": variables,
                            "activated_beats": ["hook"],
                            "beat_turn_numbers": {"hook": context.turn_number},
                            "quest_ids": [],
                            "data": {},
                        })

                        # Generate hook quest if template exists
                        hook = selected.get("hook", {})
                        quest_template = hook.get("quest_template")
                        if quest_template:
                            quest_event = self._generate_story_quest(
                                selected, "hook",
                                {"resolved_variables": variables, "seed_id": selected["id"]},
                                quest_template, context, repos,
                            )
                            if quest_event:
                                quest_id = quest_event.get("target_id", "")
                                if quest_id:
                                    repos["world_state"].update_story_beat(
                                        context.game_id, selected["id"],
                                        "hook", ["hook"], [quest_id],
                                    )
                                events.append(quest_event)
                            else:
                                # No hook quest generated — undo the active story save
                                repos["world_state"].complete_story(
                                    context.game_id, selected["id"], "failed",
                                )

                        self._last_generation["story"] = context.turn_number

                        seed_name = selected.get("name", selected["id"])
                        events.append({
                            "event_type": "STORY_BEAT",
                            "description": f"A new story begins: '{seed_name}'.",
                            "actor_id": context.character.get("id"),
                            "location_id": context.location.get("id"),
                            "mechanical_details": {
                                "story_name": seed_name,
                                "beat_name": "hook",
                                "seed_id": selected["id"],
                            },
                        })

        except Exception as e:
            logger.warning(f"Story progression check failed: {e}")

        return events

    def _maybe_complete_story(
        self, story: dict, seed: dict, context: GameContext, repos: dict[str, Any],
    ) -> list[dict]:
        """Complete a story at its resolution beat once its quests resolve."""
        quest_ids = safe_json(story.get("quest_ids"), [])
        if not quest_ids:
            return []

        ws = repos["world_state"]
        statuses = []
        for quest_id in quest_ids:
            quest = ws.get_quest(quest_id, context.game_id)
            if quest:
                statuses.append(quest.get("status", ""))
        if not statuses or any(s in ("active", "available") for s in statuses):
            return []  # Still in progress

        succeeded = all(s == "completed" for s in statuses)
        seed_id = story.get("seed_id", "")
        seed_name = seed.get("name", seed_id)
        ws.complete_story(context.game_id, seed_id, "completed" if succeeded else "failed")

        resolution = seed.get("resolution", {})
        effects = resolution.get("success_effects" if succeeded else "failure_effects", [])
        self._apply_story_effects(effects, context, repos)

        hints = resolution.get(
            "success_narrator_hints" if succeeded else "failure_narrator_hints", [])
        description = (
            hints[0] if hints
            else f"The story '{seed_name}' reaches its {'triumphant' if succeeded else 'bitter'} end."
        )
        return [{
            "event_type": "STORY_BEAT",
            "description": description,
            "actor_id": context.character.get("id"),
            "location_id": context.location.get("id"),
            "mechanical_details": {
                "story_name": seed_name,
                "beat_name": "complete" if succeeded else "failed",
                "seed_id": seed_id,
            },
        }]

    def _apply_story_effects(
        self, effects: list, context: GameContext, repos: dict[str, Any],
    ) -> None:
        """Apply story resolution effects. Only faction reputation is wired;
        other effect types are logged and skipped (see plan follow-up)."""
        rep_repo = repos.get("reputation")
        for effect in effects or []:
            if not isinstance(effect, dict):
                continue
            if effect.get("type") == "faction_rep" and rep_repo:
                delta = effect.get("delta", 0)
                faction_id = effect.get("faction_id", "")
                if not faction_id:
                    # Seeds use faction_role; map onto a current-region faction.
                    role = effect.get("faction_role", "")
                    faction_id = self._resolve_faction_for_role(role, context, repos)
                if faction_id and delta:
                    try:
                        rep_repo.adjust_faction_rep(context.game_id, faction_id, delta)
                    except Exception as e:
                        logger.warning(f"Story faction effect failed: {e}")
            else:
                logger.info(f"Skipping unwired story effect type: {effect.get('type')}")

    @staticmethod
    def _resolve_faction_for_role(
        role: str, context: GameContext, repos: dict[str, Any],
    ) -> str:
        """Best-effort mapping from a story role to a faction in the current region."""
        if not role:
            return ""
        from text_rpg.content.loader import load_all_factions

        region = context.location.get("region_id", "")
        try:
            factions = load_all_factions()
        except Exception:
            return ""
        trade_roles = {"merchant", "farmer", "innkeeper", "trader"}
        for fid, data in factions.items():
            if region and data.get("region") != region:
                continue
            if role in fid:
                return fid
            if role in trade_roles and any(k in fid for k in ("merchant", "trade", "cooperative")):
                return fid
        return ""

    def _generate_story_quest(
        self,
        seed: dict,
        beat_name: str,
        story: dict,
        quest_template: dict,
        context: GameContext,
        repos: dict[str, Any],
    ) -> dict | None:
        """Generate a quest from a story beat's quest_template."""
        from text_rpg.mechanics.story_seeds import fill_template
        from text_rpg.systems.director.generators import generate_quest

        variables = safe_json(story.get("resolved_variables"), {})

        # Build a synthetic NPC for quest generation
        quest_type = quest_template.get("type", "investigate")
        target = quest_template.get("target", "")
        report_to = quest_template.get("report_to", "")

        # Resolve target and report_to from variables
        target_name = variables.get(target, target.replace("_", " "))
        report_to_name = variables.get(report_to, report_to.replace("_", " "))

        seed_name = seed.get("name", "Unknown")
        beat_desc = f"Story: {seed_name}, Beat: {beat_name}"

        # Find an NPC to be the quest giver
        quest_giver_id = variables.get(f"{report_to}_id", "")
        if not quest_giver_id:
            # Find first available NPC
            for e in context.entities:
                if e.get("entity_type") == "npc" and e.get("is_alive", True):
                    quest_giver_id = e.get("id", "")
                    break

        try:
            # Use Director's quest generator with story context
            candidates = self._quest_candidates(context, repos)
            if quest_giver_id:
                candidates["npcs"].add(quest_giver_id)
            quest_data = generate_quest(self.llm, context, {
                "name": report_to_name,
                "description": f"Quest giver for {seed_name}",
                "id": quest_giver_id,
                "dialogue_tags": ["concerned", "urgent"],
                "properties": json.dumps({
                    "motivation": beat_desc,
                    "quest_hook": f"A {quest_type} task related to {target_name}. {seed.get('description_template', '')}",
                }),
            }, candidates)
        except Exception as e:
            logger.warning(f"Story quest generation failed: {e}")
            return None

        quest_data["game_id"] = context.game_id
        quest_data["quest_giver_id"] = quest_giver_id

        repos["world_state"].save_quest(_serialize_quest(quest_data))

        # Index to RAG
        try:
            self.indexer.index_lore(
                f"Story quest: {quest_data['name']} — {quest_data.get('description', '')}",
                category="quest",
                tags={"game_id": context.game_id, "quest_id": quest_data["id"]},
            )
        except Exception:
            pass

        self._last_generation["quest"] = context.turn_number
        return {
            "event_type": "DIRECTOR_QUEST_AVAILABLE",
            "description": f"A new quest emerges from the story: {quest_data.get('name', '')}",
            "target_id": quest_data["id"],
            "location_id": context.location.get("id"),
            "mechanical_details": {
                "quest_name": quest_data.get("name", ""),
                "quest_description": quest_data.get("description", ""),
                "quest_giver": report_to_name,
            },
        }

    def _populate_new_location(
        self, location_data: dict, context: GameContext, repos: dict[str, Any]
    ) -> None:
        """Optionally add 0-1 NPCs to a newly generated location."""
        loc_type = location_data.get("location_type", "wilderness")
        # Towns/settlements are more likely to have NPCs
        if loc_type in ("town", "village", "settlement", "tavern", "shop"):
            from text_rpg.systems.director.generators import generate_npc
            try:
                npc_data = generate_npc(
                    self.llm, context, location_data, {},
                )
                npc_data = _scale_npc_to_player(npc_data, context)
                npc_data["game_id"] = context.game_id
                npc_data["location_id"] = location_data["id"]
                repos["entity"].save(_serialize_entity(npc_data))
            except Exception as e:
                logger.debug(f"Failed to populate new location with NPC: {e}")


# -- NPC scaling --

def _scale_npc_to_player(npc_data: dict, context: GameContext) -> dict:
    """Scale a Director-spawned NPC's level to player_level ± 2, clamped to region range.

    Also adjusts HP and AC proportionally based on level difference.

    Rare event (2%): a vastly overpowered mob spawns (e.g., dragon attack)
    ignoring the normal scaling — creates dangerous surprise encounters.
    """
    import random

    player_level = context.character.get("level", 1)
    region_id = context.location.get("region_id", "")

    # Rare event: 2% chance to spawn a vastly overpowered mob
    if random.random() < 0.02:
        rare_level = max(player_level + 5, 10)
        npc_data["level"] = rare_level
        npc_data["hp_max"] = max(npc_data.get("hp_max", 10), rare_level * 8)
        npc_data["hp_current"] = npc_data["hp_max"]
        npc_data["ac"] = min(20, max(npc_data.get("ac", 10), 14 + rare_level // 4))
        npc_data["is_hostile"] = True
        npc_data.setdefault("properties", {})
        if isinstance(npc_data["properties"], str):
            import json
            npc_data["properties"] = json.loads(npc_data["properties"]) if npc_data["properties"] else {}
        npc_data["properties"]["rare_spawn"] = True
        return npc_data

    # Get region level bounds
    region_min, region_max = 1, 20
    if region_id:
        try:
            from text_rpg.content.loader import load_region
            region_data = load_region(region_id)
            region_min = region_data.get("level_range_min", 1)
            region_max = region_data.get("level_range_max", 20)
        except Exception:
            pass

    # Target level: player ± 2, clamped to region range
    target_min = max(region_min, player_level - 2)
    target_max = min(region_max, player_level + 2)
    # Handle edge case where player is above/below region range
    if target_min > target_max:
        target_level = target_max if player_level > region_max else target_min
    else:
        target_level = random.randint(target_min, target_max)

    old_level = npc_data.get("level", 1)
    npc_data["level"] = target_level

    # Scale HP proportionally if level changed significantly
    if old_level > 0 and target_level != old_level:
        ratio = target_level / old_level
        old_hp = npc_data.get("hp_max", 10)
        new_hp = max(4, round(old_hp * ratio))
        npc_data["hp_max"] = new_hp
        npc_data["hp_current"] = new_hp

        # Slight AC adjustment for higher levels
        base_ac = npc_data.get("ac", 10)
        ac_bonus = max(0, (target_level - old_level) // 3)
        npc_data["ac"] = min(20, base_ac + ac_bonus)

    return npc_data


# -- Serialization helpers --

def _serialize_entity(data: dict) -> dict:
    """Prepare entity dict for DB storage."""
    out = dict(data)
    for field in ("ability_scores", "attacks", "behaviors", "dialogue_tags", "loot_table", "properties"):
        if field in out and out[field] is not None and not isinstance(out[field], str):
            out[field] = json.dumps(out[field])
    return out


def _serialize_location(data: dict) -> dict:
    """Prepare location dict for DB storage."""
    out = dict(data)
    for field in ("connections", "entities", "items", "properties"):
        if field in out and out[field] is not None and not isinstance(out[field], str):
            out[field] = json.dumps(out[field])
    return out


def _serialize_quest(data: dict) -> dict:
    """Prepare quest dict for DB storage."""
    out = dict(data)
    for field in ("objectives", "item_rewards"):
        if field in out and out[field] is not None and not isinstance(out[field], str):
            out[field] = json.dumps(out[field])
    return out


def _reverse_direction(direction: str) -> str:
    """Return the opposite compass direction."""
    opposites = {
        "north": "south", "south": "north",
        "east": "west", "west": "east",
        "northeast": "southwest", "southwest": "northeast",
        "northwest": "southeast", "southeast": "northwest",
        "up": "down", "down": "up",
    }
    return opposites.get(direction.lower(), "back")
