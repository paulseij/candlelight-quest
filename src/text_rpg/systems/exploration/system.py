"""Exploration system — movement, looking, searching."""
from __future__ import annotations

import json
import logging
from typing import Any

from text_rpg.mechanics.ability_scores import modifier
from text_rpg.mechanics.size import stealth_modifier
from text_rpg.mechanics.skills import SKILL_ABILITY_MAP, skill_check
from text_rpg.engine.exits import exit_options
from text_rpg.models.action import Action, ActionResult, DiceRoll, StateMutation
from text_rpg.systems.base import GameContext, GameSystem
from text_rpg.utils import safe_json

logger = logging.getLogger(__name__)


class ExplorationSystem(GameSystem):
    @staticmethod
    def _blocked_route_text(direction: str, context: GameContext) -> str:
        options = exit_options(safe_json(context.location.get('connections'), []))
        usable = [label for label, connection in options if not connection.get('is_locked')]
        if usable:
            return f"No route {direction} is available from here. Known exits: " + ', '.join(f'go {label}' for label in usable) + '. Use look or leads to choose a route.'
        return f"No route {direction} is available from here. Use look for your surroundings."

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
        return "exploration"

    @property
    def handled_action_types(self) -> set[str]:
        return {"move", "look", "search", "interact", "hunt", "danger", "options"}

    def can_handle(self, action: Action, context: GameContext) -> bool:
        return action.action_type.lower() in self.handled_action_types

    def resolve(self, action: Action, context: GameContext) -> ActionResult:
        action_type = action.action_type.lower()
        if action_type == 'options':
            return self._resolve_options(action, context)
        if action_type in {"hunt", "danger"}:
            from text_rpg.engine.scene_detail import danger_encounters
            if action_type == "danger":
                lines = []
                locations = self._repos['location'].get_all(context.game_id) if self._repos.get('location') else [context.location]
                for loc in locations:
                    encounters = danger_encounters(loc)
                    if not encounters:
                        continue
                    route = self._route_to(context.game_id, context.location['id'], loc['id'])
                    if route is None:
                        continue
                    from text_rpg.engine.scene_detail import danger_rating
                    rating = danger_rating(loc, context.character.get('level', 1))
                    enemy_names = rating['names'] if rating else ['hostile creature']
                    level_text = (f"levels {rating['minimum_level']}-{rating['maximum_level']}; "
                                  f"{rating['threat'].upper()} for you") if rating else 'unknown level'
                    travel = 'you are here' if not route else ', then '.join(f'go {step}' for step in route)
                    lines.append(f"- {loc.get('name', loc['id'])}: {travel}; possible {', '.join(enemy_names)} ({level_text})")
                if not lines:
                    return ActionResult(action_id=action.id, outcome_description='No reachable combat zones are currently known. Explore further or follow leads into another region.')
                return ActionResult(action_id=action.id, outcome_description='Reachable combat zones:\n' + '\n'.join(lines) + '\nUse `hunt` after arriving. Encounters can still be dangerous at the listed levels.')
            if context.combat_state and context.combat_state.get('is_active'):
                return ActionResult(action_id=action.id, outcome_description='You are already fighting.')
            if not danger_encounters(context.location):
                return ActionResult(action_id=action.id, outcome_description='There are no established hostile encounters here. Use danger to find combat zones.')
            return ActionResult(action_id=action.id, success=True, outcome_description='You spend ten minutes deliberately searching for signs of hostile creatures.',
                                events=[{'event_type': 'HUNT', 'location_id': context.location['id'], 'description': 'Sought hostile creatures for ten minutes.'}])
        if action_type == "move":
            return self._resolve_move(action, context)
        elif action_type == "look":
            return self._resolve_look(action, context)
        elif action_type == "search":
            return self._resolve_search(action, context)
        elif action_type == "interact":
            return self._resolve_interact(action, context)
        return ActionResult(action_id=action.id, success=False, outcome_description="Unknown exploration action.")

    def _resolve_options(self, action: Action, context: GameContext) -> ActionResult:
        """List commands that act on the current saved scene."""
        lines: list[str] = []
        props = safe_json(context.location.get('properties'), {})
        puzzle = props.get('active_puzzle')
        if puzzle:
            command = {'lock': 'pick lock', 'trap': 'disarm trap', 'riddle': 'answer <your answer>',
                       'elemental': 'solve <your magical approach>'}.get(puzzle.get('puzzle_type'),
                                                                         'solve <your approach>')
            lines.append(f"`{command}` — address {puzzle.get('name', 'the unresolved obstacle')}")
        hostiles = [entity for entity in context.entities
                    if entity.get('is_alive', True) and entity.get('is_hostile')]
        if hostiles:
            lines.append(f"`attack {hostiles[0]['name']}` — confront a visible hostile creature")
        people = [entity for entity in context.entities
                  if entity.get('is_alive', True) and entity.get('entity_type') == 'npc']
        if people:
            lines.append(f"`talk to {people[0]['name']}` — ask a person here about local events or work")
        items = safe_json(context.location.get('items'), [])
        if items:
            lines.append('`pick up items` — collect the visible ground items')
        if self._repos.get('save_game'):
            from text_rpg.engine.scene_actions import SceneActions
            objects = SceneActions(self._repos, None).objects(context)
            unsearched = [obj for obj in objects if 'search' in obj['state'].get('operations', [])
                          and not obj['state'].get('searched')
                          and int(obj['state'].get('search_attempts', {}).get(context.character.get('id'), 0))
                          < int(context.character.get('level', 1))]
            if unsearched:
                lines.append(f"`search behind {unsearched[0]['name']}` — investigate a visible feature")
            from text_rpg.engine.campaign import CampaignRuntime
            campaign = CampaignRuntime(self._repos).get(context.game_id)
            if campaign and campaign.get('status') == 'active':
                lines.append('`leads` — view the regional investigation and routes to its evidence')
        exits = exit_options(safe_json(context.location.get('connections'), []))
        if exits:
            lines.append(f"`go {exits[0][0]}` — follow a known exit")
        from text_rpg.engine.scene_detail import danger_encounters
        if danger_encounters(context.location):
            lines.append('`hunt` — deliberately seek a hostile encounter in this combat zone')
        else:
            lines.append('`danger` — find reachable combat zones and their threat levels')
        lines.append('You can also describe another concrete action in plain language.')
        return ActionResult(action_id=action.id, success=True,
                            outcome_description='Things you can do here now:\n' +
                            '\n'.join(f'{index}. {line}' for index, line in enumerate(lines, 1)))

    def _route_to(self, game_id: str, start: str, target: str) -> list[str] | None:
        """Return stable player-facing exit selectors for the shortest known route."""
        if start == target:
            return []
        connection_repo = self._repos.get('connection')
        if not connection_repo:
            return None
        queue = [(start, [])]
        seen = {start}
        while queue:
            location_id, path = queue.pop(0)
            for label, connection in exit_options(connection_repo.get_connections(game_id, location_id)):
                if connection.get('is_locked'):
                    continue
                destination = connection.get('target_location_id')
                if not destination or destination in seen:
                    continue
                next_path = path + [label]
                if destination == target:
                    return next_path
                seen.add(destination)
                queue.append((destination, next_path))
        return None

    def get_available_actions(self, context: GameContext) -> list[dict]:
        actions: list[dict] = [{"action_type": "look", "description": "Look around"}]
        connections = safe_json(context.location.get("connections"), [])
        for label, conn in exit_options(connections):
            if isinstance(conn, dict):
                actions.append({
                    "action_type": "move",
                    "target": label,
                    "description": f"Go {conn.get('direction', '?')} to {conn.get('description', conn.get('target_location_id', '?'))}",
                })
        actions.append({"action_type": "search", "description": "Search the area"})
        return actions

    def _resolve_move(self, action: Action, context: GameContext) -> ActionResult:
        direction = (action.target_id or "").lower()
        connections = safe_json(context.location.get("connections"), [])

        options = exit_options(connections)
        matches = [(label, conn) for label, conn in options
                   if direction in {label, conn.get("direction", "").lower(), conn.get("target_location_id", "").lower(),
                                    conn.get("target_location_id", "").replace("_", " ").lower()}]
        if len(matches) > 1:
            choices = "\n".join(f"- go {label}: {conn.get('description') or conn.get('target_location_id', '?')}" for label, conn in matches)
            return ActionResult(action_id=action.id, outcome_description=f"There are several paths that way. Choose one:\n{choices}")
        target_conn = matches[0][1] if matches else None

        if not target_conn:
            outdoor = context.location.get('location_type') in {
                'wilderness', 'road', 'mountain', 'forest', 'beach', 'coast',
                'ruins', 'desert', 'swamp', 'hills', 'grassland', 'valley',
            }
            compass = direction in {
                'north', 'south', 'east', 'west', 'northeast', 'northwest',
                'southeast', 'southwest',
            }
            if outdoor and compass and self._director and self._repos:
                return self._try_dynamic_move(action, direction, context)
            return ActionResult(
                action_id=action.id, success=False,
                outcome_description=self._blocked_route_text(direction, context),
            )

        if target_conn.get("is_locked"):
            return ActionResult(
                action_id=action.id, success=False,
                outcome_description="That way is locked.",
            )

        target_loc_id = target_conn["target_location_id"]

        # Authored cross-region edge: the target may belong to a region that
        # hasn't been seeded into this game yet — seed it on first entry.
        crossed_into = self._maybe_seed_target_region(target_loc_id, context)

        outcome = f"You head {direction}."
        target_location = self._repos.get('location').get(target_loc_id, context.game_id) if self._repos.get('location') else None
        if target_location:
            from text_rpg.engine.scene_detail import danger_rating
            rating = danger_rating(target_location, context.character.get('level', 1))
            if rating:
                names = ', '.join(rating['names'])
                outcome += (f"\nDanger: this area can contain {names} "
                            f"(levels {rating['minimum_level']}-{rating['maximum_level']}; "
                            f"{rating['threat'].upper()} for you).")
        events = [{"event_type": "MOVE", "description": f"Traveled {direction} to a new location.", "location_id": target_loc_id}]
        if crossed_into:
            outcome += f"\nYou cross into {crossed_into.get('name', 'a new land')}."
            events.append({
                "event_type": "DISCOVERY",
                "description": f"Entered a new region: {crossed_into.get('name', '')}. {crossed_into.get('description', '')[:150]}",
                "location_id": target_loc_id,
            })

        mutations = [
            StateMutation(target_type="game", target_id=context.game_id, field="current_location_id", old_value=context.location["id"], new_value=target_loc_id),
            StateMutation(target_type="location", target_id=target_loc_id, field="visited", old_value=False, new_value=True),
        ]

        # Move companions to new location
        for comp in (context.companions or []):
            if comp.get("status") == "active":
                mutations.append(StateMutation(
                    target_type="entity", target_id=comp["entity_id"],
                    field="location_id", old_value=None, new_value=target_loc_id,
                ))

        # Check for bounty-related encounters when entering a town
        bounty_event = self._check_bounty_on_enter(target_loc_id, context)
        if bounty_event:
            outcome += f"\n{bounty_event['description']}"
            events.append(bounty_event)

        return ActionResult(
            action_id=action.id, success=True,
            outcome_description=outcome,
            state_mutations=mutations,
            events=events,
        )

    def _maybe_seed_target_region(self, target_loc_id: str, context: GameContext) -> dict | None:
        """Seed the target location's authored region if it isn't in the DB yet.

        Returns the region data when a new region was seeded, else None.
        """
        if not self._repos:
            return None
        try:
            if self._repos["location"].get(target_loc_id, context.game_id):
                return None
            from text_rpg.content.loader import find_region_for_location
            from text_rpg.content.region_seeder import seed_region

            region_id = find_region_for_location(target_loc_id)
            if not region_id:
                return None
            result = seed_region(region_id, context.game_id, self._repos)
            return result["region"] if result.get("seeded") else None
        except Exception as e:
            logger.warning(f"Lazy region seeding failed for {target_loc_id}: {e}")
            return None

    def _try_dynamic_move(self, action: Action, direction: str, context: GameContext) -> ActionResult:
        """Explore an open direction with one generation request and a saved fallback."""

        try:
            new_location = self._director.generate_location_for_direction(
                direction, context, self._repos,
            )
            if not new_location:
                return ActionResult(
                    action_id=action.id, success=False,
                    outcome_description="The route could not be created. Nothing changed. " + self._blocked_route_text(direction, context),
                )
            target_loc_id = new_location["id"]
            return ActionResult(
                action_id=action.id, success=True,
                outcome_description=f"You discover a path leading {direction} to {new_location.get('name', 'a new area')}.",
                state_mutations=[
                    StateMutation(target_type="game", target_id=context.game_id, field="current_location_id", old_value=context.location["id"], new_value=target_loc_id),
                    StateMutation(target_type="location", target_id=target_loc_id, field="visited", old_value=False, new_value=True),
                ],
                events=[{
                    "event_type": "DISCOVERY",
                    "description": f"Discovered a new path {direction} leading to {new_location.get('name', 'a new area')}.",
                    "location_id": target_loc_id,
                }],
            )
        except Exception as e:
            from text_rpg.llm.errors import LLMUnavailableError
            if isinstance(e, LLMUnavailableError):
                logger.error(f"LLM unavailable during dynamic move: {e}")
                return ActionResult(
                    action_id=action.id, success=False,
                    outcome_description=(
                        "The storyteller cannot be reached (LLM provider unavailable). "
                        "Nothing happened. " + self._blocked_route_text(direction, context)
                    ),
                )
            logger.warning(f"Dynamic location generation failed: {e}")
            return ActionResult(
                action_id=action.id, success=False,
                outcome_description="The new route could not be generated. Nothing happened. " + self._blocked_route_text(direction, context),
            )

    def _check_bounty_on_enter(self, target_loc_id: str, context: GameContext) -> dict | None:
        """Check if entering a town triggers a guard confrontation due to bounty."""
        if not self._repos or not self._repos.get("reputation") or not self._repos.get("location"):
            return None

        try:
            target_loc = self._repos["location"].get(target_loc_id, context.game_id)
            if not target_loc:
                return None

            loc_type = target_loc.get("location_type", "wilderness")
            if loc_type not in ("town", "village", "settlement"):
                return None

            region_id = target_loc.get("region_id", "")
            if not region_id:
                return None

            bounty = self._repos["reputation"].get_bounty(context.game_id, region_id)
            if bounty.get("amount", 0) <= 0:
                return None

            amount = bounty["amount"]
            return {
                "event_type": "GUARD_CONFRONTATION",
                "description": (
                    f"As you enter, a guard steps forward. \"Halt! You're wanted for crimes in this region. "
                    f"Your bounty stands at {amount} gold. Pay up or face justice!\""
                ),
                "mechanical_details": {
                    "bounty_amount": amount,
                    "region": region_id,
                },
            }
        except Exception:
            return None

    def _resolve_look(self, action: Action, context: GameContext) -> ActionResult:
        loc = context.location
        entities = context.entities
        entity_names = [e["name"] for e in entities if e.get("is_alive", True)]
        items = safe_json(loc.get("items"), [])

        description = loc.get("description") or "You see nothing notable."
        parts = [description]
        if self._repos.get('save_game'):
            from text_rpg.engine.scene_actions import SceneActions
            from text_rpg.engine.scene_detail import scene_summary
            from text_rpg.mechanics import world_clock
            summary = scene_summary(loc, SceneActions(self._repos, None).objects(context), entities,
                                    world_clock.get_period(context.world_time), context.game_id, context.world_time)
            if summary:
                parts.append(summary)
        notes = safe_json(loc.get("properties"), {}).get("discovery_notes", [])
        if notes:
            parts.append("Known discoveries:\n" + "\n".join(notes))
        if entity_names:
            parts.append(f"Present: {', '.join(entity_names)}")
        if items:
            from text_rpg.systems.inventory.pickup import ground_entries
            from text_rpg.systems.item_catalog import get_item_catalog
            catalog = get_item_catalog(self._repos, context.game_id, context.character.get("id"))
            entries = ground_entries(items, catalog)
            parts.append("Items on the ground:\n" + "\n".join(f"- {e['name']} (x{e['quantity']})" for e in entries))

        connections = safe_json(loc.get("connections"), [])
        if connections:
            exits = []
            for label, c in exit_options(connections):
                exits.append(f"{label} ({c.get('description', c.get('target_location_id', '?'))})")
            parts.append("Exits (use go followed by the direction and number):\n" + "\n".join(f"- {e}" for e in exits))

        return ActionResult(action_id=action.id, success=True, outcome_description="\n\n".join(parts))

    def _resolve_search(self, action: Action, context: GameContext) -> ActionResult:
        char = context.character
        if context.combat_state and context.combat_state.get("is_active"):
            return ActionResult(action_id=action.id, outcome_description="You cannot search the area while fighting.")
        props = dict(safe_json(context.location.get("properties"), {}))
        discoveries = props.get("discoveries", [])
        # Add authored search content to existing saves without replacing their
        # mutable location state. Only explicitly authored discoveries qualify.
        if not discoveries and context.location.get("region_id"):
            from text_rpg.content.loader import load_region
            try:
                region = load_region(context.location["region_id"])
                authored = next((loc for loc in region.get("locations", []) if loc["id"] == context.location["id"]), {})
                discoveries = authored.get("properties", {}).get("discoveries", [])
            except (FileNotFoundError, KeyError, ValueError):
                discoveries = []
        found = list(props.get("discovered_ids", []))
        remaining = [d for d in discoveries if d["id"] not in found]
        if self._repos.get('save_game') and not action.target_id and not discoveries:
            from text_rpg.engine.scene_actions import SceneActions
            objects = SceneActions(self._repos, None).objects(context)
            searchable = [o for o in objects if 'search' in o['state'].get('operations', []) and not o['state'].get('searched')]
            if searchable:
                return ActionResult(action_id=action.id, outcome_description='Where do you want to search? ' + ', '.join(o['name'] for o in searchable) + '. Try search behind <feature>.')
        attempts = dict(props.get("search_attempts", {}))
        key = char["id"]
        level = char.get("level", 1)
        if attempts.get(key, 0) >= level:
            return ActionResult(action_id=action.id, outcome_description=
                "You have already searched this area at your current level. Repeating the same search will not reveal more. "
                "You can try again after gaining a level, or explore somewhere else.")
        attempts[key] = level
        props["search_attempts"] = attempts
        mutation = StateMutation(target_type="location", target_id=context.location["id"], field="properties", new_value=props)
        if not remaining:
            return ActionResult(action_id=action.id, success=True, state_mutations=[mutation],
                outcome_description="You spend ten minutes checking the area. There is nothing new to discover here.",
                events=[{"event_type": "SEARCH", "description": "Searched the area; no undiscovered findings remain.", "location_id": context.location["id"]}])
        scores = safe_json(char.get("ability_scores"), {})
        profs = safe_json(char.get("skill_proficiencies"), [])
        skill = "investigation" if "investigation" in profs or "investigat" in action.raw_input.lower() else "perception"
        ability = "intelligence" if skill == "investigation" else "wisdom"
        dc = int(remaining[0].get("dc", 12))
        success, roll = skill_check(scores.get(ability, 10), char.get("proficiency_bonus", 2), skill in profs, dc=dc)
        dice = DiceRoll(dice_expression="1d20", rolls=roll.individual_rolls, modifier=roll.modifier,
                        total=roll.total, purpose=f"{skill} (DC {dc})")
        events = [{"event_type": "SKILL_CHECK", "description": f"{skill} check (DC {dc}) — {'success' if success else 'failure'}",
                   "actor_id": key, "location_id": context.location["id"],
                   "mechanical_details": {"skill": skill, "dc": dc, "success": success, "roll": roll.total}}]
        if success:
            revealed = [d for d in remaining if roll.total >= int(d.get("dc", 12))]
            found.extend(d["id"] for d in revealed)
            props["discovered_ids"] = found
            notes = list(props.get("discovery_notes", []))
            notes.extend(d["description"] for d in revealed)
            props["discovery_notes"] = notes
            outcome = "Your ten-minute search reveals:\n" + "\n".join(d["description"] for d in revealed)
            for discovery in revealed:
                events.append({"event_type": "DISCOVERY", "description": discovery["description"],
                               "location_id": context.location["id"], "mechanical_details": {"discovery_id": discovery["id"]}})
        else:
            outcome = "You spend ten minutes searching, but fail to uncover anything hidden. You cannot reroll this search at your current level."
        return ActionResult(action_id=action.id, success=success, outcome_description=outcome,
                            dice_rolls=[dice], state_mutations=[mutation], events=events)

    def _resolve_interact(self, action: Action, context: GameContext) -> ActionResult:
        target = (action.target_id or "").strip()
        # Check if target is an item on the ground (match against real item ids)
        items = safe_json(context.location.get("items"), [])
        normalized = target.lower().replace(" ", "_")
        item_id = next(
            (i for i in items if i.lower() in (target.lower(), normalized)), None,
        )
        if item_id is not None:
            remaining = [i for i in items if i != item_id]
            return ActionResult(
                action_id=action.id, success=True,
                outcome_description=f"You pick up {item_id.replace('_', ' ')}.",
                state_mutations=[
                    StateMutation(target_type="location", target_id=context.location["id"], field="items", old_value=items, new_value=remaining),
                    StateMutation(target_type="inventory", target_id=context.character["id"], field="items_add", old_value=None, new_value={"item_id": item_id, "quantity": 1}),
                ],
                events=[{
                    "event_type": "ITEM_PICKUP",
                    "description": f"Picked up {item_id.replace('_', ' ')}.",
                    "actor_id": context.character["id"],
                    "mechanical_details": {"item_id": item_id},
                }],
            )
        return ActionResult(action_id=action.id, success=False, outcome_description=f"Nothing to interact with called '{target}'.")
