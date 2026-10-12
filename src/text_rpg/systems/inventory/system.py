"""Inventory system — item usage, equip/unequip, and management."""
from __future__ import annotations

import json
from typing import Any

from text_rpg.mechanics.ability_scores import modifier
from text_rpg.mechanics.combat_math import calculate_ac, calculate_ac_unarmored
from text_rpg.models.action import Action, ActionResult, StateMutation
from text_rpg.systems.base import GameContext, GameSystem
from text_rpg.utils import safe_json

ITEM_EFFECTS: dict[str, dict[str, Any]] = {
    "healing_potion": {"heal": 7, "description": "You drink a healing potion and feel warmth spread through you."},
    "torch": {"light": True, "description": "You light the torch, illuminating the area."},
}


class InventorySystem(GameSystem):
    def __init__(self) -> None:
        self._repos: dict[str, Any] | None = None

    def inject(self, *, repos: dict | None = None, **kwargs: Any) -> None:
        if repos is not None:
            self._repos = repos

    def _catalog(self, context: GameContext) -> dict[str, dict]:
        """Item catalog including this character's custom (invented) items."""
        from text_rpg.systems.item_catalog import get_item_catalog
        return get_item_catalog(
            self._repos, context.game_id, context.character.get("id"),
        )

    @property
    def system_id(self) -> str:
        return "inventory"

    @property
    def handled_action_types(self) -> set[str]:
        return {"use_item", "equip", "unequip", "pickup"}

    def can_handle(self, action: Action, context: GameContext) -> bool:
        return action.action_type.lower() in self.handled_action_types

    def resolve(self, action: Action, context: GameContext) -> ActionResult:
        action_type = action.action_type.lower()
        if action_type == "pickup":
            from text_rpg.systems.inventory.pickup import pickup
            return pickup(action, context, self._repos, self._catalog(context))
        if action_type == "equip":
            return self._resolve_equip(action, context)
        elif action_type == "unequip":
            return self._resolve_unequip(action, context)
        return self._resolve_use_item(action, context)

    def _resolve_equip(self, action: Action, context: GameContext) -> ActionResult:
        item_name = (action.target_id or "").lower().strip()
        if not item_name:
            return ActionResult(action_id=action.id, success=False, outcome_description="Equip what?")

        # Find item in inventory
        inv = context.inventory
        if not inv:
            return ActionResult(action_id=action.id, success=False, outcome_description="You don't have any items.")

        items = safe_json(inv.get("items"), [])

        all_items = self._catalog(context)
        found_entry = None
        found_item_data = None
        for entry in items:
            item_id = entry.get("item_id", "")
            item_data = all_items.get(item_id, {})
            display_name = item_data.get("name", item_id).lower()
            if item_id.lower() == item_name or item_name in display_name or display_name in item_name or item_name in item_id.lower().replace("_", " "):
                found_entry = entry
                found_item_data = item_data
                break

        if not found_entry or not found_item_data:
            return ActionResult(action_id=action.id, success=False, outcome_description=f"You don't have '{item_name}'.")

        item_type = found_item_data.get("item_type", "")
        item_id = found_entry["item_id"]
        char = context.character
        char_id = char["id"]
        mutations: list[StateMutation] = []
        events: list[dict[str, Any]] = []

        if item_type == "weapon":
            old_weapon = char.get("equipped_weapon_id")
            mutations.append(StateMutation(
                target_type="character", target_id=char_id,
                field="equipped_weapon_id", old_value=old_weapon, new_value=item_id,
            ))
            display_name = found_item_data.get("name", item_id)
            desc = f"You equip the {display_name}."
            if old_weapon and old_weapon != item_id:
                old_name = all_items.get(old_weapon, {}).get("name", old_weapon)
                desc = f"You swap your {old_name} for the {display_name}."
            events.append({
                "event_type": "EQUIP",
                "description": desc,
                "actor_id": char_id,
                "mechanical_details": {"item_id": item_id, "slot": "weapon"},
            })
            return ActionResult(action_id=action.id, success=True, outcome_description=desc,
                                state_mutations=mutations, events=events)

        elif item_type == "armor":
            armor_type = found_item_data.get("armor_type", "")
            display_name = found_item_data.get("name", item_id)

            if armor_type == "shield":
                # Shield: just update equipped_armor_id won't work well alongside armor.
                # For simplicity, treat shield as a separate concept but store in equipped_armor_id
                # if no armor is equipped, otherwise we'd need a third slot.
                # For now: equip as armor slot (shield replaces armor in this simplified model).
                # Actually, let's keep it simple: shields go in armor slot too.
                pass

            old_armor = char.get("equipped_armor_id")
            mutations.append(StateMutation(
                target_type="character", target_id=char_id,
                field="equipped_armor_id", old_value=old_armor, new_value=item_id,
            ))

            # Recalculate AC
            new_ac = self._calculate_equipped_ac(char, found_item_data)
            old_ac = char.get("ac", 10)
            mutations.append(StateMutation(
                target_type="character", target_id=char_id,
                field="ac", old_value=old_ac, new_value=new_ac,
            ))

            desc = f"You equip the {display_name}. Your AC is now {new_ac}."
            if old_armor and old_armor != item_id:
                old_name = all_items.get(old_armor, {}).get("name", old_armor)
                desc = f"You swap your {old_name} for the {display_name}. Your AC is now {new_ac}."
            events.append({
                "event_type": "EQUIP",
                "description": desc,
                "actor_id": char_id,
                "mechanical_details": {"item_id": item_id, "slot": "armor", "new_ac": new_ac},
            })
            return ActionResult(action_id=action.id, success=True, outcome_description=desc,
                                state_mutations=mutations, events=events)

        return ActionResult(action_id=action.id, success=False,
                            outcome_description=f"You can't equip {found_item_data.get('name', item_id)} — it's not a weapon or armor.")

    def _resolve_unequip(self, action: Action, context: GameContext) -> ActionResult:
        slot_name = (action.target_id or "").lower().strip()
        if not slot_name:
            return ActionResult(action_id=action.id, success=False, outcome_description="Unequip what? (weapon, armor, or all)")

        char = context.character
        char_id = char["id"]
        all_items = self._catalog(context)
        mutations: list[StateMutation] = []
        events: list[dict[str, Any]] = []
        descriptions: list[str] = []

        # Determine what to unequip
        unequip_weapon = slot_name in ("weapon", "all") or slot_name == char.get("equipped_weapon_id", "")
        unequip_armor = slot_name in ("armor", "shield", "all") or slot_name == char.get("equipped_armor_id", "")

        # Also try to match by item name
        if not unequip_weapon and not unequip_armor:
            equipped_weapon = char.get("equipped_weapon_id")
            equipped_armor = char.get("equipped_armor_id")
            if equipped_weapon:
                weapon_data = all_items.get(equipped_weapon, {})
                weapon_name = weapon_data.get("name", equipped_weapon).lower()
                if slot_name in weapon_name or weapon_name in slot_name or slot_name in equipped_weapon.replace("_", " "):
                    unequip_weapon = True
            if equipped_armor:
                armor_data = all_items.get(equipped_armor, {})
                armor_name = armor_data.get("name", equipped_armor).lower()
                if slot_name in armor_name or armor_name in slot_name or slot_name in equipped_armor.replace("_", " "):
                    unequip_armor = True

        if not unequip_weapon and not unequip_armor:
            return ActionResult(action_id=action.id, success=False,
                                outcome_description=f"You don't have '{slot_name}' equipped.")

        if unequip_weapon:
            old_weapon = char.get("equipped_weapon_id")
            if old_weapon:
                weapon_name = all_items.get(old_weapon, {}).get("name", old_weapon)
                mutations.append(StateMutation(
                    target_type="character", target_id=char_id,
                    field="equipped_weapon_id", old_value=old_weapon, new_value=None,
                ))
                descriptions.append(f"You put away your {weapon_name}.")
                events.append({
                    "event_type": "UNEQUIP",
                    "description": f"Unequipped {weapon_name}",
                    "actor_id": char_id,
                    "mechanical_details": {"item_id": old_weapon, "slot": "weapon"},
                })

        if unequip_armor:
            old_armor = char.get("equipped_armor_id")
            if old_armor:
                armor_name = all_items.get(old_armor, {}).get("name", old_armor)
                mutations.append(StateMutation(
                    target_type="character", target_id=char_id,
                    field="equipped_armor_id", old_value=old_armor, new_value=None,
                ))
                descriptions.append(f"You remove your {armor_name}.")
                events.append({
                    "event_type": "UNEQUIP",
                    "description": f"Unequipped {armor_name}",
                    "actor_id": char_id,
                    "mechanical_details": {"item_id": old_armor, "slot": "armor"},
                })

                # Recalculate AC as unarmored
                scores = safe_json(char.get("ability_scores"), {})
                dex_mod = modifier(scores.get("dexterity", 10))
                new_ac = calculate_ac_unarmored(dex_mod)
                old_ac = char.get("ac", 10)
                mutations.append(StateMutation(
                    target_type="character", target_id=char_id,
                    field="ac", old_value=old_ac, new_value=new_ac,
                ))
                descriptions.append(f"Your AC is now {new_ac}.")

        if not descriptions:
            return ActionResult(action_id=action.id, success=False,
                                outcome_description="Nothing to unequip.")

        return ActionResult(
            action_id=action.id, success=True,
            outcome_description=" ".join(descriptions),
            state_mutations=mutations, events=events,
        )

    def _calculate_equipped_ac(self, char: dict, armor_data: dict) -> int:
        """Calculate AC for a character equipping the given armor."""
        scores = safe_json(char.get("ability_scores"), {})
        dex_mod = modifier(scores.get("dexterity", 10))

        armor_type = armor_data.get("armor_type", "light")
        ac_base = armor_data.get("ac_base", 10)

        if armor_type == "shield":
            # Shield adds +2 on top of current armor or unarmored AC
            # Check if character has armor already equipped
            current_armor_id = char.get("equipped_armor_id")
            if current_armor_id:
                all_items = self._catalog(context)
                current_armor = all_items.get(current_armor_id, {})
                if current_armor.get("armor_type") != "shield":
                    base_ac = calculate_ac(
                        current_armor.get("ac_base", 10), dex_mod,
                        current_armor.get("armor_type", "light"), shield=True,
                    )
                    return base_ac
            return calculate_ac_unarmored(dex_mod) + 2

        return calculate_ac(ac_base, dex_mod, armor_type)

    def _resolve_use_item(self, action: Action, context: GameContext) -> ActionResult:
        item_name = (action.target_id or "").lower()
        if not item_name:
            return ActionResult(action_id=action.id, success=False, outcome_description="Use what?")

        inv = context.inventory
        if not inv:
            return ActionResult(action_id=action.id, success=False, outcome_description="You don't have any items.")

        items = safe_json(inv.get("items"), [])

        found_item = None
        for entry in items:
            item_id = entry.get("item_id", "")
            if item_id.lower() == item_name or item_name in item_id.lower():
                found_item = entry
                break

        if not found_item:
            return ActionResult(action_id=action.id, success=False, outcome_description=f"You don't have '{item_name}'.")

        item_id = found_item["item_id"]

        # Check if this is a scroll — delegate to scroll usage
        all_items_data = self._catalog(context)
        item_full_data = all_items_data.get(item_id, {})
        if item_full_data.get("item_type") == "scroll":
            return self._resolve_use_scroll(action, context, found_item, item_full_data)

        effects = ITEM_EFFECTS.get(item_id, {})

        mutations: list[StateMutation] = []
        events: list[dict[str, Any]] = []

        if effects.get("heal"):
            heal_amount = effects["heal"]
            char = context.character
            old_hp = char.get("hp_current", 0)
            max_hp = char.get("hp_max", old_hp)
            new_hp = min(old_hp + heal_amount, max_hp)
            mutations.append(StateMutation(
                target_type="character", target_id=char["id"],
                field="hp_current", old_value=old_hp, new_value=new_hp,
            ))
            # Remove one from inventory
            mutations.append(StateMutation(
                target_type="inventory", target_id=char["id"],
                field="items_remove_one", old_value=None, new_value=item_id,
            ))
            actual_healed = new_hp - old_hp
            events.append({
                "event_type": "ITEM_USE",
                "description": f"Used {item_id}. Healed {actual_healed} HP.",
                "actor_id": char["id"],
            })
            events.append({
                "event_type": "HEAL",
                "description": f"Healed {actual_healed} HP from {item_id}.",
                "actor_id": char["id"],
                "mechanical_details": {"amount": actual_healed, "source": item_id},
            })

        # Apply survival need effects
        from text_rpg.mechanics.survival import apply_item_to_needs
        char = context.character
        need_effects = apply_item_to_needs(
            item_id,
            char.get("hunger", 100) or 100,
            char.get("thirst", 100) or 100,
            char.get("warmth", 80) or 80,
            char.get("morale", 75) or 75,
        )
        need_desc_parts = []
        if need_effects:
            for need_name, new_val in need_effects.items():
                old_val = char.get(need_name, 100) or 100
                if new_val != old_val:
                    mutations.append(StateMutation(
                        target_type="character", target_id=char["id"],
                        field=need_name, old_value=old_val, new_value=new_val,
                    ))
                    diff = new_val - old_val
                    if diff > 0:
                        need_desc_parts.append(f"{need_name.title()} +{diff}")
            # Remove from inventory for consumables
            if not effects:
                mutations.append(StateMutation(
                    target_type="inventory", target_id=char["id"],
                    field="items_remove_one", old_value=None, new_value=item_id,
                ))
                events.append({
                    "event_type": "ITEM_USE",
                    "description": f"Used {item_id}.",
                    "actor_id": char["id"],
                })

        all_items_data = self._catalog(context)
        item_display_name = all_items_data.get(item_id, {}).get("name", item_id)

        if not effects and not need_effects:
            description = f"You use the {item_display_name}, but nothing happens."
        elif need_desc_parts and not effects:
            description = f"You consume the {item_display_name}. ({', '.join(need_desc_parts)})"
        elif need_desc_parts:
            description = effects.get("description", f"You use the {item_display_name}.")
            description += f" ({', '.join(need_desc_parts)})"
        else:
            description = effects.get("description", f"You use the {item_display_name}.")

        return ActionResult(
            action_id=action.id, success=True, outcome_description=description,
            state_mutations=mutations, events=events,
        )

    def _resolve_use_scroll(
        self, action: Action, context: GameContext,
        inv_entry: dict, item_data: dict,
    ) -> ActionResult:
        """Use a spell scroll — casts the spell without consuming a spell slot."""
        char = context.character
        char_id = char["id"]
        effects = safe_json(item_data.get("effects"), {})
        spell_id = effects.get("spell_id", "")
        if not spell_id:
            return ActionResult(
                action_id=action.id, success=False,
                outcome_description="This scroll is blank — it has no spell inscribed.",
            )

        from text_rpg.content.loader import load_all_spells
        all_spells = load_all_spells()
        spell = all_spells.get(spell_id)
        if not spell:
            return ActionResult(
                action_id=action.id, success=False,
                outcome_description=f"The spell '{spell_id}' inscribed on this scroll is unknown.",
            )

        # Build a temporary action for spellcasting resolution
        scroll_name = item_data.get("name", inv_entry["item_id"])
        spell_name = spell.get("name", spell_id)

        # Get spellcasting stats — scrolls use the character's own ability if they
        # have one, otherwise default to Intelligence (reading the scroll).
        scores = safe_json(char.get("ability_scores"), {})
        casting_ability = char.get("spellcasting_ability") or "intelligence"
        ability_score = scores.get(casting_ability, 10)
        prof_bonus = char.get("proficiency_bonus", 2)

        from text_rpg.mechanics.ability_scores import modifier as calc_mod
        from text_rpg.mechanics.spellcasting import (
            calculate_spell_attack_bonus, calculate_spell_dc,
            calculate_healing, calculate_spell_damage,
            resolve_spell_attack, resolve_spell_save,
            scale_cantrip_dice,
        )
        from text_rpg.mechanics.dice import roll as dice_roll
        from text_rpg.models.action import DiceRoll

        spell_dc = calculate_spell_dc(ability_score, prof_bonus)
        spell_attack_bonus = calculate_spell_attack_bonus(ability_score, prof_bonus)
        casting_mod = calc_mod(ability_score)

        mutations: list[StateMutation] = []
        events: list[dict[str, Any]] = []
        dice_rolls: list[DiceRoll] = []
        outcome_parts: list[str] = [f"You read the {scroll_name} aloud."]

        mechanics = spell.get("mechanics", {})
        spell_type = mechanics.get("type", "utility")

        # Find target entity
        spell_target = action.parameters.get("spell_target") or action.target_id
        target_entity = None
        if spell_target:
            for entity in context.entities:
                e_name = entity.get("name", "").lower()
                if spell_target.lower() == e_name or spell_target.lower() in e_name:
                    target_entity = entity
                    break

        if spell_type == "attack":
            damage_dice = mechanics.get("damage_dice", "1d6")
            damage_type = mechanics.get("damage_type", "magical")
            if spell.get("level", 0) == 0:
                damage_dice = scale_cantrip_dice(damage_dice, char.get("level", 1))
            if not target_entity:
                outcome_parts.append(f"{spell_name} fizzles — no target in range.")
            else:
                t_name = target_entity.get("name", "the target")
                t_id = target_entity.get("id", "")
                hit, critical, atk_result = resolve_spell_attack(spell_attack_bonus, target_entity.get("ac", 10))
                dice_rolls.append(DiceRoll(
                    dice_expression="1d20", rolls=atk_result.individual_rolls,
                    modifier=spell_attack_bonus, total=atk_result.total,
                    purpose=f"scroll spell attack vs {t_name}",
                ))
                if hit:
                    dmg = calculate_spell_damage(damage_dice, critical)
                    dice_rolls.append(DiceRoll(
                        dice_expression=damage_dice, rolls=dmg.individual_rolls,
                        modifier=0, total=dmg.total,
                        purpose=f"{damage_type} damage" + (" (CRITICAL!)" if critical else ""),
                    ))
                    old_hp = target_entity.get("hp_current", 10)
                    new_hp = max(0, old_hp - dmg.total)
                    mutations.append(StateMutation(
                        target_type="entity", target_id=t_id,
                        field="hp_current", old_value=old_hp, new_value=new_hp,
                    ))
                    outcome_parts.append(f"{spell_name} hits {t_name} for {dmg.total} {damage_type} damage!")
                else:
                    outcome_parts.append(f"{spell_name} misses {t_name}.")

        elif spell_type == "healing":
            healing_dice = mechanics.get("healing_dice", "1d8")
            heal_result = calculate_healing(healing_dice, casting_mod)
            dice_rolls.append(DiceRoll(
                dice_expression=healing_dice, rolls=heal_result.individual_rolls,
                modifier=casting_mod, total=heal_result.total, purpose="scroll healing",
            ))
            old_hp = char.get("hp_current", 0)
            max_hp = char.get("hp_max", old_hp)
            new_hp = min(old_hp + heal_result.total, max_hp)
            healed = new_hp - old_hp
            mutations.append(StateMutation(
                target_type="character", target_id=char_id,
                field="hp_current", old_value=old_hp, new_value=new_hp,
            ))
            outcome_parts.append(f"{spell_name} restores {healed} hit points.")

        elif spell_type == "auto_hit":
            damage_dice = mechanics.get("damage_dice", "1d4+1")
            damage_type = mechanics.get("damage_type", "force")
            num_targets = mechanics.get("num_targets", 1)
            if not target_entity:
                outcome_parts.append(f"{spell_name} fizzles — no target in range.")
            else:
                t_name = target_entity.get("name", "the target")
                t_id = target_entity.get("id", "")
                total_damage = 0
                for i in range(num_targets):
                    dmg = dice_roll(damage_dice)
                    dice_rolls.append(DiceRoll(
                        dice_expression=damage_dice, rolls=dmg.individual_rolls,
                        modifier=dmg.modifier, total=dmg.total,
                        purpose=f"{damage_type} damage (dart {i+1})",
                    ))
                    total_damage += dmg.total
                old_hp = target_entity.get("hp_current", 10)
                new_hp = max(0, old_hp - total_damage)
                mutations.append(StateMutation(
                    target_type="entity", target_id=t_id,
                    field="hp_current", old_value=old_hp, new_value=new_hp,
                ))
                outcome_parts.append(f"{spell_name} strikes {t_name} for {total_damage} {damage_type} damage!")

        elif spell_type == "save":
            save_ability = mechanics.get("save_ability", "dexterity")
            damage_dice = mechanics.get("damage_dice")
            damage_type = mechanics.get("damage_type", "magical")
            effect = mechanics.get("effect")
            if not target_entity and damage_dice:
                outcome_parts.append(f"{spell_name} fizzles — no target in range.")
            elif target_entity:
                t_name = target_entity.get("name", "the target")
                t_id = target_entity.get("id", "")
                t_scores = safe_json(target_entity.get("ability_scores"), {})
                saved, save_result = resolve_spell_save(t_scores.get(save_ability, 10), spell_dc)
                dice_rolls.append(DiceRoll(
                    dice_expression="1d20", rolls=save_result.individual_rolls,
                    modifier=save_result.modifier, total=save_result.total,
                    purpose=f"{t_name} {save_ability[:3].upper()} save (DC {spell_dc})",
                ))
                if saved:
                    if damage_dice:
                        dmg = calculate_spell_damage(damage_dice)
                        half = max(1, dmg.total // 2)
                        mutations.append(StateMutation(
                            target_type="entity", target_id=t_id,
                            field="hp_current",
                            old_value=target_entity.get("hp_current", 10),
                            new_value=max(0, target_entity.get("hp_current", 10) - half),
                        ))
                        outcome_parts.append(f"{t_name} saves but takes {half} {damage_type} damage.")
                    else:
                        outcome_parts.append(f"{t_name} resists the effect!")
                else:
                    if damage_dice:
                        dmg = calculate_spell_damage(damage_dice)
                        old_hp = target_entity.get("hp_current", 10)
                        new_hp = max(0, old_hp - dmg.total)
                        mutations.append(StateMutation(
                            target_type="entity", target_id=t_id,
                            field="hp_current", old_value=old_hp, new_value=new_hp,
                        ))
                        outcome_parts.append(f"{t_name} fails the save and takes {dmg.total} {damage_type} damage!")
                    elif effect:
                        outcome_parts.append(f"{t_name} fails the save! Effect: {effect}.")
                    else:
                        outcome_parts.append(f"{t_name} fails the save!")
            else:
                outcome_parts.append(f"{spell_name} takes effect. {spell.get('description', '')}")

        elif spell_type == "buff":
            effect = mechanics.get("effect", "")
            if effect == "shield":
                outcome_parts.append(f"+{mechanics.get('ac_bonus', 5)} AC until your next turn.")
            elif effect == "mage_armor":
                ac_base = mechanics.get("ac_base", 13)
                dex_mod = calc_mod(scores.get("dexterity", 10))
                new_ac = ac_base + dex_mod
                mutations.append(StateMutation(
                    target_type="character", target_id=char_id,
                    field="ac", old_value=char.get("ac", 10), new_value=new_ac,
                ))
                outcome_parts.append(f"Your AC is now {new_ac}.")
            else:
                outcome_parts.append(spell.get("description", "The spell takes effect."))

        else:
            outcome_parts.append(f"{spell_name} takes effect. {spell.get('description', '')}")

        # Remove scroll from inventory (consumed on use)
        mutations.append(StateMutation(
            target_type="inventory", target_id=char_id,
            field="items_remove_one", old_value=None, new_value=inv_entry["item_id"],
        ))

        events.append({
            "event_type": "SCROLL_USE",
            "description": f"Used {scroll_name} to cast {spell_name}.",
            "actor_id": char_id,
            "mechanical_details": {"scroll_id": inv_entry["item_id"], "spell_id": spell_id},
        })

        outcome_parts.append("The scroll crumbles to dust.")

        return ActionResult(
            action_id=action.id, success=True,
            outcome_description=" ".join(outcome_parts),
            dice_rolls=dice_rolls,
            state_mutations=mutations,
            events=events,
        )

    def get_available_actions(self, context: GameContext) -> list[dict]:
        if not context.inventory:
            return []
        items = safe_json(context.inventory.get("items"), [])
        return [{"action_type": "use_item", "target": e.get("item_id", "?"), "description": f"Use {e.get('item_id', '?')}"} for e in items]
