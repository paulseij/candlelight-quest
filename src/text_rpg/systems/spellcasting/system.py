"""Spellcasting system — handles cast_spell actions."""
from __future__ import annotations

import json
from typing import Any

from text_rpg.content.loader import load_all_spells
from text_rpg.mechanics.ability_scores import modifier
from text_rpg.mechanics.elements import get_effective_damage
from text_rpg.mechanics.spellcasting import (
    SPELLCASTING_ABILITY,
    calculate_healing,
    calculate_spell_attack_bonus,
    calculate_spell_damage,
    calculate_spell_dc,
    can_cast_spell,
    find_usable_slot,
    resolve_spell_attack,
    resolve_spell_save,
    scale_cantrip_dice,
)
from text_rpg.models.action import Action, ActionResult, DiceRoll, StateMutation
from text_rpg.systems.base import GameContext, GameSystem
from text_rpg.utils import safe_json


class SpellcastingSystem(GameSystem):
    """Handles spell casting actions."""

    def __init__(self) -> None:
        self._repos: dict[str, Any] | None = None

    def inject(self, *, repos: dict | None = None, **kwargs) -> None:
        if repos is not None:
            self._repos = repos
        self._all_spells: dict[str, dict] | None = None

    @property
    def system_id(self) -> str:
        return "spellcasting"

    @property
    def handled_action_types(self) -> set[str]:
        return {"cast_spell"}

    def can_handle(self, action: Action, context: GameContext) -> bool:
        return action.action_type.lower() == "cast_spell"

    def resolve(self, action: Action, context: GameContext) -> ActionResult:
        return self._resolve_cast_spell(action, context)

    def get_available_actions(self, context: GameContext) -> list[dict]:
        if context.character.get("spellcasting_ability"):
            return [{"action_type": "cast_spell", "description": "Cast a spell"}]
        return []

    def _get_spells(self) -> dict[str, dict]:
        if self._all_spells is None:
            self._all_spells = load_all_spells()
        return self._all_spells

    def _get_all_spells_for_context(self, context: GameContext) -> dict[str, dict]:
        """Merge TOML spells with player-created custom spells."""
        base = dict(self._get_spells())
        repos = self._repos or {}
        spell_creation_repo = repos.get("spell_creation")
        if spell_creation_repo:
            customs = spell_creation_repo.get_custom_spells(context.game_id, context.character["id"])
            for cs in customs:
                base[cs["id"]] = {
                    "id": cs["id"],
                    "name": cs["name"],
                    "level": cs["level"],
                    "school": cs.get("school", "evocation"),
                    "description": cs["description"],
                    "mechanics": cs.get("mechanics", {}),
                    "elements": cs.get("elements", []),
                    "classes": [],  # Custom spells are class-agnostic for creator
                    "is_custom": True,
                }
        return base

    def _resolve_cast_spell(self, action: Action, context: GameContext) -> ActionResult:
        char = context.character
        char_id = char["id"]
        game_id = context.game_id

        # Check if character is a spellcaster
        casting_ability = char.get("spellcasting_ability")
        if not casting_ability:
            return ActionResult(
                action_id=action.id, success=False,
                outcome_description="You don't know how to cast spells.",
            )

        # Find the spell by fuzzy name match
        spell_name_input = (action.target_id or action.parameters.get("spell_name") or "").lower().strip()
        if not spell_name_input:
            return ActionResult(
                action_id=action.id, success=False,
                outcome_description="Cast what? Specify a spell name, e.g. 'cast fire bolt at goblin'.",
            )

        all_spells = self._get_all_spells_for_context(context)
        spell = self._find_spell(spell_name_input, all_spells)
        if not spell:
            return ActionResult(
                action_id=action.id, success=False,
                outcome_description=f"Unknown spell '{spell_name_input}'. Type 'spells' to see your known spells.",
            )

        if spell.get('mechanics', {}).get('effect') == 'revivify':
            # An invented spell cannot lower the canonical cost of this effect.
            spell = {**spell, 'level': max(3, spell.get('level', 0))}

        # Check spell is known/prepared
        repos = self._repos or {}
        spell_repo = repos.get("spell")
        char_class = char.get("char_class", "").lower()
        spell_level = spell.get("level", 0)

        if spell_repo:
            known = spell_repo.get_known_spells(game_id, char_id)
            prepared = spell_repo.get_prepared_spells(game_id, char_id)

            if spell["id"] not in known:
                return ActionResult(
                    action_id=action.id, success=False,
                    outcome_description=f"You don't know {spell['name']}.",
                )

            # Cantrips are always prepared; leveled spells must be prepared
            if spell_level > 0 and spell["id"] not in prepared:
                return ActionResult(
                    action_id=action.id, success=False,
                    outcome_description=f"{spell['name']} is not prepared. Use 'spells' to see your prepared spells.",
                )

        # Check spell slot availability
        slots_remaining = safe_json(char.get("spell_slots_remaining"), {})
        # Normalize keys to int
        slots_remaining = {int(k): v for k, v in slots_remaining.items()}

        castable, reason = can_cast_spell(spell, char.get("level", 1), slots_remaining, char_class)
        if not castable:
            return ActionResult(
                action_id=action.id, success=False,
                outcome_description=reason,
            )

        # Get spellcasting stats
        scores = safe_json(char.get("ability_scores"), {})
        ability_score = scores.get(casting_ability, 10)
        prof_bonus = char.get("proficiency_bonus", 2)
        spell_dc = calculate_spell_dc(ability_score, prof_bonus)
        spell_attack_bonus = calculate_spell_attack_bonus(ability_score, prof_bonus)
        casting_mod = modifier(ability_score)

        # Handle concentration: drop existing if casting new concentration spell
        mutations: list[StateMutation] = []
        events: list[dict] = []
        dice_rolls: list[DiceRoll] = []
        concentration_spell = char.get("concentration_spell")

        if spell.get("concentration") and concentration_spell:
            # Drop existing concentration
            mutations.append(StateMutation(
                target_type="character", target_id=char_id,
                field="concentration_spell", old_value=concentration_spell, new_value=None,
            ))
            events.append({
                "event_type": "SPELL_CONCENTRATION_LOST",
                "description": f"Lost concentration on {concentration_spell}.",
                "actor_id": char_id,
            })

        # Get target for combat spells
        spell_target = action.parameters.get("spell_target") or action.target_id
        target_entity = None
        if spell_target:
            # Find target entity by name
            for entity in context.entities:
                if entity.get("name", "").lower() == spell_target.lower():
                    target_entity = entity
                    break
                if spell_target.lower() in entity.get("name", "").lower():
                    target_entity = entity
                    break

        # Resolve spell effect based on type
        mechanics = spell.get("mechanics", {})
        spell_type = mechanics.get("type", "utility")
        outcome_parts: list[str] = []

        if mechanics.get('effect') == 'revivify':
            from text_rpg.engine.revival import resolve_revivify
            revival = resolve_revivify(action, context, repos)
            if not revival.success:
                return revival
            mutations.extend(revival.state_mutations)
            events.extend(revival.events)
            outcome_parts.append(revival.outcome_description)

        elif spell_type == "attack":
            result = self._resolve_attack_spell(
                spell, mechanics, spell_attack_bonus, target_entity,
                char, char_id, char.get("level", 1), dice_rolls, mutations, events,
            )
            outcome_parts.append(result)

        elif spell_type == "save":
            result = self._resolve_save_spell(
                spell, mechanics, spell_dc, target_entity,
                char, char_id, char.get("level", 1), dice_rolls, mutations, events,
            )
            outcome_parts.append(result)

        elif spell_type == "auto_hit":
            result = self._resolve_auto_hit_spell(
                spell, mechanics, target_entity, char_id, dice_rolls, mutations, events,
            )
            outcome_parts.append(result)

        elif spell_type == "healing":
            result = self._resolve_healing_spell(
                spell, mechanics, casting_mod, char, char_id, dice_rolls, mutations, events,
            )
            outcome_parts.append(result)

        elif spell_type == "buff":
            result = self._resolve_buff_spell(
                spell, mechanics, char, char_id, casting_mod, dice_rolls, mutations, events,
            )
            outcome_parts.append(result)

        elif spell_type == "utility":
            outcome_parts.append(f"You cast {spell['name']}. {spell.get('description', '')}")
            events.append({
                "event_type": "SPELL_CAST",
                "description": f"Cast {spell['name']}.",
                "actor_id": char_id,
                "mechanical_details": {"spell": spell["id"], "spell_level": spell_level},
            })

        # Consume spell slot (if not cantrip)
        if spell_level > 0:
            slot_used = find_usable_slot(spell_level, slots_remaining)
            if slot_used is not None:
                new_slots = dict(slots_remaining)
                new_slots[slot_used] = new_slots.get(slot_used, 1) - 1
                # Convert keys back to str for JSON
                new_slots_str = {str(k): v for k, v in new_slots.items()}
                old_slots_str = {str(k): v for k, v in slots_remaining.items()}
                mutations.append(StateMutation(
                    target_type="character", target_id=char_id,
                    field="spell_slots_remaining",
                    old_value=old_slots_str,
                    new_value=new_slots_str,
                ))
                outcome_parts.append(f"(Level {slot_used} spell slot consumed)")

        # Set concentration if applicable
        if spell.get("concentration"):
            mutations.append(StateMutation(
                target_type="character", target_id=char_id,
                field="concentration_spell",
                old_value=concentration_spell,
                new_value=spell["name"],
            ))

        return ActionResult(
            action_id=action.id, success=True,
            outcome_description=" ".join(outcome_parts),
            dice_rolls=dice_rolls,
            state_mutations=mutations,
            events=events,
        )

    def _resolve_attack_spell(
        self, spell: dict, mechanics: dict, attack_bonus: int,
        target_entity: dict | None, char: dict, char_id: str,
        char_level: int,
        dice_rolls: list, mutations: list, events: list,
    ) -> str:
        spell_name = spell["name"]
        damage_dice = mechanics.get("damage_dice", "1d6")
        damage_type = mechanics.get("damage_type", "magical")
        num_targets = mechanics.get("num_targets", 1)

        # Scale cantrip damage
        if spell.get("level", 0) == 0:
            damage_dice = scale_cantrip_dice(damage_dice, char_level)

        if not target_entity:
            return f"You cast {spell_name}, but there's no target in range."

        target_name = target_entity.get("name", "the target")
        target_ac = target_entity.get("ac", 10)
        target_id = target_entity.get("id", "")
        total_damage = 0
        parts: list[str] = []

        for i in range(num_targets):
            hit, critical, atk_result = resolve_spell_attack(attack_bonus, target_ac)
            dice_rolls.append(DiceRoll(
                dice_expression="1d20", rolls=atk_result.individual_rolls,
                modifier=attack_bonus, total=atk_result.total,
                purpose=f"spell attack vs {target_name}" + (f" (ray {i+1})" if num_targets > 1 else ""),
            ))

            if hit:
                dmg_result = calculate_spell_damage(damage_dice, critical)

                # Apply target resistance/vulnerability/immunity
                t_props = safe_json(target_entity.get("properties"), {}) or {}
                eff_dmg, eff_label = get_effective_damage(
                    dmg_result.total, damage_type,
                    t_props.get("resistances", []),
                    t_props.get("vulnerabilities", []),
                    t_props.get("immunities", []),
                )

                dice_rolls.append(DiceRoll(
                    dice_expression=damage_dice, rolls=dmg_result.individual_rolls,
                    modifier=0, total=eff_dmg,
                    purpose=f"{damage_type} damage" + (" (CRITICAL!)" if critical else ""),
                ))
                total_damage += eff_dmg
                resist_note = f" ({eff_label})" if eff_label != "normal" else ""
                if critical:
                    parts.append(f"Critical hit for {eff_dmg} {damage_type} damage!{resist_note}")
                else:
                    parts.append(f"Hit for {eff_dmg} {damage_type} damage.{resist_note}")
            else:
                parts.append("Miss!")

        if total_damage > 0:
            old_hp = target_entity.get("hp_current", 10)
            new_hp = max(0, old_hp - total_damage)
            mutations.append(StateMutation(
                target_type="entity", target_id=target_id,
                field="hp_current", old_value=old_hp, new_value=new_hp,
            ))

        summary = f"You cast {spell_name} at {target_name}. " + " ".join(parts)
        if total_damage > 0:
            summary += f" Total: {total_damage} damage."

        events.append({
            "event_type": "SPELL_CAST",
            "description": summary,
            "actor_id": char_id,
            "target_id": target_id,
            "mechanical_details": {
                "spell": spell["id"], "total_damage": total_damage,
                "damage_type": damage_type,
            },
        })
        return summary

    def _resolve_save_spell(
        self, spell: dict, mechanics: dict, spell_dc: int,
        target_entity: dict | None, char: dict, char_id: str,
        char_level: int,
        dice_rolls: list, mutations: list, events: list,
    ) -> str:
        spell_name = spell["name"]
        save_ability = mechanics.get("save_ability", "dexterity")
        damage_dice = mechanics.get("damage_dice")
        damage_type = mechanics.get("damage_type", "magical")
        effect = mechanics.get("effect")

        # Scale cantrip damage
        if spell.get("level", 0) == 0 and damage_dice:
            damage_dice = scale_cantrip_dice(damage_dice, char_level)

        if not target_entity and damage_dice:
            return f"You cast {spell_name}, but there's no target in range."

        if target_entity:
            target_name = target_entity.get("name", "the target")
            target_id = target_entity.get("id", "")
            target_scores = safe_json(target_entity.get("ability_scores"), {})
            target_ability = target_scores.get(save_ability, 10)

            saved, save_result = resolve_spell_save(target_ability, spell_dc)
            dice_rolls.append(DiceRoll(
                dice_expression="1d20", rolls=save_result.individual_rolls,
                modifier=save_result.modifier, total=save_result.total,
                purpose=f"{target_name} {save_ability[:3].upper()} save (DC {spell_dc})",
            ))

            # Get target's resistance properties
            sv_props = safe_json(target_entity.get("properties"), {}) or {}
            sv_resists = sv_props.get("resistances", [])
            sv_vulns = sv_props.get("vulnerabilities", [])
            sv_immunes = sv_props.get("immunities", [])

            if saved:
                if damage_dice:
                    # Most save spells do half damage on save
                    dmg_result = calculate_spell_damage(damage_dice)
                    half_damage = max(1, dmg_result.total // 2)
                    eff_half, eff_half_label = get_effective_damage(
                        half_damage, damage_type, sv_resists, sv_vulns, sv_immunes,
                    )
                    dice_rolls.append(DiceRoll(
                        dice_expression=damage_dice, rolls=dmg_result.individual_rolls,
                        modifier=0, total=eff_half,
                        purpose=f"{damage_type} damage (save: half)",
                    ))
                    old_hp = target_entity.get("hp_current", 10)
                    new_hp = max(0, old_hp - eff_half)
                    mutations.append(StateMutation(
                        target_type="entity", target_id=target_id,
                        field="hp_current", old_value=old_hp, new_value=new_hp,
                    ))
                    resist_note = f" ({eff_half_label})" if eff_half_label != "normal" else ""
                    summary = f"You cast {spell_name} at {target_name}. They save but take {eff_half} {damage_type} damage.{resist_note}"
                else:
                    summary = f"You cast {spell_name} at {target_name}. They resist the effect!"
            else:
                if damage_dice:
                    dmg_result = calculate_spell_damage(damage_dice)
                    eff_full, eff_full_label = get_effective_damage(
                        dmg_result.total, damage_type, sv_resists, sv_vulns, sv_immunes,
                    )
                    dice_rolls.append(DiceRoll(
                        dice_expression=damage_dice, rolls=dmg_result.individual_rolls,
                        modifier=0, total=eff_full,
                        purpose=f"{damage_type} damage",
                    ))
                    old_hp = target_entity.get("hp_current", 10)
                    new_hp = max(0, old_hp - eff_full)
                    mutations.append(StateMutation(
                        target_type="entity", target_id=target_id,
                        field="hp_current", old_value=old_hp, new_value=new_hp,
                    ))
                    resist_note = f" ({eff_full_label})" if eff_full_label != "normal" else ""
                    summary = f"You cast {spell_name} at {target_name}. They fail the save and take {eff_full} {damage_type} damage!{resist_note}"
                elif effect:
                    summary = f"You cast {spell_name} at {target_name}. They fail the save! Effect: {effect}."
                else:
                    summary = f"You cast {spell_name} at {target_name}. They fail the save!"

            events.append({
                "event_type": "SPELL_CAST",
                "description": summary,
                "actor_id": char_id,
                "target_id": target_id,
                "mechanical_details": {
                    "spell": spell["id"], "save_ability": save_ability,
                    "dc": spell_dc, "saved": saved,
                },
            })
            return summary

        # No target — utility save spell
        summary = f"You cast {spell_name}. {spell.get('description', '')}"
        events.append({
            "event_type": "SPELL_CAST",
            "description": summary,
            "actor_id": char_id,
            "mechanical_details": {"spell": spell["id"]},
        })
        return summary

    def _resolve_auto_hit_spell(
        self, spell: dict, mechanics: dict, target_entity: dict | None,
        char_id: str, dice_rolls: list, mutations: list, events: list,
    ) -> str:
        spell_name = spell["name"]
        damage_dice = mechanics.get("damage_dice", "1d4+1")
        damage_type = mechanics.get("damage_type", "force")
        num_targets = mechanics.get("num_targets", 1)

        if not target_entity:
            return f"You cast {spell_name}, but there's no target in range."

        target_name = target_entity.get("name", "the target")
        target_id = target_entity.get("id", "")
        total_damage = 0

        for i in range(num_targets):
            # Magic missile: each dart is 1d4+1
            from text_rpg.mechanics.dice import roll
            dmg_result = roll(damage_dice)
            dice_rolls.append(DiceRoll(
                dice_expression=damage_dice, rolls=dmg_result.individual_rolls,
                modifier=dmg_result.modifier, total=dmg_result.total,
                purpose=f"{damage_type} damage (dart {i+1})",
            ))
            total_damage += dmg_result.total

        old_hp = target_entity.get("hp_current", 10)
        new_hp = max(0, old_hp - total_damage)
        mutations.append(StateMutation(
            target_type="entity", target_id=target_id,
            field="hp_current", old_value=old_hp, new_value=new_hp,
        ))

        summary = f"You cast {spell_name} at {target_name}. {num_targets} darts strike automatically for {total_damage} {damage_type} damage!"
        events.append({
            "event_type": "SPELL_CAST",
            "description": summary,
            "actor_id": char_id,
            "target_id": target_id,
            "mechanical_details": {
                "spell": spell["id"], "total_damage": total_damage,
                "damage_type": damage_type, "auto_hit": True,
            },
        })
        return summary

    def _resolve_healing_spell(
        self, spell: dict, mechanics: dict, casting_mod: int,
        char: dict, char_id: str,
        dice_rolls: list, mutations: list, events: list,
    ) -> str:
        spell_name = spell["name"]
        healing_dice = mechanics.get("healing_dice", "1d8")

        heal_result = calculate_healing(healing_dice, casting_mod)
        dice_rolls.append(DiceRoll(
            dice_expression=healing_dice, rolls=heal_result.individual_rolls,
            modifier=casting_mod, total=heal_result.total,
            purpose="healing",
        ))

        old_hp = char.get("hp_current", 0)
        max_hp = char.get("hp_max", old_hp)
        new_hp = min(old_hp + heal_result.total, max_hp)
        healed = new_hp - old_hp

        mutations.append(StateMutation(
            target_type="character", target_id=char_id,
            field="hp_current", old_value=old_hp, new_value=new_hp,
        ))

        summary = f"You cast {spell_name} and recover {healed} hit points."
        events.append({
            "event_type": "SPELL_CAST",
            "description": summary,
            "actor_id": char_id,
            "mechanical_details": {"spell": spell["id"], "healed": healed},
        })
        if healed > 0:
            events.append({
                "event_type": "HEAL",
                "description": f"Healed {healed} HP with {spell_name}.",
                "actor_id": char_id,
                "mechanical_details": {"amount": healed, "source": spell["id"]},
            })
        return summary

    def _resolve_buff_spell(
        self, spell: dict, mechanics: dict, char: dict, char_id: str,
        casting_mod: int,
        dice_rolls: list, mutations: list, events: list,
    ) -> str:
        spell_name = spell["name"]
        effect = mechanics.get("effect", "")

        summary_parts = [f"You cast {spell_name}."]

        if effect == "shield":
            ac_bonus = mechanics.get("ac_bonus", 5)
            summary_parts.append(f"+{ac_bonus} AC until your next turn.")
        elif effect == "mage_armor":
            ac_base = mechanics.get("ac_base", 13)
            scores = safe_json(char.get("ability_scores"), {})
            dex_mod = modifier(scores.get("dexterity", 10))
            new_ac = ac_base + dex_mod
            mutations.append(StateMutation(
                target_type="character", target_id=char_id,
                field="ac", old_value=char.get("ac", 10), new_value=new_ac,
            ))
            summary_parts.append(f"Your AC is now {new_ac}.")
        elif effect == "bless":
            summary_parts.append("You gain +1d4 to attack rolls and saving throws.")
        elif effect == "guidance":
            summary_parts.append("You gain +1d4 to your next ability check.")
        else:
            summary_parts.append(spell.get("description", ""))

        summary = " ".join(summary_parts)
        events.append({
            "event_type": "SPELL_CAST",
            "description": summary,
            "actor_id": char_id,
            "mechanical_details": {"spell": spell["id"], "effect": effect},
        })
        return summary

    @staticmethod
    def _find_spell(name_input: str, all_spells: dict[str, dict]) -> dict | None:
        """Find a spell by fuzzy name match."""
        name_lower = name_input.lower().replace("_", " ")

        # Exact ID match
        if name_lower.replace(" ", "_") in all_spells:
            return all_spells[name_lower.replace(" ", "_")]

        # Exact name match
        for spell in all_spells.values():
            if spell["name"].lower() == name_lower:
                return spell

        # Substring match
        for spell in all_spells.values():
            if name_lower in spell["name"].lower() or spell["name"].lower() in name_lower:
                return spell

        return None
