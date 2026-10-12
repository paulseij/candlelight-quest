"""Action dispatcher — routes actions to appropriate systems."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import replace
from typing import Any

from text_rpg.engine.system_registry import SystemRegistry
from text_rpg.mechanics.ability_scores import modifier
from text_rpg.mechanics.skills import SKILL_ABILITY_MAP, skill_check
from text_rpg.models.action import Action, ActionResult, DiceRoll
from text_rpg.systems.base import GameContext
from text_rpg.utils import safe_json

logger = logging.getLogger(__name__)

# Verbs that imply using/manipulating a specific item.
_ITEM_USE_PATTERN = re.compile(
    r"(?:throw|toss|hurl|use|drink|eat|consume|apply|wave|swing|wield|brandish|"
    r"pour|drop|place|put|give|show|offer|light|smash|break)\s+"
    r"(?:the\s+|my\s+|a\s+|some\s+)?(.+?)"
    r"(?:\s+(?:at|on|to|toward|towards|into|onto|against|over|with)\s+|$)",
    re.I,
)


class ActionDispatcher:
    def __init__(self, registry: SystemRegistry, director: Any | None = None, repos: dict[str, Any] | None = None):
        self.registry = registry
        self.director = director
        self.repos = repos or {}

    def dispatch(self, action: Action, context: GameContext) -> ActionResult:
        if action.action_type == 'search' and action.target_id and self.repos.get('save_game'):
            from text_rpg.engine.scene_actions import SceneActions
            scene = SceneActions(self.repos, getattr(self.director, 'llm', None))
            return scene.propose(action, context)
        # The LLM classifier could not be reached — surface it, record nothing
        if action.action_type == "llm_unavailable":
            return self._llm_unavailable_result(action)

        # Unrecognized input — don't try plausibility, just give a helpful nudge
        if action.action_type == "unrecognized":
            return ActionResult(
                action_id=action.id,
                success=False,
                outcome_description=(
                    f"You pause, uncertain. What exactly do you want to do? "
                    f"(Try commands like **look**, **go north**, **talk to** someone, "
                    f"**attack**, **search**, or type **help** for a full list.)"
                ),
            )

        from text_rpg.engine.nearby_people import asks_about_people, generic_conversation_target, nearby_people
        if action.action_type == "talk" and generic_conversation_target(action.target_id):
            return ActionResult(action_id=action.id, outcome_description=nearby_people(context))
        if action.action_type == "question":
            if asks_about_people(action.raw_input):
                return ActionResult(action_id=action.id, outcome_description=nearby_people(context))
            return ActionResult(action_id=action.id, outcome_description=f"{context.location.get('name','Here')}: {context.location.get('description','')}\n{nearby_people(context)}")
        # Explicit names remain instant; roles and unfamiliar phrasing can use
        # the same constrained interpreter as freeform environmental actions.
        unresolved_talk = False
        if action.action_type == "talk" and not action.parameters.get("_interpreted"):
            from text_rpg.engine.nearby_people import match_people
            matches = match_people(context, action.target_id or '')
            if len(matches) > 1:
                return ActionResult(action_id=action.id, outcome_description=nearby_people(context, action.target_id))
            if len(matches) == 1:
                action = replace(action, target_id=matches[0]['id'])
            else:
                unresolved_talk = True
        if not action.parameters.get("_interpreted") and (unresolved_talk or action.action_type in {"custom", "interact", "use_item", "puzzle", "look"}):
            from text_rpg.storage.database import Database
            from text_rpg.engine.scene_actions import SceneActions
            db = getattr(self.repos.get("save_game"), "db", None)
            if isinstance(db,Database):
                scene = SceneActions(self.repos, self.director.llm if self.director else None)
                named_object = any(o['name'].lower() in action.raw_input.lower() for o in scene.objects(context))
                may_use_model = self.director is not None
                if named_object or may_use_model and (unresolved_talk or action.action_type == "custom" or (action.action_type == "look" and action.target_id)):
                    from text_rpg.llm.errors import LLMError
                    from pydantic import ValidationError
                    try:
                        proposal=scene.propose(action,context)
                    except (LLMError, ValidationError):
                        return ActionResult(action_id=action.id, success=False, outcome_description=
                            'I could not interpret that request. Try a direct command such as look or talk to a name.\n' + nearby_people(context))
                    if isinstance(proposal,Action):
                        from text_rpg.engine.validators import validate_action
                        valid,reason=validate_action(proposal,context)
                        return self.dispatch(proposal,context) if valid else ActionResult(action_id=action.id,outcome_description=reason)
                    return proposal
        system = self.registry.find_system_for_action(action, context)
        if not system:
            # No system handles this action — try plausibility-gated creative action
            if self.director:
                return self._try_creative_action(action, context)
            logger.warning(f"No system found for action type: {action.action_type}")
            return ActionResult(
                action_id=action.id,
                success=False,
                outcome_description=f"You're not sure how to '{action.raw_input}'.",
            )
        try:
            return system.resolve(action, context)
        except Exception as e:
            logger.exception(f"Error resolving action in {system.system_id}")
            if getattr(getattr(self.repos.get("save_game"), "db", None), "_staging", False):
                raise
            return ActionResult(
                action_id=action.id,
                success=False,
                outcome_description=f"Something went wrong: {e}",
            )

    def _check_item_references(self, raw_input: str, context: GameContext) -> str | None:
        """Check if the action references items the player doesn't have.

        Returns an error message if a referenced item is missing, or None if OK.
        """
        if re.search(r"\bthrow (?:a |the )?punch\b",raw_input,re.I):
            return None
        match = _ITEM_USE_PATTERN.search(raw_input.lower())
        if not match:
            return None

        item_ref = match.group(1).strip()
        if not item_ref:
            return None

        inv = context.inventory
        inv_items: list[str] = []
        if inv:
            items_raw = safe_json(inv.get("items"), [])
            inv_items = [e.get("item_id", "").lower().replace("_", " ") for e in items_raw]

        # Check if the referenced item matches anything in inventory
        found = any(
            item_ref in item_name or item_name in item_ref
            for item_name in inv_items
        )

        if not found:
            if inv_items:
                return f"You don't have '{item_ref}' in your inventory."
            return f"Your pack is empty — you don't have '{item_ref}'."

        return None

    def _try_creative_action(self, action: Action, context: GameContext) -> ActionResult:
        """Use the Director's plausibility engine to evaluate and resolve a creative action."""
        from text_rpg.llm.errors import LLMMalformedOutputError, LLMUnavailableError

        try:
            # Check inventory for referenced items before running plausibility
            item_error = self._check_item_references(action.raw_input, context)
            if item_error:
                return ActionResult(
                    action_id=action.id,
                    success=False,
                    outcome_description=item_error,
                )

            from text_rpg.systems.director.generators import plausibility_to_dc

            plausibility = self.director.evaluate_plausibility(action, context)
            dc = plausibility_to_dc(plausibility["plausibility"])

            # Determine ability score and skill
            ability_name = plausibility.get("ability", "strength").lower()
            skill_name = plausibility.get("skill", "athletics").lower()

            char = context.character
            scores = safe_json(char.get("ability_scores"), {})
            ability_score = scores.get(ability_name, 10)

            skill_profs = safe_json(char.get("skill_proficiencies"), [])
            is_prof = skill_name in skill_profs

            prof_bonus = char.get("proficiency_bonus", 2)

            success, roll_result = skill_check(ability_score, prof_bonus, is_prof, dc)

            dice_rolls = [DiceRoll(
                dice_expression="1d20",
                rolls=roll_result.individual_rolls,
                modifier=roll_result.modifier,
                total=roll_result.total,
                purpose=f"{skill_name}_check (DC {dc})",
            )]

            result = self.director.generate_creative_outcome(
                action, context, plausibility, self.repos,
                roll_total=roll_result.total, dc=dc, roll_succeeded=success,
            )
            result.dice_rolls = dice_rolls
            return result
        except LLMUnavailableError as e:
            logger.error(f"LLM unavailable during creative action: {e}")
            return self._llm_unavailable_result(action)
        except LLMMalformedOutputError as e:
            # Plausibility itself stayed malformed after retries — safe refusal,
            # no roll, no events, nothing recorded.
            logger.warning(f"Creative action produced malformed LLM output: {e}")
            return ActionResult(
                action_id=action.id,
                success=False,
                outcome_description="You attempt it, but nothing comes of it.",
            )
        except Exception as e:
            logger.warning(f"Creative action evaluation failed: {e}")
            return ActionResult(
                action_id=action.id,
                success=False,
                outcome_description=f"You're not sure how to '{action.raw_input}'.",
            )

    @staticmethod
    def _llm_unavailable_result(action: Action) -> ActionResult:
        """Provider is down: tell the player plainly and record nothing."""
        return ActionResult(
            action_id=action.id,
            success=False,
            outcome_description=(
                "The storyteller cannot be reached (LLM provider unavailable). "
                "Check your connection or API key, or run 'text-rpg setup'. "
                "Nothing happened — try again in a moment."
            ),
        )
