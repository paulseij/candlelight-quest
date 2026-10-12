"""Combat game system — turn-based combat with initiative, NPC AI, and combat menu."""
from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

from text_rpg.content.loader import load_all_items
from text_rpg.mechanics.ability_scores import modifier
from text_rpg.mechanics.combat_math import (
    assess_threat_level,
    attack_roll,
    calculate_flee_dc,
    damage_roll,
    determine_turn_order,
    grapple_check,
    initiative_roll,
    npc_choose_action,
)
from text_rpg.mechanics.conditions import (
    can_take_actions,
    grants_advantage_to_attackers,
    has_attack_advantage,
    has_attack_disadvantage,
)
from text_rpg.mechanics.elements import get_effective_damage
from text_rpg.mechanics.skills import skill_check
from text_rpg.models.action import Action, ActionResult, DiceRoll, StateMutation
from text_rpg.systems.base import GameContext, GameSystem
from text_rpg.utils import safe_json

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent.parent.parent / "llm" / "prompts"
_jinja_env: Environment | None = None


def _get_jinja() -> Environment:
    global _jinja_env
    if _jinja_env is None:
        _jinja_env = Environment(loader=FileSystemLoader(str(_PROMPTS_DIR)), autoescape=False)
    return _jinja_env


class CombatSystem(GameSystem):
    def __init__(self, repos: dict[str, Any] | None = None):
        self._repos = repos or {}
        self._llm = None

    def inject(self, *, repos: dict | None = None, llm: Any = None, **kwargs: Any) -> None:
        if repos is not None:
            self._repos = repos
        if llm is not None:
            self._llm = llm

    @property
    def system_id(self) -> str:
        return "combat"

    @property
    def handled_action_types(self) -> set[str]:
        return {"attack", "dodge", "dash", "disengage", "help", "hide", "flee", "combat_item", "combat_spell", "class_ability", "puzzle", "grapple"}

    def can_handle(self, action: Action, context: GameContext) -> bool:
        at = action.action_type.lower()
        if at in self.handled_action_types:
            return True
        # During active combat, also intercept cast_spell and use_item
        if context.combat_state and context.combat_state.get("is_active"):
            if at in ("cast_spell", "use_item"):
                return True
        return False

    def resolve(self, action: Action, context: GameContext) -> ActionResult:
        # Check for puzzle encounter
        if action.action_type.lower() == "puzzle":
            encounter = action.parameters.get("encounter") or self._find_encounter_for_combat(context)
            if encounter:
                return self._resolve_puzzle(action, context, encounter)
            return ActionResult(action_id=action.id, success=False,
                                outcome_description="There is no unresolved puzzle at this location.")

        combat = context.combat_state
        if not combat or not combat.get("is_active"):
            # Check if the encounter is a puzzle type before initiating combat
            encounter = self._find_encounter_for_combat(context)
            if encounter and encounter.get("fight_type") == "puzzle":
                return self._resolve_puzzle(action, context, encounter)
            # First attack → initiate combat
            return self._initiate_combat(action, context)
        # Already in combat → resolve player turn, then NPC turns
        return self._resolve_pending_opening(action, context)

    def get_available_actions(self, context: GameContext) -> list[dict]:
        if not context.combat_state or not context.combat_state.get("is_active"):
            hostiles = [e for e in context.entities if e.get("is_hostile") and e.get("is_alive", True)]
            if hostiles:
                return [{"action_type": "attack", "targets": [e["name"] for e in hostiles]}]
            return []
        return [
            {"action_type": "attack", "description": "[1] Attack an enemy"},
            {"action_type": "combat_spell", "description": "[2] Cast a spell"},
            {"action_type": "combat_item", "description": "[3] Use an item"},
            {"action_type": "flee", "description": "[4] Flee"},
            {"action_type": "dodge", "description": "[5] Dodge"},
        ]

    # -- Combat Initiation --

    def begin_encounter(self, context: GameContext, enemy_id: str) -> ActionResult:
        """Start hostile contact without inventing a player attack."""
        return self._initiate_combat(Action(action_type="attack", actor_id=context.character["id"], target_id=enemy_id),
                                     context, opening_attack=False)

    def _initiate_combat(self, action: Action, context: GameContext, opening_attack: bool = True) -> ActionResult:
        """Roll initiative for all combatants, create combat state, resolve first exchange."""
        target = self._find_target(action.target_id, context)
        if not target:
            # If attacking a non-hostile NPC, still allow it
            target = self._find_any_target(action.target_id, context)
            if not target:
                return ActionResult(action_id=action.id, success=False, outcome_description="No valid target found.")

        # Collect all hostile + the targeted entity for combat
        enemy_ids = set()
        enemy_ids.add(target["id"])
        for e in context.entities:
            if e.get("is_hostile") and e.get("is_alive", True):
                enemy_ids.add(e["id"])

        # Assess threat level before combat
        player_level = context.character.get("level", 1)
        threat_warnings: list[str] = []
        for eid in enemy_ids:
            for e in context.entities:
                if e["id"] == eid:
                    enemy_level = e.get("level", e.get("challenge_rating", 1)) or 1
                    threat = assess_threat_level(player_level, enemy_level)
                    if threat in ("deadly", "overwhelming"):
                        threat_warnings.append(f"{e['name']} ({threat})")
                    break

        # Build combat state via start_combat
        combat = self.start_combat(context, list(enemy_ids))

        # Save combat state to DB
        if self._repos and self._repos.get("world_state"):
            try:
                self._repos["world_state"].save_combat(combat)
            except Exception as e:
                logger.error(f"Failed to save combat state: {e}")

        # Build initiative report
        dice_rolls: list[DiceRoll] = []
        init_report = []
        for c in combat["combatants"]:
            init_report.append(f"{c['name']}: {c['initiative']}")
            dice_rolls.append(DiceRoll(
                dice_expression="1d20",
                rolls=[c["initiative"] - c.get("initiative_bonus", 0)],
                modifier=c.get("initiative_bonus", 0),
                total=c["initiative"],
                purpose=f"initiative ({c['name']})",
            ))

        events: list[dict[str, Any]] = [{
            "event_type": "COMBAT_START",
            "description": f"Combat begins! Initiative: {', '.join(init_report)}",
            "actor_id": context.character["id"],
        }]

        # Show threat warning if applicable
        if threat_warnings:
            warning = "DANGER: " + ", ".join(threat_warnings) + "!"
            events.append({
                "event_type": "THREAT_WARNING",
                "description": warning,
                "actor_id": context.character["id"],
            })

        # Determine who goes first
        first_id = combat["turn_order"][0] if combat["turn_order"] else None
        player_id = context.character["id"]

        # If player goes first, resolve their attack action
        if not opening_attack:
            mutations = []
            xp = 0
            self._get_combatant(combat, player_id)["opening_pending"] = True
            order = "You have initiative." if first_id == player_id else "The enemy has initiative and will act before your chosen action."
            outcome = f"Combat begins! {order} Choose an action: attack, dodge, cast a spell, or flee. No attacks have resolved yet."
        elif first_id == player_id:
            player_result = self._resolve_player_attack(action, target, context, combat)
            mutations = player_result["mutations"]
            dice_rolls.extend(player_result["dice_rolls"])
            events.extend(player_result["events"])
            outcome = f"Combat begins! You act first.\n{player_result['description']}"
            xp = player_result["xp"]

            # Then resolve NPC turns
            npc_results = self._resolve_all_npc_turns(combat, context)
            mutations.extend(npc_results["mutations"])
            dice_rolls.extend(npc_results["dice_rolls"])
            events.extend(npc_results["events"])
            if npc_results["description"]:
                outcome += f"\n{npc_results['description']}"
        else:
            # NPC goes first — resolve NPC turns until player's turn
            outcome = "Combat begins! The enemy acts first.\n"
            mutations: list[StateMutation] = []
            xp = 0

            npc_results = self._resolve_all_npc_turns(combat, context, stop_at_player=True)
            mutations.extend(npc_results["mutations"])
            dice_rolls.extend(npc_results["dice_rolls"])
            events.extend(npc_results["events"])
            if npc_results["description"]:
                outcome += npc_results["description"]
            if self._get_combatant_hp(combat, player_id) > 0:
                outcome += "\nYour turn — choose your action."

        # The next input belongs to the player after the opening exchange.
        if player_id in combat["turn_order"]:
            combat["current_turn_index"] = combat["turn_order"].index(player_id)

        # Check combat end
        end_result = self._check_combat_end(combat, context)
        if end_result:
            outcome += f"\n{end_result['description']}"
            xp += end_result.get("xp", 0)
            events.extend(end_result.get("events", []))
            mutations.extend(end_result.get("mutations", []))
            # End combat in DB
            self._end_combat(combat)
        else:
            # Update combat state
            self._save_combat_state(combat)

        return ActionResult(
            action_id=action.id, success=True,
            outcome_description=outcome,
            dice_rolls=dice_rolls,
            state_mutations=mutations,
            events=events,
            xp_gained=xp,
        )

    # -- Turn Resolution --

    def _resolve_pending_opening(self, action: Action, context: GameContext) -> ActionResult:
        """Collect the player's opening choice before any hostile damage."""
        combat = context.combat_state
        player = self._get_combatant(combat, context.character["id"])
        if not player or not player.get("opening_pending"):
            return self._resolve_combat_turn(action, context)
        if action.action_type in {"attack", "1"} and not self._find_combat_target(action.target_id, context, combat):
            return ActionResult(action_id=action.id, outcome_description="Choose a visible enemy to attack.")
        if action.action_type in {"2", "combat_spell", "cast_spell", "3", "combat_item", "use_item"} and not (action.target_id or action.parameters.get("spell_name") or action.parameters.get("item_name")):
            prompt = self._prompt_spell_list(context) if action.action_type in {"2", "combat_spell", "cast_spell"} else self._prompt_item_list(context)
            return ActionResult(action_id=action.id, outcome_description=prompt["description"])
        player["opening_pending"] = False
        order = combat["turn_order"]
        before_player = set(order[:order.index(context.character["id"])])
        opening = self._resolve_all_npc_turns(combat, context, stop_at_player=True)
        end = self._check_combat_end(combat, context)
        if end:
            self._end_combat(combat)
            return ActionResult(action_id=action.id, success=True,
                outcome_description="\n".join(filter(None, [opening["description"], end["description"]])),
                dice_rolls=opening["dice_rolls"], state_mutations=opening["mutations"] + end.get("mutations", []),
                events=opening["events"] + end.get("events", []), xp_gained=end.get("xp", 0))
        # Actions such as healing must see damage from the opening exchange.
        context.character = dict(context.character)
        for mutation in opening["mutations"]:
            if mutation.target_type == "character" and mutation.target_id == context.character["id"]:
                context.character[mutation.field] = mutation.new_value
        self._save_combat_state(combat)
        result = self._resolve_combat_turn(action, context, skip_npcs=before_player)
        result.dice_rolls = opening["dice_rolls"] + result.dice_rolls
        result.state_mutations = opening["mutations"] + result.state_mutations
        result.events = opening["events"] + result.events
        result.outcome_description = "\n".join(filter(None, [opening["description"], result.outcome_description]))
        return result

    def _resolve_combat_turn(self, action: Action, context: GameContext, skip_npcs: set[str] | None = None) -> ActionResult:
        """Resolve the player's chosen action, then all NPC turns."""
        combat = context.combat_state
        action_type = action.action_type.lower()
        dice_rolls: list[DiceRoll] = []
        mutations: list[StateMutation] = []
        events: list[dict[str, Any]] = []
        xp = 0
        outcome_parts: list[str] = []

        # Map numbered choices to actions
        if action_type in ("1", "attack"):
            target = self._find_combat_target(action.target_id, context, combat)
            if not target:
                return ActionResult(action_id=action.id, success=False, outcome_description="No valid target in combat.")
            result = self._resolve_player_attack(action, target, context, combat)
            mutations.extend(result["mutations"])
            dice_rolls.extend(result["dice_rolls"])
            events.extend(result["events"])
            outcome_parts.append(result["description"])
            xp += result["xp"]

        elif action_type in ("5", "dodge"):
            outcome_parts.append("You take the Dodge action. Attacks against you have disadvantage until your next turn.")
            # Set dodging condition on player combatant
            self._set_player_condition(combat, "dodging")

        elif action_type in ("4", "flee"):
            flee_result = self._resolve_flee(action, context, combat)
            dice_rolls.extend(flee_result["dice_rolls"])
            events.extend(flee_result["events"])
            outcome_parts.append(flee_result["description"])
            if flee_result["escaped"]:
                self._end_combat(combat)
                return ActionResult(
                    action_id=action.id, success=True,
                    outcome_description="\n".join(outcome_parts),
                    dice_rolls=dice_rolls, events=events,
                )

        elif action_type in ("3", "combat_item", "use_item"):
            spell_result = self._resolve_combat_item(action, context, combat)
            if not spell_result["success"]:
                return ActionResult(action_id=action.id, success=False, outcome_description=spell_result["description"])
            mutations.extend(spell_result.get("mutations", []))
            dice_rolls.extend(spell_result.get("dice_rolls", []))
            events.extend(spell_result.get("events", []))
            outcome_parts.append(spell_result["description"])

        elif action_type in ("2", "combat_spell", "cast_spell"):
            spell_result = self._resolve_combat_spell(action, context, combat)
            if not spell_result["success"]:
                return ActionResult(action_id=action.id, success=False, outcome_description=spell_result["description"])
            mutations.extend(spell_result.get("mutations", []))
            dice_rolls.extend(spell_result.get("dice_rolls", []))
            events.extend(spell_result.get("events", []))
            outcome_parts.append(spell_result["description"])
            xp += spell_result.get("xp", 0)

        elif action_type in ("6", "class_ability"):
            ability_result = self._resolve_class_ability(action, context, combat)
            if not ability_result["success"]:
                return ActionResult(action_id=action.id, success=False, outcome_description=ability_result["description"])
            mutations.extend(ability_result.get("mutations", []))
            dice_rolls.extend(ability_result.get("dice_rolls", []))
            events.extend(ability_result.get("events", []))
            outcome_parts.append(ability_result["description"])

        elif action_type in ("grapple",):
            grapple_result = self._resolve_grapple(action, context, combat)
            if not grapple_result["success"]:
                return ActionResult(action_id=action.id, success=False, outcome_description=grapple_result["description"])
            mutations.extend(grapple_result.get("mutations", []))
            dice_rolls.extend(grapple_result.get("dice_rolls", []))
            events.extend(grapple_result.get("events", []))
            outcome_parts.append(grapple_result["description"])

        elif action_type in ("dash",):
            outcome_parts.append("You take the Dash action, doubling your movement.")

        elif action_type in ("disengage",):
            outcome_parts.append("You disengage, moving without provoking opportunity attacks.")

        else:
            outcome_parts.append(f"You take the {action_type} action.")

        # Advance round
        combat["round_number"] = combat.get("round_number", 1) + 1

        # Check combat end after player turn
        end_result = self._check_combat_end(combat, context)
        if end_result:
            outcome_parts.append(end_result["description"])
            xp += end_result.get("xp", 0)
            events.extend(end_result.get("events", []))
            mutations.extend(end_result.get("mutations", []))
            self._end_combat(combat)
            return ActionResult(
                action_id=action.id, success=True,
                outcome_description="\n".join(outcome_parts),
                dice_rolls=dice_rolls, state_mutations=mutations,
                events=events, xp_gained=xp,
            )

        # Resolve NPC turns
        npc_results = self._resolve_all_npc_turns(combat, context, skip_ids=skip_npcs)
        mutations.extend(npc_results["mutations"])
        dice_rolls.extend(npc_results["dice_rolls"])
        events.extend(npc_results["events"])
        if npc_results["description"]:
            outcome_parts.append(npc_results["description"])

        # Check combat end after NPC turns
        end_result = self._check_combat_end(combat, context)
        if end_result:
            outcome_parts.append(end_result["description"])
            xp += end_result.get("xp", 0)
            events.extend(end_result.get("events", []))
            mutations.extend(end_result.get("mutations", []))
            self._end_combat(combat)
        else:
            self._save_combat_state(combat)

        return ActionResult(
            action_id=action.id, success=True,
            outcome_description="\n".join(outcome_parts),
            dice_rolls=dice_rolls, state_mutations=mutations,
            events=events, xp_gained=xp,
        )

    # -- Player Action Resolution --

    def _resolve_player_attack(
        self, action: Action, target: dict, context: GameContext, combat: dict,
    ) -> dict:
        """Resolve a player attack. Returns dict with mutations, dice_rolls, events, description, xp."""
        char = context.character
        atk_bonus = self._get_attack_bonus(char)
        target_ac = target.get("ac", 10)

        attacker_conditions = safe_json(char.get("conditions"), [])
        target_conditions = safe_json(target.get("conditions"), [])

        advantage = has_attack_advantage(attacker_conditions) or grants_advantage_to_attackers(target_conditions)
        disadvantage = has_attack_disadvantage(attacker_conditions)

        # Check if target has dodging condition in combat state
        target_combatant = self._get_combatant(combat, target.get("id", ""))
        if target_combatant and "dodging" in target_combatant.get("conditions", []):
            disadvantage = True

        hit, is_critical, atk_result = attack_roll(atk_bonus, target_ac, advantage, disadvantage)

        dice_rolls = [DiceRoll(
            dice_expression="1d20", rolls=atk_result.individual_rolls,
            modifier=atk_bonus, total=atk_result.total,
            purpose="attack_roll", advantage=advantage, disadvantage=disadvantage,
        )]
        mutations: list[StateMutation] = []
        events: list[dict[str, Any]] = []
        xp = 0

        if hit:
            dmg_dice, dmg_mod = self._get_damage_dice(char)
            dmg_result = damage_roll(dmg_dice, dmg_mod, is_critical)

            # Apply resistance/vulnerability/immunity
            weapon = self._get_weapon_data(char)
            raw_damage_type = weapon.get("damage_type", "physical") if weapon else "physical"
            target_props = safe_json(target.get("properties"), {}) or {}
            effective_dmg, dmg_label = get_effective_damage(
                dmg_result.total,
                raw_damage_type,
                target_props.get("resistances", []),
                target_props.get("vulnerabilities", []),
                target_props.get("immunities", []),
            )

            dice_rolls.append(DiceRoll(
                dice_expression=dmg_dice, rolls=dmg_result.individual_rolls,
                modifier=dmg_mod, total=effective_dmg, purpose="damage_roll",
            ))

            # Update HP in combat state
            old_hp = self._get_combatant_hp(combat, target["id"])
            new_hp = max(0, old_hp - effective_dmg)
            self._set_combatant_hp(combat, target["id"], new_hp)

            mutations.append(StateMutation(
                target_type="entity", target_id=target["id"],
                field="hp_current", old_value=old_hp, new_value=new_hp,
            ))

            defeated = new_hp <= 0
            crit_text = " CRITICAL HIT!" if is_critical else ""
            resist_text = f" ({dmg_label})" if dmg_label != "normal" else ""
            desc = f"Hit!{crit_text} {effective_dmg} damage to {target['name']}.{resist_text}"

            # Narrative prose from LLM
            attack_name = weapon.get("name", "weapon strike") if weapon else "weapon strike"
            narration = self._narrate_attack(
                attacker_name=char["name"], attacker_type="player",
                defender_name=target["name"], hit=True, critical=is_critical,
                damage=effective_dmg, damage_type=raw_damage_type,
                defeated=defeated, attack_name=attack_name,
            )
            if narration:
                desc = narration

            events.append({
                "event_type": "ATTACK",
                "description": f"{char['name']} attacks {target['name']} and hits for {effective_dmg} damage.",
                "actor_id": char["id"], "target_id": target["id"],
                "mechanical_details": {
                    "damage": effective_dmg, "critical": is_critical,
                    "attack_style": self._get_attack_style(char),
                    "damage_type": raw_damage_type,
                    "damage_modifier": dmg_label,
                },
            })

            if defeated:
                self._mark_combatant_defeated(combat, target["id"])
                mutations.append(StateMutation(
                    target_type="entity", target_id=target["id"],
                    field="is_alive", old_value=True, new_value=False,
                ))
                events.append({
                    "event_type": "DEATH",
                    "description": f"{target['name']} has been defeated!",
                    "target_id": target["id"],
                })
                cr = target.get("challenge_rating", target.get("level", 1))
                xp = int((cr or 1) * 100)
                if not narration:
                    desc += f" {target['name']} is defeated!"
        else:
            desc = f"Miss! Attack roll {atk_result.total} vs AC {target_ac}."
            narration = self._narrate_attack(
                attacker_name=char["name"], attacker_type="player",
                defender_name=target["name"], hit=False, critical=False,
                damage=0, damage_type="", defeated=False,
            )
            if narration:
                desc = narration
            events.append({
                "event_type": "ATTACK",
                "description": f"{char['name']} attacks {target['name']} and misses.",
                "actor_id": char["id"], "target_id": target["id"],
                "mechanical_details": {
                    "hit": False,
                    "attack_style": self._get_attack_style(char),
                },
            })

        return {"mutations": mutations, "dice_rolls": dice_rolls, "events": events, "description": desc, "xp": xp}

    def _resolve_flee(self, action: Action, context: GameContext, combat: dict) -> dict:
        """Attempt to flee combat. DEX check vs DC based on enemy count."""
        char = context.character
        scores = safe_json(char.get("ability_scores"), {})
        dex_score = scores.get("dexterity", 10)
        prof_bonus = char.get("proficiency_bonus", 2)

        skill_profs = safe_json(char.get("skill_proficiencies"), [])
        is_prof = "athletics" in skill_profs or "acrobatics" in skill_profs

        active_enemies = [c for c in combat.get("combatants", [])
                         if c.get("combatant_type") == "enemy" and c.get("is_active", True)]
        dc = calculate_flee_dc(len(active_enemies))
        success, roll_result = skill_check(dex_score, prof_bonus, is_prof, dc)

        dice_rolls = [DiceRoll(
            dice_expression="1d20", rolls=roll_result.individual_rolls,
            modifier=roll_result.modifier, total=roll_result.total,
            purpose=f"flee_check (DC {dc})",
        )]
        flee_skill = "acrobatics" if "acrobatics" in skill_profs else "athletics"
        events: list[dict[str, Any]] = [{
            "event_type": "SKILL_CHECK",
            "description": f"{flee_skill} check (DC {dc}) — {'success' if success else 'failure'}",
            "actor_id": char.get("id", ""),
            "mechanical_details": {"skill": flee_skill, "dc": dc, "success": success, "roll": roll_result.total},
        }]

        if success:
            desc = f"You successfully flee from combat! (Roll: {roll_result.total} vs DC {dc})"
            events.append({"event_type": "COMBAT_FLEE", "description": "Successfully fled from combat."})
            events.append({
                "event_type": "COMBAT_END", "description": "Combat ended when the player escaped.",
                "location_id": context.location.get("id"),
                "mechanical_details": {"result": "fled"},
            })
        else:
            desc = f"You fail to escape! (Roll: {roll_result.total} vs DC {dc}) The enemies block your retreat."
            events.append({"event_type": "COMBAT_FLEE_FAIL", "description": "Failed to flee from combat."})

        return {"dice_rolls": dice_rolls, "events": events, "description": desc, "escaped": success}

    def _resolve_grapple(self, action: Action, context: GameContext, combat: dict) -> dict:
        """Resolve a grapple attempt. Athletics contest with size advantage/disadvantage."""
        char = context.character
        target = self._find_combat_target(action.target_id, context, combat)
        if not target:
            return {"success": False, "description": "No valid target to grapple."}

        scores = safe_json(char.get("ability_scores"), {})
        str_score = scores.get("strength", 10)
        prof_bonus = char.get("proficiency_bonus", 2)
        skill_profs = safe_json(char.get("skill_proficiencies"), [])
        is_prof = "athletics" in skill_profs

        target_scores = safe_json(target.get("ability_scores"), {})
        # Defender uses higher of Athletics (STR) or Acrobatics (DEX)
        target_str = target_scores.get("strength", 10)
        target_dex = target_scores.get("dexterity", 10)
        defender_score = max(target_str, target_dex)
        target_prof = target.get("proficiency_bonus", 2)
        target_profs = safe_json(target.get("skill_proficiencies"), [])
        defender_proficient = "athletics" in target_profs or "acrobatics" in target_profs

        attacker_size = char.get("size", "Medium")
        defender_size = target.get("size", "Medium")

        result = grapple_check(
            attacker_athletics=str_score,
            attacker_prof=prof_bonus,
            attacker_proficient=is_prof,
            defender_score=defender_score,
            defender_prof=target_prof,
            defender_proficient=defender_proficient,
            attacker_size=attacker_size,
            defender_size=defender_size,
        )

        dice_rolls: list[DiceRoll] = []
        mutations: list[StateMutation] = []
        events: list[dict[str, Any]] = []

        if result.get("auto_fail"):
            return {
                "success": True,
                "description": f"You can't grapple {target['name']} — {result.get('reason', 'too large!')}",
                "mutations": [], "dice_rolls": [], "events": [],
            }

        atk_roll_result = result["attacker_roll"]
        def_roll_result = result["defender_roll"]

        dice_rolls.append(DiceRoll(
            dice_expression="1d20", rolls=atk_roll_result.individual_rolls,
            modifier=atk_roll_result.modifier, total=atk_roll_result.total,
            purpose="grapple_athletics", advantage=result["advantage"], disadvantage=result["disadvantage"],
        ))
        dice_rolls.append(DiceRoll(
            dice_expression="1d20", rolls=def_roll_result.individual_rolls,
            modifier=def_roll_result.modifier, total=def_roll_result.total,
            purpose=f"grapple_contest ({target['name']})",
        ))

        if result["success"]:
            # Apply grappled condition to target in combat state
            target_combatant = self._get_combatant(combat, target["id"])
            if target_combatant:
                target_combatant.setdefault("conditions", []).append("grappled")
            size_note = ""
            if result["advantage"]:
                size_note = " (size advantage!)"
            desc = (
                f"You grapple {target['name']}!{size_note} "
                f"(Athletics {atk_roll_result.total} vs {def_roll_result.total}) "
                f"{target['name']} is grappled — their speed is 0."
            )
            events.append({
                "event_type": "GRAPPLE",
                "description": f"Grappled {target['name']}.",
                "actor_id": char["id"], "target_id": target["id"],
                "mechanical_details": {
                    "attacker_roll": atk_roll_result.total,
                    "defender_roll": def_roll_result.total,
                    "attacker_size": attacker_size,
                    "defender_size": defender_size,
                },
            })
        else:
            size_note = ""
            if result["disadvantage"]:
                size_note = " (size disadvantage)"
            desc = (
                f"You fail to grapple {target['name']}!{size_note} "
                f"(Athletics {atk_roll_result.total} vs {def_roll_result.total})"
            )
            events.append({
                "event_type": "GRAPPLE_FAIL",
                "description": f"Failed to grapple {target['name']}.",
                "actor_id": char["id"], "target_id": target["id"],
            })

        return {
            "success": True,
            "description": desc,
            "mutations": mutations,
            "dice_rolls": dice_rolls,
            "events": events,
        }

    # -- Combat Spell/Item Resolution --

    def _resolve_combat_spell(self, action: Action, context: GameContext, combat: dict) -> dict:
        """Resolve casting a spell during combat. Delegates to SpellcastingSystem then syncs combat HP."""
        # Determine spell name from the action
        spell_name = action.target_id or action.parameters.get("spell_name") or ""

        if not spell_name:
            # Bare "2" with no spell — list available spells as a prompt
            return self._prompt_spell_list(context)

        from text_rpg.systems.spellcasting.system import SpellcastingSystem

        spell_system = SpellcastingSystem()
        spell_system.inject(repos=(self._repos or {}))

        # Build a cast_spell action for the SpellcastingSystem
        spell_action = Action(
            id=action.id,
            actor_id=action.actor_id,
            action_type="cast_spell",
            target_id=spell_name,
            parameters=action.parameters,
            raw_input=action.raw_input,
        )
        result = spell_system.resolve(spell_action, context)

        # Sync HP mutations to combat state
        for mut in result.state_mutations:
            if mut.field == "hp_current":
                self._set_combatant_hp(combat, mut.target_id, mut.new_value)
                # Check if enemy was killed
                if mut.target_type == "entity" and mut.new_value <= 0:
                    self._mark_combatant_defeated(combat, mut.target_id)

        for event in result.events:
            if event.get('event_type') == 'REVIVAL':
                target_id = event['target_id']
                hp = event['mechanical_details']['restored_hp']
                self._set_combatant_hp(combat, target_id, hp)
                combatant = self._get_combatant(combat, target_id)
                if combatant:
                    combatant['is_active'] = True
                # Subsequent actors in this round must see the revived person.
                for entity in context.entities:
                    if entity['id'] == target_id:
                        entity['is_alive'], entity['hp_current'] = True, hp

        return {
            "success": result.success,
            "description": result.outcome_description,
            "mutations": list(result.state_mutations),
            "dice_rolls": list(result.dice_rolls),
            "events": list(result.events),
            "xp": result.xp_gained,
        }

    def _prompt_spell_list(self, context: GameContext) -> dict:
        """Return a prompt showing available spells when player types bare '2'."""
        char = context.character
        if not char.get("spellcasting_ability"):
            return {"success": False, "description": "You don't know any spells."}

        repos = self._repos or {}
        spell_repo = repos.get("spell")
        if not spell_repo:
            return {"success": False, "description": "Type 'cast [spell name]' to cast a spell."}

        prepared = spell_repo.get_prepared_spells(context.game_id, char["id"])
        known = spell_repo.get_known_spells(context.game_id, char["id"])

        from text_rpg.content.loader import load_all_spells
        all_spells = load_all_spells()

        # Show cantrips (always available) and prepared spells
        cantrip_names = []
        leveled_names = []
        for sid in known:
            spell = all_spells.get(sid)
            if not spell:
                continue
            if spell["level"] == 0:
                cantrip_names.append(spell["name"])
            elif sid in prepared:
                leveled_names.append(f"{spell['name']} (L{spell['level']})")

        parts = ["Type 'cast [spell name]' to cast during combat."]
        if cantrip_names:
            parts.append(f"Cantrips: {', '.join(cantrip_names)}")
        if leveled_names:
            slots = safe_json(char.get("spell_slots_remaining"), {})
            slot_str = ", ".join(f"L{k}:{v}" for k, v in sorted(slots.items()) if int(v) > 0)
            parts.append(f"Prepared: {', '.join(leveled_names)}")
            if slot_str:
                parts.append(f"Slots: {slot_str}")

        return {"success": False, "description": "\n".join(parts)}

    def _resolve_combat_item(self, action: Action, context: GameContext, combat: dict) -> dict:
        """Resolve using an item during combat. Delegates to InventorySystem then syncs combat HP."""
        item_name = action.target_id or action.parameters.get("item_name") or ""

        if not item_name:
            # Bare "3" with no item — list usable combat items
            return self._prompt_item_list(context)

        from text_rpg.systems.inventory.system import InventorySystem

        inv_system = InventorySystem()
        item_action = Action(
            id=action.id,
            actor_id=action.actor_id,
            action_type="use_item",
            target_id=item_name,
            parameters=action.parameters,
            raw_input=action.raw_input,
        )
        result = inv_system.resolve(item_action, context)

        # Sync HP mutations to combat state
        for mut in result.state_mutations:
            if mut.field == "hp_current":
                self._set_combatant_hp(combat, mut.target_id, mut.new_value)

        return {
            "success": result.success,
            "description": result.outcome_description,
            "mutations": list(result.state_mutations),
            "dice_rolls": list(result.dice_rolls),
            "events": list(result.events),
            "xp": 0,
        }

    def _prompt_item_list(self, context: GameContext) -> dict:
        """Return a prompt showing usable combat items when player types bare '3'."""
        inv = context.inventory
        if not inv:
            return {"success": False, "description": "You don't have any items."}

        items = safe_json(inv.get("items"), [])
        if not items:
            return {"success": False, "description": "Your inventory is empty."}

        all_items_data = load_all_items()
        usable = []
        for entry in items:
            item_id = entry.get("item_id", "")
            item_data = all_items_data.get(item_id, {})
            item_type = item_data.get("item_type", "")
            # Show potions, scrolls, and consumables
            if item_type in ("potion", "scroll", "consumable") or item_id in ("healing_potion",):
                name = item_data.get("name", item_id)
                qty = entry.get("quantity", 1)
                usable.append(f"{name} x{qty}" if qty > 1 else name)

        if not usable:
            return {"success": False, "description": "You have no usable combat items. (Potions, scrolls)"}

        return {
            "success": False,
            "description": f"Type 'use [item name]' to use an item.\nUsable: {', '.join(usable)}",
        }

    # -- Class Ability Resolution --

    def _resolve_class_ability(self, action: Action, context: GameContext, combat: dict) -> dict:
        """Resolve a class-specific combat ability (rage, flurry, lay on hands, etc.)."""
        char = context.character
        char_id = char["id"]
        char_class = (char.get("char_class") or "").lower()
        raw = (action.raw_input or "").lower().strip()
        mutations: list[StateMutation] = []
        events: list[dict[str, Any]] = []
        dice_rolls: list[DiceRoll] = []

        if char_class == "barbarian" or raw in ("rage",):
            return self._resolve_rage(char, char_id, combat, mutations, events)

        if char_class == "monk" or raw in ("flurry", "flurry of blows"):
            return self._resolve_flurry(action, char, char_id, context, combat, mutations, events, dice_rolls)

        if char_class == "paladin" or raw in ("lay on hands",):
            return self._resolve_lay_on_hands(char, char_id, combat, mutations, events)

        if char_class == "bard" or raw in ("inspire", "bardic inspiration"):
            return self._resolve_bardic_inspiration(char, char_id, mutations, events)

        if char_class == "druid" or raw in ("wild shape",):
            return self._resolve_wild_shape(char, char_id, combat, mutations, events)

        return {"success": False, "description": "Your class doesn't have a special combat ability."}

    def _resolve_rage(self, char: dict, char_id: str, combat: dict,
                      mutations: list, events: list) -> dict:
        from text_rpg.mechanics.class_resources import get_rage_uses
        level = char.get("level", 1)
        remaining = char.get("rage_remaining")
        if remaining is None:
            remaining = get_rage_uses(level)
        if remaining <= 0:
            return {"success": False, "description": "You have no rage uses remaining."}

        # Set rage condition on player combatant
        self._set_player_condition(combat, "raging")
        mutations.append(StateMutation(
            target_type="character", target_id=char_id,
            field="rage_remaining", old_value=remaining, new_value=remaining - 1,
        ))
        events.append({
            "event_type": "CLASS_ABILITY",
            "description": "You enter a rage! Resistance to physical damage, +2 melee damage.",
            "actor_id": char_id,
            "mechanical_details": {"ability": "rage", "remaining": remaining - 1},
        })
        return {
            "success": True,
            "description": "You let out a primal roar and enter a RAGE! (+2 melee damage, resistance to bludgeoning/piercing/slashing)",
            "mutations": mutations, "dice_rolls": [], "events": events,
        }

    def _resolve_flurry(self, action: Action, char: dict, char_id: str,
                        context: GameContext, combat: dict,
                        mutations: list, events: list, dice_rolls: list) -> dict:
        ki = char.get("ki_remaining")
        if ki is None:
            from text_rpg.mechanics.class_resources import get_ki_points
            ki = get_ki_points(char.get("level", 1))
        if ki <= 0:
            return {"success": False, "description": "You have no ki points remaining."}

        # Spend 1 ki for two unarmed strikes
        mutations.append(StateMutation(
            target_type="character", target_id=char_id,
            field="ki_remaining", old_value=ki, new_value=ki - 1,
        ))

        target = self._find_combat_target(action.target_id, context, combat)
        if not target:
            return {"success": False, "description": "No valid target for Flurry of Blows."}

        scores = safe_json(char.get("ability_scores"), {})
        dex_mod = modifier(scores.get("dexterity", 10))
        prof = char.get("proficiency_bonus", 2)
        atk_bonus = dex_mod + prof
        target_ac = target.get("ac", 10)
        total_damage = 0
        parts: list[str] = ["Flurry of Blows!"]

        for i in range(2):
            from text_rpg.mechanics.combat_math import attack_roll as atk_roll, damage_roll as dmg_roll
            hit, crit, result = atk_roll(atk_bonus, target_ac)
            dice_rolls.append(DiceRoll(
                dice_expression="1d20", rolls=result.individual_rolls,
                modifier=atk_bonus, total=result.total,
                purpose=f"unarmed strike {i+1}",
            ))
            if hit:
                dmg = dmg_roll("1d4", dex_mod, crit)
                dice_rolls.append(DiceRoll(
                    dice_expression="1d4", rolls=dmg.individual_rolls,
                    modifier=dex_mod, total=dmg.total,
                    purpose=f"unarmed damage {i+1}",
                ))
                total_damage += dmg.total
                parts.append(f"Strike {i+1}: {'CRIT! ' if crit else ''}{dmg.total} damage.")
            else:
                parts.append(f"Strike {i+1}: Miss!")

        if total_damage > 0:
            old_hp = self._get_combatant_hp(combat, target["id"])
            new_hp = max(0, old_hp - total_damage)
            self._set_combatant_hp(combat, target["id"], new_hp)
            mutations.append(StateMutation(
                target_type="entity", target_id=target["id"],
                field="hp_current", old_value=old_hp, new_value=new_hp,
            ))
            if new_hp <= 0:
                self._mark_combatant_defeated(combat, target["id"])
                parts.append(f"{target['name']} is defeated!")

        events.append({
            "event_type": "CLASS_ABILITY",
            "description": " ".join(parts),
            "actor_id": char_id,
            "mechanical_details": {"ability": "flurry_of_blows", "ki_remaining": ki - 1, "damage": total_damage},
        })
        return {
            "success": True, "description": " ".join(parts),
            "mutations": mutations, "dice_rolls": dice_rolls, "events": events,
        }

    def _resolve_lay_on_hands(self, char: dict, char_id: str, combat: dict,
                              mutations: list, events: list) -> dict:
        pool = char.get("lay_on_hands_remaining")
        if pool is None:
            from text_rpg.mechanics.class_resources import get_lay_on_hands_pool
            pool = get_lay_on_hands_pool(char.get("level", 1))
        if pool <= 0:
            return {"success": False, "description": "Your Lay on Hands pool is empty."}

        hp_cur = self._get_combatant_hp(combat, char_id)
        hp_max = char.get("hp_max", 10)
        missing = hp_max - hp_cur
        if missing <= 0:
            return {"success": False, "description": "You are already at full health."}

        heal_amount = min(missing, pool, 10)  # Heal up to 10 per use
        new_hp = hp_cur + heal_amount
        self._set_combatant_hp(combat, char_id, new_hp)

        mutations.append(StateMutation(
            target_type="character", target_id=char_id,
            field="hp_current", old_value=hp_cur, new_value=new_hp,
        ))
        mutations.append(StateMutation(
            target_type="character", target_id=char_id,
            field="lay_on_hands_remaining", old_value=pool, new_value=pool - heal_amount,
        ))
        events.append({
            "event_type": "CLASS_ABILITY",
            "description": f"Lay on Hands: healed {heal_amount} HP. ({pool - heal_amount} pool remaining)",
            "actor_id": char_id,
        })
        return {
            "success": True,
            "description": f"You channel divine energy, healing yourself for {heal_amount} HP. (Pool: {pool - heal_amount} remaining)",
            "mutations": mutations, "dice_rolls": [], "events": events,
        }

    def _resolve_bardic_inspiration(self, char: dict, char_id: str,
                                     mutations: list, events: list) -> dict:
        remaining = char.get("bardic_inspiration_remaining")
        if remaining is None:
            from text_rpg.mechanics.class_resources import get_inspiration_uses
            scores = safe_json(char.get("ability_scores"), {})
            remaining = get_inspiration_uses(scores.get("charisma", 10))
        if remaining <= 0:
            return {"success": False, "description": "You have no Bardic Inspiration uses remaining."}

        from text_rpg.mechanics.class_resources import get_inspiration_die
        die = get_inspiration_die(char.get("level", 1))

        mutations.append(StateMutation(
            target_type="character", target_id=char_id,
            field="bardic_inspiration_remaining", old_value=remaining, new_value=remaining - 1,
        ))
        events.append({
            "event_type": "CLASS_ABILITY",
            "description": f"Used Bardic Inspiration ({die}). {remaining - 1} uses left.",
            "actor_id": char_id,
        })
        return {
            "success": True,
            "description": f"You play an inspiring melody! Gain a {die} Bardic Inspiration die to add to your next attack, check, or save.",
            "mutations": mutations, "dice_rolls": [], "events": events,
        }

    def _resolve_wild_shape(self, char: dict, char_id: str, combat: dict,
                            mutations: list, events: list) -> dict:
        remaining = char.get("wild_shape_remaining")
        if remaining is None:
            remaining = 2  # Wild Shape: 2 uses per short rest
        if remaining <= 0:
            return {"success": False, "description": "You have no Wild Shape uses remaining."}

        from text_rpg.mechanics.class_resources import get_wild_shape_temp_hp
        temp_hp = get_wild_shape_temp_hp(char.get("level", 1))

        # Add temp HP to player combatant
        player = self._get_combatant(combat, char_id)
        if player:
            hp = player.get("hp", {})
            if isinstance(hp, dict):
                hp["temp"] = temp_hp

        mutations.append(StateMutation(
            target_type="character", target_id=char_id,
            field="wild_shape_remaining", old_value=remaining, new_value=remaining - 1,
        ))
        events.append({
            "event_type": "CLASS_ABILITY",
            "description": f"Wild Shape: gained {temp_hp} temporary HP.",
            "actor_id": char_id,
        })
        return {
            "success": True,
            "description": f"You shift into a bestial form! Gained {temp_hp} temporary HP. ({remaining - 1} uses left)",
            "mutations": mutations, "dice_rolls": [], "events": events,
        }

    # -- NPC Turn Resolution --

    def _resolve_all_npc_turns(self, combat: dict, context: GameContext, stop_at_player: bool = False, skip_ids: set[str] | None = None) -> dict:
        """Resolve all non-player turns (enemies and companions). Returns combined results."""
        mutations: list[StateMutation] = []
        dice_rolls: list[DiceRoll] = []
        events: list[dict[str, Any]] = []
        descriptions: list[str] = []

        player_id = context.character["id"]
        player_combatant = self._get_combatant(combat, player_id)

        for eid in combat.get("turn_order", []):
            if self._get_combatant_hp(combat, player_id) <= 0:
                break
            if skip_ids and eid in skip_ids:
                continue
            if stop_at_player and eid == player_id:
                break
            combatant = self._get_combatant(combat, eid)
            if not combatant or combatant.get("combatant_type") == "player":
                continue
            if not combatant.get("is_active", True):
                continue

            hp = combatant.get("hp", {})
            if isinstance(hp, dict) and hp.get("current", 0) <= 0:
                continue

            c_type = combatant.get("combatant_type")

            if c_type == "companion":
                # Companion AI targets enemies
                enemy_targets = [c for c in combat["combatants"]
                                 if c.get("combatant_type") == "enemy" and c.get("is_active", True)]
                if enemy_targets:
                    ai_action = npc_choose_action(combatant, enemy_targets)
                    if ai_action["action"] == "attack":
                        # Simple companion attack (reuses NPC attack logic but targeting enemy)
                        target = enemy_targets[0]
                        comp_result = self._resolve_companion_attack(combatant, target, context, combat)
                        mutations.extend(comp_result["mutations"])
                        dice_rolls.extend(comp_result["dice_rolls"])
                        events.extend(comp_result["events"])
                        descriptions.append(comp_result["description"])
                continue

            # Enemy NPC AI
            # Enemies target player and companions
            targets = [c for c in combat["combatants"]
                       if c.get("combatant_type") in ("player", "companion") and c.get("is_active", True)]
            ai_action = npc_choose_action(combatant, targets)

            if ai_action["action"] == "flee":
                descriptions.append(f"{combatant['name']} attempts to flee!")
                self._mark_combatant_defeated(combat, eid)
                events.append({"event_type": "NPC_FLEE", "description": f"{combatant['name']} fled from combat."})
                continue

            if ai_action["action"] == "attack" and player_combatant:
                result = self._resolve_npc_attack(combatant, player_combatant, context, combat)
                mutations.extend(result["mutations"])
                dice_rolls.extend(result["dice_rolls"])
                events.extend(result["events"])
                descriptions.append(result["description"])

            elif ai_action["action"] == "dodge":
                descriptions.append(f"{combatant['name']} takes a defensive stance.")
                combatant.setdefault("conditions", []).append("dodging")

        return {
            "mutations": mutations, "dice_rolls": dice_rolls,
            "events": events, "description": "\n".join(descriptions),
        }

    def _resolve_npc_attack(
        self, npc: dict, target: dict, context: GameContext, combat: dict,
    ) -> dict:
        """Resolve an NPC attacking the player."""
        npc_name = npc.get("name", "Enemy")
        attack_spec: dict[str, Any] = {}

        # Get NPC attack bonus from entity data
        npc_entity = self._find_entity_for_combatant(npc, context)
        if npc_entity:
            e_scores = safe_json(npc_entity.get("ability_scores"), {})
            str_mod = modifier(e_scores.get("strength", 10))
            dex_mod = modifier(e_scores.get("dexterity", 10))
            atk_bonus = max(str_mod, dex_mod) + 2  # Assume +2 proficiency

            # Check for attacks array for damage dice
            attacks = safe_json(npc.get("attacks"), []) or safe_json(npc_entity.get("attacks"), [])
            if attacks and isinstance(attacks[0], dict):
                attack_index = int(npc.get("next_attack_index", 0)) % len(attacks)
                attack_spec = attacks[attack_index]
                npc["next_attack_index"] = (attack_index + 1) % len(attacks)
                dmg_dice = attack_spec.get("damage_dice", "1d6")
                dmg_mod = attack_spec.get("damage_bonus", max(str_mod, dex_mod))
            else:
                dmg_dice = "1d6"
                dmg_mod = max(str_mod, dex_mod)
        else:
            atk_bonus = 3
            dmg_dice = "1d6"
            dmg_mod = 1

        # Target AC
        target_hp = target.get("hp", {})
        if isinstance(target_hp, dict):
            target_ac = context.character.get("ac", 10)
        else:
            target_ac = target.get("ac", 10)

        # Check for advantage/disadvantage
        advantage = False
        disadvantage = False
        player_conditions = target.get("conditions", [])
        if "dodging" in player_conditions:
            disadvantage = True

        hit, is_critical, atk_result = attack_roll(atk_bonus, target_ac, advantage, disadvantage)

        dice_rolls = [DiceRoll(
            dice_expression="1d20", rolls=atk_result.individual_rolls,
            modifier=atk_bonus, total=atk_result.total,
            purpose=f"attack_roll ({npc_name})",
        )]
        mutations: list[StateMutation] = []
        events: list[dict[str, Any]] = []

        char_id = context.character["id"]

        if hit:
            dmg_result = damage_roll(dmg_dice, dmg_mod, is_critical)

            # Determine NPC's damage type for resistance check
            npc_dmg_type_raw = attack_spec.get("damage_type", "")

            # Apply player's resistance/vulnerability/immunity
            char_props = safe_json(context.character.get("properties"), {}) or {}
            effective_npc_dmg, npc_dmg_label = get_effective_damage(
                dmg_result.total,
                npc_dmg_type_raw,
                char_props.get("resistances", []),
                char_props.get("vulnerabilities", []),
                char_props.get("immunities", []),
            )

            dice_rolls.append(DiceRoll(
                dice_expression=dmg_dice, rolls=dmg_result.individual_rolls,
                modifier=dmg_mod, total=effective_npc_dmg,
                purpose=f"damage_roll ({npc_name})",
            ))

            old_hp = self._get_combatant_hp(combat, char_id)
            new_hp = max(0, old_hp - effective_npc_dmg)
            self._set_combatant_hp(combat, char_id, new_hp)

            mutations.append(StateMutation(
                target_type="character", target_id=char_id,
                field="hp_current",
                old_value=context.character.get("hp_current", old_hp),
                new_value=new_hp,
            ))

            crit = " CRITICAL HIT!" if is_critical else ""
            resist_text = f" ({npc_dmg_label})" if npc_dmg_label != "normal" else ""
            npc_attack_name = attack_spec.get("name", "attack")
            desc = (f"{npc_name} uses {npc_attack_name} and hits!{crit} "
                    f"{effective_npc_dmg} damage.{resist_text}")
            events.append({
                "event_type": "ATTACK",
                "description": f"{npc_name} attacks {context.character['name']} for {effective_npc_dmg} damage.",
                "actor_id": npc.get("entity_id", ""), "target_id": char_id,
                "mechanical_details": {
                    "damage": effective_npc_dmg, "critical": is_critical,
                    "npc_attack": True, "attack_style": "melee",
                    "damage_modifier": npc_dmg_label, "attack_name": npc_attack_name,
                },
            })

            # Check for wounds from heavy hits
            from text_rpg.mechanics.wounds import check_for_wound
            hp_max = context.character.get("hp_max", 10)
            wound = check_for_wound(effective_npc_dmg, hp_max)
            wound_text = None
            if wound and new_hp > 0:
                # Add wound to character
                wounds = safe_json(context.character.get("wounds"), [])
                wounds.append(wound)
                mutations.append(StateMutation(
                    target_type="character", target_id=char_id,
                    field="wounds", old_value=None, new_value=json.dumps(wounds),
                ))
                wound_text = wound['type'].replace('_', ' ')
                desc += f" You suffer a {wound_text}! ({wound['description']})"
                events.append({
                    "event_type": "WOUND",
                    "description": f"Suffered {wound_text} from {npc_name}'s attack.",
                    "target_id": char_id,
                    "mechanical_details": wound,
                })

            # Narrative prose from LLM
            narration = self._narrate_attack(
                attacker_name=npc_name, attacker_type="enemy",
                defender_name=context.character["name"], hit=True, critical=is_critical,
                damage=effective_npc_dmg, damage_type=npc_dmg_type_raw,
                defeated=new_hp <= 0, attack_name=npc_attack_name, wound=wound_text,
            )
            if narration:
                desc = narration

            if new_hp <= 0:
                events.append({
                    "event_type": "PLAYER_DEFEAT",
                    "description": f"{context.character['name']} has been defeated!",
                    "target_id": char_id,
                })
        else:
            npc_attack_name = attack_spec.get("name", "attack")
            desc = (f"{npc_name} uses {npc_attack_name} and misses! "
                    f"(Roll: {atk_result.total} vs AC {target_ac})")
            narration = self._narrate_attack(
                attacker_name=npc_name, attacker_type="enemy",
                defender_name=context.character["name"], hit=False, critical=False,
                damage=0, damage_type=attack_spec.get("damage_type", ""), defeated=False,
                attack_name=npc_attack_name,
            )
            if narration:
                desc = narration
            events.append({
                "event_type": "ATTACK",
                "description": f"{npc_name} attacks {context.character['name']} and misses.",
                "actor_id": npc.get("entity_id", ""), "target_id": char_id,
                "mechanical_details": {"hit": False, "npc_attack": True, "attack_style": "melee",
                                       "attack_name": npc_attack_name},
            })

        # Clear dodging after being attacked
        if "dodging" in target.get("conditions", []):
            target["conditions"].remove("dodging")

        return {"mutations": mutations, "dice_rolls": dice_rolls, "events": events, "description": desc}

    def _resolve_companion_attack(
        self, companion: dict, target: dict, context: GameContext, combat: dict,
    ) -> dict:
        """Resolve a companion attacking an enemy. Simplified version of NPC attack."""
        comp_name = companion.get("name", "Companion")
        target_name = target.get("name", "Enemy")

        # Get companion entity for attack data
        comp_entity = self._find_entity_for_combatant(companion, context)
        if comp_entity:
            e_scores = safe_json(comp_entity.get("ability_scores"), {})
            str_mod = modifier(e_scores.get("strength", 10))
            dex_mod = modifier(e_scores.get("dexterity", 10))
            atk_bonus = max(str_mod, dex_mod) + 2

            attacks = safe_json(comp_entity.get("attacks"), [])
            if attacks and isinstance(attacks[0], dict):
                dmg_dice = attacks[0].get("damage_dice", "1d6")
                dmg_mod = attacks[0].get("damage_bonus", max(str_mod, dex_mod))
            else:
                dmg_dice = "1d6"
                dmg_mod = max(str_mod, dex_mod)
        else:
            atk_bonus = 3
            dmg_dice = "1d6"
            dmg_mod = 1

        target_ac = target.get("ac", 10)
        hit, is_critical, atk_result = attack_roll(atk_bonus, target_ac, False, False)

        dice_rolls = [DiceRoll(
            dice_expression="1d20", rolls=atk_result.individual_rolls,
            modifier=atk_bonus, total=atk_result.total,
            purpose=f"attack_roll ({comp_name})",
        )]
        mutations: list[StateMutation] = []
        events: list[dict[str, Any]] = []

        if hit:
            dmg_result = damage_roll(dmg_dice, dmg_mod, is_critical)
            dice_rolls.append(DiceRoll(
                dice_expression=dmg_dice, rolls=dmg_result.individual_rolls,
                modifier=dmg_mod, total=dmg_result.total,
                purpose=f"damage_roll ({comp_name})",
            ))

            old_hp = self._get_combatant_hp(combat, target.get("entity_id", ""))
            new_hp = max(0, old_hp - dmg_result.total)
            self._set_combatant_hp(combat, target.get("entity_id", ""), new_hp)

            mutations.append(StateMutation(
                target_type="entity", target_id=target.get("entity_id", ""),
                field="hp_current", old_value=old_hp, new_value=new_hp,
            ))

            crit_text = " Critical!" if is_critical else ""
            desc = f"{comp_name} hits {target_name} for {dmg_result.total} damage!{crit_text}"

            events.append({
                "event_type": "ATTACK",
                "description": f"{comp_name} attacks {target_name} for {dmg_result.total} damage.",
                "actor_id": companion.get("entity_id", ""), "target_id": target.get("entity_id", ""),
            })

            if new_hp <= 0:
                self._mark_combatant_defeated(combat, target.get("entity_id", ""))
                mutations.append(StateMutation(
                    target_type="entity", target_id=target.get("entity_id", ""),
                    field="is_alive", old_value=True, new_value=False,
                ))
                desc += f" {target_name} is defeated!"
                events.append({
                    "event_type": "DEATH",
                    "description": f"{target_name} defeated by {comp_name}!",
                    "target_id": target.get("entity_id", ""),
                })
        else:
            desc = f"{comp_name} attacks {target_name} and misses."
            events.append({
                "event_type": "ATTACK",
                "description": f"{comp_name} attacks {target_name} and misses.",
                "actor_id": companion.get("entity_id", ""), "target_id": target.get("entity_id", ""),
            })

        return {"mutations": mutations, "dice_rolls": dice_rolls, "events": events, "description": desc}

    # -- Combat State Helpers --

    def _check_combat_end(self, combat: dict, context: GameContext) -> dict | None:
        """Check if combat has ended. Returns result dict or None."""
        import random
        from text_rpg.mechanics.death import calculate_death_penalty, get_weakened_condition, find_safe_location

        player_id = context.character["id"]
        player = self._get_combatant(combat, player_id)
        player_hp = 0
        if player:
            hp = player.get("hp", {})
            player_hp = hp.get("current", 0) if isinstance(hp, dict) else 0

        active_enemies = [
            c for c in combat.get("combatants", [])
            if c.get("combatant_type") == "enemy" and c.get("is_active", True)
            and (c.get("hp", {}).get("current", 0) if isinstance(c.get("hp"), dict) else 0) > 0
        ]

        if not active_enemies:
            total_xp = 0
            for c in combat.get("combatants", []):
                if c.get("combatant_type") == "enemy" and not c.get("is_active", True):
                    cr = c.get("challenge_rating", c.get("level", 1)) or 1
                    total_xp += int(cr * 100)

            # Generate loot drops
            loot_mutations, loot_desc = self._generate_loot(combat, context)
            defeated_names = [
                c.get("name", "enemy") for c in combat.get("combatants", [])
                if c.get("combatant_type") == "enemy" and
                (c.get("hp", {}).get("current", 0) if isinstance(c.get("hp"), dict) else 0) <= 0
            ]
            opponents = [c.get("name", "enemy") for c in combat.get("combatants", [])
                         if c.get("combatant_type") == "enemy"]
            location_props = dict(safe_json(context.location.get("properties"), {}))
            battles = list(location_props.get("battle_history", []))
            battles.append({
                "world_time": context.world_time,
                "outcome": "victory",
                "defeated": defeated_names,
                "opponents": opponents,
            })
            location_props["battle_history"] = battles[-8:]
            loot_mutations.append(StateMutation(
                target_type="location", target_id=context.location["id"], field="properties",
                old_value=context.location.get("properties"), new_value=location_props,
            ))

            desc = "Victory! All enemies have been defeated!"
            if loot_desc:
                desc += f"\n{loot_desc}"

            return {
                "description": desc,
                "result": "victory",
                "xp": total_xp,
                "mutations": loot_mutations,
                "events": [{
                    "event_type": "COMBAT_END", "description": "Combat ended in victory.",
                    "location_id": context.location["id"],
                    "mechanical_details": {"result": "victory", "defeated": defeated_names},
                }],
            }

        if player_hp <= 0:
            # Death penalty: lose gold, respawn at safe location, gain weakened condition
            char = context.character
            gold = char.get("gold", 0)
            penalty = calculate_death_penalty(gold)
            gold_lost = penalty["gold_lost"]
            weakened = get_weakened_condition()

            death_mutations: list[StateMutation] = []

            # Gold loss
            if gold_lost > 0:
                death_mutations.append(StateMutation(
                    target_type="character", target_id=player_id,
                    field="gold", old_value=gold, new_value=max(0, gold - gold_lost),
                ))

            # Revive with 1 HP
            death_mutations.append(StateMutation(
                target_type="character", target_id=player_id,
                field="hp_current", old_value=0, new_value=1,
            ))

            # Add weakened condition
            conditions = safe_json(char.get("conditions"), [])
            new_conditions = [c for c in conditions if c != "weakened"]
            new_conditions.append("weakened")
            death_mutations.append(StateMutation(
                target_type="character", target_id=player_id,
                field="conditions", old_value=conditions, new_value=new_conditions,
            ))

            # Store weakened duration on character (JSON wounds field)
            wounds = safe_json(char.get("wounds"), [])
            # Add weakened tracker
            wounds = [w for w in wounds if w.get("type") != "_weakened"]
            wounds.append({"type": "_weakened", "turns_remaining": weakened["turns_remaining"]})
            death_mutations.append(StateMutation(
                target_type="character", target_id=player_id,
                field="wounds", old_value=None, new_value=json.dumps(wounds),
            ))

            # Teleport to safe location
            if self._repos:
                locations = self._repos.get("location")
                if locations:
                    all_locs = locations.get_all(context.game_id)
                    safe_id = find_safe_location(all_locs)
                    if safe_id:
                        death_mutations.append(StateMutation(
                            target_type="game", target_id=context.game_id,
                            field="current_location_id", old_value=None, new_value=safe_id,
                        ))

            desc_parts = ["You have been defeated... darkness claims you."]
            if gold_lost > 0:
                desc_parts.append(f"You lost {gold_lost} gold.")
            desc_parts.append("You awaken weakened (-2 to all checks for 5 turns).")

            return {
                "description": "\n".join(desc_parts),
                "result": "defeat",
                "xp": 0,
                "mutations": death_mutations,
                "events": [{"event_type": "COMBAT_END", "description": "Combat ended in defeat."}],
            }

        return None

    def _generate_loot(self, combat: dict, context: GameContext) -> tuple[list[StateMutation], str]:
        """Generate loot drops from defeated enemies. Returns (mutations, description)."""
        import random

        mutations: list[StateMutation] = []
        loot_items: list[str] = []
        gold_total = 0
        char_id = context.character["id"]

        # Look up encounter loot tables by matching enemy names against known encounters
        loot_table = self._find_encounter_loot(combat, context)
        if not loot_table:
            return mutations, ""

        # Roll for item drops
        items = loot_table.get("items", [])
        for item_entry in items:
            if isinstance(item_entry, dict):
                item_id = item_entry.get("id", "")
                chance = item_entry.get("chance", 0.5)
                if item_id and random.random() < chance:
                    loot_items.append(item_id)
                    mutations.append(StateMutation(
                        target_type="inventory", target_id=char_id,
                        field="items_add", old_value=None,
                        new_value=json.dumps({"item_id": item_id, "quantity": 1}),
                    ))

        # Roll gold
        gold_min = loot_table.get("gold_min", 0)
        gold_max = loot_table.get("gold_max", 0)
        if gold_max > 0:
            gold_total = random.randint(gold_min, gold_max)
            if gold_total > 0:
                old_gold = context.character.get("gold", 0)
                mutations.append(StateMutation(
                    target_type="character", target_id=char_id,
                    field="gold", old_value=old_gold, new_value=old_gold + gold_total,
                ))

        # Build description
        desc_parts = []
        if loot_items:
            from text_rpg.content.loader import load_all_items
            all_items = load_all_items()
            item_names = [all_items.get(iid, {}).get("name", iid.replace("_", " ").title()) for iid in loot_items]
            desc_parts.append(f"Loot: {', '.join(item_names)}")
        if gold_total > 0:
            desc_parts.append(f"Gold: {gold_total} gp")

        return mutations, " | ".join(desc_parts) if desc_parts else ""

    def _find_encounter_loot(self, combat: dict, context: GameContext) -> dict:
        """Find the loot table for this combat by matching enemy entity IDs."""
        try:
            from text_rpg.content.loader import load_region

            # Collect enemy entity base IDs (without UUID suffix)
            enemy_names = set()
            for c in combat.get("combatants", []):
                if c.get("combatant_type") == "enemy":
                    # Entity IDs in combat are UUIDs, but we stored original entity_id
                    eid = c.get("entity_id", "")
                    name = c.get("name", "").lower()
                    enemy_names.add(name)

            # Load encounters from the current region
            region_id = context.location.get("region_id") or "verdant_reach"
            region_data = load_region(region_id)
            encounters = region_data.get("encounters", [])

            for enc in encounters:
                enc_entities = enc.get("entities", [])
                for ent in enc_entities:
                    if ent.get("name", "").lower() in enemy_names:
                        return enc.get("loot", {})
        except Exception as e:
            logger.warning(f"Failed to find encounter loot: {e}")
        return {}

    def _end_combat(self, combat: dict) -> None:
        """Mark combat as ended and update DB."""
        combat["is_active"] = False
        self._save_combat_state(combat)

    def _save_combat_state(self, combat: dict) -> None:
        """Persist combat state to DB."""
        if self._repos and self._repos.get("world_state"):
            try:
                self._repos["world_state"].update_combat(combat["id"], {
                    "is_active": combat.get("is_active", True),
                    "round_number": combat.get("round_number", 1),
                    "current_turn_index": combat.get("current_turn_index", 0),
                    "combatants": combat.get("combatants", []),
                    "turn_order": combat.get("turn_order", []),
                })
            except Exception as e:
                logger.error(f"Failed to update combat state: {e}")

    def _get_combatant(self, combat: dict, entity_id: str) -> dict | None:
        for c in combat.get("combatants", []):
            if c.get("entity_id") == entity_id:
                return c
        return None

    def _get_combatant_hp(self, combat: dict, entity_id: str) -> int:
        c = self._get_combatant(combat, entity_id)
        if not c:
            return 0
        hp = c.get("hp", {})
        return hp.get("current", 0) if isinstance(hp, dict) else 0

    def _set_combatant_hp(self, combat: dict, entity_id: str, new_hp: int) -> None:
        c = self._get_combatant(combat, entity_id)
        if c:
            hp = c.get("hp", {})
            if isinstance(hp, dict):
                hp["current"] = new_hp
            else:
                c["hp"] = {"current": new_hp, "max": 10, "temp": 0}

    def _mark_combatant_defeated(self, combat: dict, entity_id: str) -> None:
        c = self._get_combatant(combat, entity_id)
        if c:
            c["is_active"] = False

    def _set_player_condition(self, combat: dict, condition: str) -> None:
        for c in combat.get("combatants", []):
            if c.get("combatant_type") == "player":
                c.setdefault("conditions", []).append(condition)
                break

    def _find_entity_for_combatant(self, combatant: dict, context: GameContext) -> dict | None:
        """Find the full entity data for a combat combatant."""
        eid = combatant.get("entity_id", "")
        for e in context.entities:
            if e["id"] == eid:
                return e
        return None

    # -- Target Finding --

    def _find_target(self, target_id: str | None, context: GameContext) -> dict | None:
        """Find a hostile target by name or id (supports partial name matching)."""
        if not target_id:
            for e in context.entities:
                if e.get("is_hostile") and e.get("is_alive", True):
                    return e
            return None
        target_lower = target_id.lower()
        # Exact match first
        for e in context.entities:
            if e.get("is_alive", True) and e.get("is_hostile"):
                if e["id"] == target_id or e["name"].lower() == target_lower:
                    return e
        # Partial match fallback
        for e in context.entities:
            if e.get("is_alive", True) and e.get("is_hostile"):
                if target_lower in e["name"].lower() or e["name"].lower() in target_lower:
                    return e
        return None

    def _find_any_target(self, target_id: str | None, context: GameContext) -> dict | None:
        """Find any alive entity by name or id (including non-hostile, supports partial matching)."""
        if not target_id:
            return None
        target_lower = target_id.lower()
        # Exact match first
        for e in context.entities:
            if e.get("is_alive", True) and (e["id"] == target_id or e["name"].lower() == target_lower):
                return e
        # Partial match fallback
        for e in context.entities:
            if e.get("is_alive", True):
                if target_lower in e["name"].lower() or e["name"].lower() in target_lower:
                    return e
        return None

    def _find_combat_target(self, target_id: str | None, context: GameContext, combat: dict) -> dict | None:
        """Find a valid combat target — must be in combat and alive."""
        active_enemies = [
            c for c in combat.get("combatants", [])
            if c.get("combatant_type") == "enemy" and c.get("is_active", True)
        ]
        if not active_enemies:
            return None

        if target_id:
            # Try to match by name
            for c in active_enemies:
                if c.get("name", "").lower() == target_id.lower() or c.get("entity_id") == target_id:
                    # Return entity data
                    for e in context.entities:
                        if e["id"] == c["entity_id"]:
                            return e
            # Fuzzy match
            for c in active_enemies:
                if target_id.lower() in c.get("name", "").lower():
                    for e in context.entities:
                        if e["id"] == c["entity_id"]:
                            return e

        # Default to first active enemy
        first = active_enemies[0]
        for e in context.entities:
            if e["id"] == first["entity_id"]:
                return e
        return None

    # -- Weapon/Attack Helpers --

    def _get_attack_style(self, char: dict) -> str:
        """Determine if a character's attack is melee or ranged."""
        weapon = self._get_weapon_data(char)
        if weapon:
            weapon_type = weapon.get("weapon_type", "")
            if "ranged" in weapon_type:
                return "ranged"
        return "melee"

    def _get_weapon_data(self, char: dict) -> dict | None:
        weapon_id = char.get("equipped_weapon_id")
        if not weapon_id:
            return None
        from text_rpg.systems.item_catalog import get_item_catalog
        all_items = get_item_catalog(self._repos, char.get("game_id"), char.get("id"))
        return all_items.get(weapon_id)

    def _get_attack_bonus(self, char: dict) -> int:
        scores = safe_json(char.get("ability_scores"), {})
        str_mod = modifier(scores.get("strength", 10))
        dex_mod = modifier(scores.get("dexterity", 10))
        prof = char.get("proficiency_bonus", 2)

        weapon = self._get_weapon_data(char)
        if weapon:
            properties = weapon.get("properties", [])
            weapon_type = weapon.get("weapon_type", "")
            if "finesse" in properties:
                return max(str_mod, dex_mod) + prof
            elif "ranged" in weapon_type:
                return dex_mod + prof
            else:
                return str_mod + prof

        return max(str_mod, dex_mod) + prof

    def _get_damage_dice(self, char: dict) -> tuple[str, int]:
        scores = safe_json(char.get("ability_scores"), {})
        str_mod = modifier(scores.get("strength", 10))
        dex_mod = modifier(scores.get("dexterity", 10))

        weapon = self._get_weapon_data(char)
        if weapon:
            damage_dice = weapon.get("damage_dice", "1d8")
            properties = weapon.get("properties", [])
            weapon_type = weapon.get("weapon_type", "")
            if "finesse" in properties:
                return damage_dice, max(str_mod, dex_mod)
            elif "ranged" in weapon_type:
                return damage_dice, dex_mod
            else:
                return damage_dice, str_mod

        return "1d8", max(str_mod, dex_mod)

    def start_combat(self, context: GameContext, enemy_ids: list[str]) -> dict:
        """Create a new combat state with initiative rolls."""
        combatants = []
        char = context.character
        scores = safe_json(char.get("ability_scores"), {})
        dex_mod = modifier(scores.get("dexterity", 10))
        init = initiative_roll(dex_mod)
        combatants.append({
            "entity_id": char["id"], "name": char["name"], "combatant_type": "player",
            "initiative": init.total, "initiative_bonus": dex_mod,
            "hp": {"current": char.get("hp_current", 10), "max": char.get("hp_max", 10), "temp": 0},
            "ac": char.get("ac", 10), "is_active": True, "conditions": [], "has_acted": False,
        })
        # Add companions as allies
        for comp in context.companions:
            if comp.get("status") != "active":
                continue
            comp_entity = None
            for e in context.entities:
                if e["id"] == comp["entity_id"]:
                    comp_entity = e
                    break
            if comp_entity:
                c_scores = safe_json(comp_entity.get("ability_scores"), {})
                c_dex = modifier(c_scores.get("dexterity", 10))
                c_init = initiative_roll(c_dex)
                combatants.append({
                    "entity_id": comp_entity["id"], "name": comp_entity["name"],
                    "combatant_type": "companion",
                    "initiative": c_init.total, "initiative_bonus": c_dex,
                    "hp": {"current": comp_entity.get("hp_current", 10), "max": comp_entity.get("hp_max", 10), "temp": 0},
                    "ac": comp_entity.get("ac", 10), "is_active": True, "conditions": [], "has_acted": False,
                })

        for eid in enemy_ids:
            for e in context.entities:
                if e["id"] == eid:
                    e_scores = safe_json(e.get("ability_scores"), {})
                    e_dex = modifier(e_scores.get("dexterity", 10))
                    e_init = initiative_roll(e_dex)
                    combatants.append({
                        "entity_id": e["id"], "name": e["name"], "combatant_type": "enemy",
                        "initiative": e_init.total, "initiative_bonus": e_dex,
                        "hp": {"current": e.get("hp_current", 10), "max": e.get("hp_max", 10), "temp": 0},
                        "ac": e.get("ac", 10), "is_active": True, "conditions": [], "has_acted": False,
                        "challenge_rating": e.get("challenge_rating", e.get("level", 1)),
                        "attacks": safe_json(e.get("attacks"), []),
                        "behaviors": safe_json(e.get("behaviors"), []),
                        "next_attack_index": 0,
                    })
        init_pairs = [(c["entity_id"], c["initiative"]) for c in combatants]
        turn_order = determine_turn_order(init_pairs)
        return {
            "id": str(uuid.uuid4()), "game_id": context.game_id, "is_active": True,
            "round_number": 1, "current_turn_index": 0,
            "combatants": combatants, "turn_order": turn_order,
        }

    # -- Narrative Combat Prose --

    def _narrate_attack(
        self, attacker_name: str, attacker_type: str, defender_name: str,
        hit: bool, critical: bool, damage: int, damage_type: str,
        defeated: bool, attack_name: str = "weapon strike",
        wound: str | None = None, location_desc: str | None = None,
    ) -> str | None:
        """Use the engine's immediate hit/miss/damage text for combat."""
        # Per-attack generations serialize a round behind several model calls.
        # Returning None selects the existing authoritative combat description.
        return None

    # -- Puzzle Encounter Resolution --

    def _resolve_puzzle(self, action: Action, context: GameContext, encounter: dict) -> ActionResult:
        """Resolve a puzzle encounter instead of normal combat."""
        from text_rpg.mechanics.puzzles import evaluate_puzzle_attempt, get_puzzle_reward

        puzzle = encounter.get("puzzle", {})
        puzzle_type = puzzle.get("puzzle_type", "lock")
        enc_desc = encounter.get("description", "You face a puzzle.")
        props = dict(safe_json(context.location.get('properties'), {}))
        encounter_id = encounter.get('id', '')
        states = dict(props.get('puzzle_states', {}))
        state = dict(states.get(encounter_id, {}))
        if state.get('solved'):
            return ActionResult(action_id=action.id, success=False,
                                outcome_description=f"{encounter.get('name', 'This puzzle')} is already solved.")
        attempts = dict(state.get('attempts', {}))
        actor_id = context.character.get('id', '')
        level = int(context.character.get('level', 1))
        attempt = dict(attempts.get(actor_id, {}))
        if attempt.get('level') == level and int(attempt.get('count', 0)) >= 3:
            return ActionResult(action_id=action.id, success=False,
                                outcome_description="You have exhausted your useful approaches at your current level. Return after gaining a level.")

        def persist_result(result: ActionResult, succeeded: bool) -> ActionResult:
            latest = dict(safe_json(context.location.get('properties'), {}))
            latest_states = dict(latest.get('puzzle_states', {}))
            latest_state = dict(latest_states.get(encounter_id, {}))
            latest_attempts = dict(latest_state.get('attempts', {}))
            current = dict(latest_attempts.get(actor_id, {}))
            count = int(current.get('count', 0)) + 1 if current.get('level') == level else 1
            latest_attempts[actor_id] = {'level': level, 'count': count}
            latest_state.update({'attempts': latest_attempts, 'solved': bool(succeeded),
                                 'solved_at': context.world_time if succeeded else None})
            latest_states[encounter_id] = latest_state
            latest['puzzle_states'] = latest_states
            if succeeded:
                latest.pop('active_puzzle', None)
            result.state_mutations.append(StateMutation(
                target_type='location', target_id=context.location['id'], field='properties',
                old_value=context.location.get('properties'), new_value=latest,
            ))
            for event in result.events:
                event.setdefault('location_id', context.location['id'])
                event.setdefault('mechanical_details', {})['encounter_id'] = encounter_id
            return result

        # For riddle type with LLM, check if player gave the right answer
        if puzzle_type == "riddle" and self._llm:
            riddle_answer = puzzle.get("riddle_answer", "")
            player_answer = action.parameters.get("answer", action.raw_input or "")
            if riddle_answer and player_answer:
                answer_lower = player_answer.lower().strip()
                correct_lower = riddle_answer.lower().strip()
                # Direct match or LLM evaluation
                if correct_lower in answer_lower or answer_lower in correct_lower:
                    reward = get_puzzle_reward(encounter)
                    mutations, loot_desc = self._generate_puzzle_loot(encounter, context)
                    desc = f"{enc_desc}\n\nYou speak the answer: \"{player_answer}\".\nThe door groans open, revealing treasure beyond!"
                    if loot_desc:
                        desc += f"\n{loot_desc}"
                    return persist_result(ActionResult(
                        action_id=action.id, success=True,
                        outcome_description=desc,
                        state_mutations=mutations,
                        xp_gained=reward["xp"],
                        events=[{"event_type": "PUZZLE_SOLVED", "description": f"Solved: {encounter.get('name', 'puzzle')}"}],
                    ), True)

        result = evaluate_puzzle_attempt(
            puzzle=puzzle,
            action_description=action.raw_input or "",
            character=context.character,
            inventory=safe_json((context.inventory or {}).get('items'), []),
        )

        if result.get('missing_item'):
            return ActionResult(action_id=action.id, success=False,
                                outcome_description=f"{enc_desc}\n\n{result['description']}")

        dice_rolls = []
        if result.get("roll_result"):
            roll_res = result["roll_result"]
            dice_rolls.append(DiceRoll(
                dice_expression="1d20",
                rolls=roll_res.individual_rolls,
                modifier=roll_res.modifier,
                total=roll_res.total,
                purpose=f"{result.get('skill_used', 'check')} (DC {result['dc']})",
            ))

        mutations: list[StateMutation] = []
        events: list[dict[str, Any]] = []
        xp = 0
        desc = f"{enc_desc}\n\n{result['description']}"

        if result["success"]:
            reward = get_puzzle_reward(encounter)
            xp = reward["xp"]
            loot_mutations, loot_desc = self._generate_puzzle_loot(encounter, context)
            mutations.extend(loot_mutations)
            if loot_desc:
                desc += f"\n{loot_desc}"
            events.append({"event_type": "PUZZLE_SOLVED", "description": f"Solved: {encounter.get('name', 'puzzle')}"})
        else:
            # Trap damage on failure
            if puzzle_type == "trap" and not result.get("detected", True):
                trap_dmg_expr = puzzle.get("trap_damage", "2d6")
                from text_rpg.mechanics.dice import roll
                trap_roll = roll(trap_dmg_expr)
                trap_dmg = trap_roll.total
                char_id = context.character["id"]
                old_hp = context.character.get("hp_current", 10)
                new_hp = max(0, old_hp - trap_dmg)
                mutations.append(StateMutation(
                    target_type="character", target_id=char_id,
                    field="hp_current", old_value=old_hp, new_value=new_hp,
                ))
                dice_rolls.append(DiceRoll(
                    dice_expression=trap_dmg_expr, rolls=trap_roll.individual_rolls,
                    modifier=0, total=trap_dmg, purpose="trap_damage",
                ))
                desc += f" You take {trap_dmg} damage from the trap!"
                events.append({"event_type": "TRAP_DAMAGE", "description": f"Took {trap_dmg} trap damage."})
            events.append({"event_type": "PUZZLE_FAILED", "description": f"Failed: {encounter.get('name', 'puzzle')}"})

        return persist_result(ActionResult(
            action_id=action.id, success=result["success"],
            outcome_description=desc,
            dice_rolls=dice_rolls,
            state_mutations=mutations,
            events=events,
            xp_gained=xp,
        ), result["success"])

    def _generate_puzzle_loot(self, encounter: dict, context: GameContext) -> tuple[list[StateMutation], str]:
        """Generate loot for solved puzzles. Reuses loot table format."""
        import random

        loot_table = encounter.get("loot", {})
        if not loot_table:
            return [], ""

        mutations: list[StateMutation] = []
        loot_items: list[str] = []
        gold_total = 0
        char_id = context.character["id"]

        for item_entry in loot_table.get("items", []):
            if isinstance(item_entry, dict):
                item_id = item_entry.get("id", "")
                chance = item_entry.get("chance", 0.5)
                if item_id and random.random() < chance:
                    loot_items.append(item_id)
                    mutations.append(StateMutation(
                        target_type="inventory", target_id=char_id,
                        field="items_add", old_value=None,
                        new_value=json.dumps({"item_id": item_id, "quantity": 1}),
                    ))

        gold_min = loot_table.get("gold_min", 0)
        gold_max = loot_table.get("gold_max", 0)
        if gold_max > 0:
            gold_total = random.randint(gold_min, gold_max)
            if gold_total > 0:
                old_gold = context.character.get("gold", 0)
                mutations.append(StateMutation(
                    target_type="character", target_id=char_id,
                    field="gold", old_value=old_gold, new_value=old_gold + gold_total,
                ))

        desc_parts = []
        if loot_items:
            all_items = load_all_items()
            item_names = [all_items.get(iid, {}).get("name", iid.replace("_", " ").title()) for iid in loot_items]
            desc_parts.append(f"Loot: {', '.join(item_names)}")
        if gold_total > 0:
            desc_parts.append(f"Gold: {gold_total} gp")

        return mutations, " | ".join(desc_parts) if desc_parts else ""

    def _find_encounter_for_combat(self, context: GameContext) -> dict | None:
        """Find the encounter definition matching the current combat."""
        try:
            from text_rpg.content.loader import load_region
            region_id = context.location.get("region_id") or "verdant_reach"
            region_data = load_region(region_id)
            location_id = context.location.get("id", "")
            active = safe_json(context.location.get('properties'), {}).get('active_puzzle')
            if active:
                return next((enc for enc in region_data.get('encounters', [])
                             if enc.get('id') == active.get('encounter_id') and
                             location_id in enc.get('location_ids', [])), None)
            if not (context.combat_state and context.combat_state.get('is_active')):
                return None
            for enc in region_data.get("encounters", []):
                locs = enc.get("location_ids", [])
                if location_id in locs:
                    return enc
        except Exception:
            pass
        return None
