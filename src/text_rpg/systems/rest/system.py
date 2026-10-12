"""Rest system — short and long rests."""
from __future__ import annotations

from text_rpg.mechanics.dice import roll
from text_rpg.mechanics.ability_scores import modifier
from text_rpg.mechanics.leveling import HIT_DICE
from text_rpg.mechanics.spellcasting import get_arcane_recovery_slots, get_spell_slots
from text_rpg.models.action import Action, ActionResult, DiceRoll, StateMutation
from text_rpg.systems.base import GameContext, GameSystem
from text_rpg.utils import safe_json

import json


class RestSystem(GameSystem):
    def __init__(self, repos: dict | None = None):
        self._repos = repos or {}

    def inject(self, *, repos: dict | None = None, **kwargs) -> None:
        if repos is not None:
            self._repos = repos

    @property
    def system_id(self) -> str:
        return "rest"

    @property
    def handled_action_types(self) -> set[str]:
        return {"rest"}

    def can_handle(self, action: Action, context: GameContext) -> bool:
        return action.action_type.lower() == "rest"

    def resolve(self, action: Action, context: GameContext) -> ActionResult:
        if context.combat_state and context.combat_state.get('is_active'):
            return ActionResult(action_id=action.id, outcome_description='You cannot rest during combat.')
        if any(e.get('is_alive', True) and e.get('is_hostile') for e in context.entities):
            return ActionResult(action_id=action.id, outcome_description='You cannot rest with a hostile creature present. Find shelter or deal with the threat first.')
        if context.character.get('hp_current', 0) <= 0:
            return ActionResult(action_id=action.id, outcome_description='You need at least 1 HP to begin a rest.')
        rest_type = action.parameters.get("rest_type", "short")
        if rest_type == "long":
            last = self._last_rest(context.game_id, 'long')
            earliest = last.get('completed_at', -960) + 960
            if last and context.world_time < earliest:
                wait = earliest - context.world_time
                return ActionResult(action_id=action.id, outcome_description=
                    f'You cannot benefit from another long rest yet. Wait {wait // 60}h {wait % 60}m before starting one. A long rest takes 8 hours.')
            return self._long_rest(action, context)
        return self._short_rest(action, context)

    def _last_rest(self, game_id, kind):
        from text_rpg.storage.database import Database
        db = getattr(self._repos.get('save_game'), 'db', None)
        if not isinstance(db, Database):
            return {}
        key = 'arcane_recovery' if kind == 'arcane_recovery' else 'rest_type'
        value = 1 if kind == 'arcane_recovery' else kind
        with db.get_connection() as conn:
            row = conn.execute(
                "SELECT data FROM world_history WHERE game_id=? AND event_type='REST' "
                "AND json_extract(data,?)=? ORDER BY world_time DESC,rowid DESC LIMIT 1",
                (game_id, '$.' + key, value)).fetchone()
        return safe_json(row['data'], {}) if row else {}

    @staticmethod
    def _rest_event(context, kind, description, *, arcane_recovery=False):
        duration = 480 if kind == 'long' else 60
        return {'event_type': 'REST', 'description': description,
                'mechanical_details': {'rest_type': kind, 'duration_minutes': duration,
                                       'completed_at': context.world_time + duration,
                                       'arcane_recovery': arcane_recovery}}

    def get_available_actions(self, context: GameContext) -> list[dict]:
        if context.combat_state and context.combat_state.get("is_active"):
            return []
        return [
            {"action_type": "rest", "parameters": {"rest_type": "short"}, "description": "Take a short rest"},
            {"action_type": "rest", "parameters": {"rest_type": "long"}, "description": "Take a long rest"},
        ]

    def _short_rest(self, action: Action, context: GameContext) -> ActionResult:
        char = context.character
        scores = safe_json(char.get("ability_scores"), {})
        con_mod = modifier(scores.get("constitution", 10))
        hit_dice_remaining = char.get("hit_dice_remaining", 0)

        cls = char.get("char_class", "fighter")
        if isinstance(cls, str):
            cls = cls.lower()
        hit_die = HIT_DICE.get(cls, "1d8")
        result = roll(hit_die) if hit_dice_remaining > 0 else None
        healed = max(1, result.total + con_mod) if result else 0

        old_hp = char.get("hp_current", 0)
        max_hp = char.get("hp_max", old_hp)
        new_hp = min(old_hp + healed, max_hp)

        mutations = [
            StateMutation(target_type="character", target_id=char["id"], field="hp_current", old_value=old_hp, new_value=new_hp),
            StateMutation(target_type="character", target_id=char["id"], field="hit_dice_remaining", old_value=hit_dice_remaining, new_value=hit_dice_remaining - 1),
        ] if result else []
        desc = f"You take a one-hour short rest and recover {new_hp - old_hp} hit points."
        used_recovery = False

        # Wizard Arcane Recovery: recover spell slot levels = ceil(level/2)
        last_recovery = self._last_rest(context.game_id, 'arcane_recovery') if cls == 'wizard' else {}
        last_long = self._last_rest(context.game_id, 'long') if cls == 'wizard' else {}
        recovery_available = not last_recovery or last_long.get('completed_at', -1) > last_recovery.get('completed_at', 0)
        if cls == "wizard" and char.get("spellcasting_ability") and recovery_available:
            slots_remaining = safe_json(char.get("spell_slots_remaining"), {})
            slots_max = safe_json(char.get("spell_slots_max"), {})

            recovery_budget = get_arcane_recovery_slots(char.get("level", 1))
            new_slots = {str(k): v for k, v in slots_remaining.items()}
            recovered_text = []
            for sl in sorted(int(k) for k in slots_max):
                if sl >= 6:
                    continue
                if recovery_budget <= 0:
                    break
                current = int(new_slots.get(str(sl), 0))
                maximum = int(slots_max.get(str(sl), slots_max.get(sl, 0)))
                can_recover = maximum - current
                if can_recover > 0 and sl <= recovery_budget:
                    recover_count = min(can_recover, recovery_budget // sl)
                    if recover_count > 0:
                        new_slots[str(sl)] = current + recover_count
                        recovery_budget -= sl * recover_count
                        recovered_text.append(f"{recover_count} level-{sl}")

            if recovered_text:
                used_recovery = True
                mutations.append(StateMutation(
                    target_type="character", target_id=char["id"],
                    field="spell_slots_remaining",
                    old_value={str(k): v for k, v in slots_remaining.items()},
                    new_value=new_slots,
                ))
                desc += f" Arcane Recovery: restored {', '.join(recovered_text)} spell slot(s)."

        actual_healed = new_hp - old_hp
        events = [self._rest_event(context, 'short', desc, arcane_recovery=used_recovery)]
        if actual_healed > 0:
            events.append({
                "event_type": "HEAL",
                "description": f"Healed {actual_healed} HP from short rest.",
                "actor_id": char["id"],
                "mechanical_details": {"amount": actual_healed, "source": "short_rest"},
            })

        return ActionResult(
            action_id=action.id, success=True,
            outcome_description=desc,
            dice_rolls=[DiceRoll(
                dice_expression=hit_die, rolls=result.individual_rolls,
                modifier=con_mod, total=healed, purpose="hit_dice_healing",
            )] if result else [],
            state_mutations=mutations,
            events=events,
        )

    def _long_rest(self, action: Action, context: GameContext) -> ActionResult:
        char = context.character
        old_hp = char.get("hp_current", 0)
        max_hp = char.get("hp_max", old_hp)
        max_dice = char.get("level", 1)
        old_dice = char.get("hit_dice_remaining", 0)
        restored_dice = min(max(max_dice // 2, 1), max_dice - old_dice)

        mutations = [
            StateMutation(target_type="character", target_id=char["id"], field="hp_current", old_value=old_hp, new_value=max_hp),
            StateMutation(target_type="character", target_id=char["id"], field="hit_dice_remaining", old_value=old_dice, new_value=old_dice + restored_dice),
            StateMutation(target_type="character", target_id=char["id"], field="conditions", old_value=None, new_value=[]),
        ]
        desc = f"You take an eight-hour long rest. All hit points restored. {restored_dice} hit dice recovered."

        # Restore all spell slots and clear concentration
        if char.get("spellcasting_ability"):
            slots_max = safe_json(char.get("spell_slots_max"), {})
            if slots_max:
                mutations.append(StateMutation(
                    target_type="character", target_id=char["id"],
                    field="spell_slots_remaining",
                    old_value=char.get("spell_slots_remaining"),
                    new_value={str(k): v for k, v in slots_max.items()},
                ))
                desc += " All spell slots restored."
            if char.get("concentration_spell"):
                mutations.append(StateMutation(
                    target_type="character", target_id=char["id"],
                    field="concentration_spell",
                    old_value=char.get("concentration_spell"),
                    new_value=None,
                ))

        # Attempt to heal wounds on long rest (50% each)
        from text_rpg.mechanics.wounds import heal_wound
        wounds = safe_json(char.get("wounds"), [])
        if wounds:
            remaining_wounds = []
            healed_wounds = []
            for wound in wounds:
                if wound.get("type") == "_weakened":
                    # Weakened always clears on long rest
                    healed_wounds.append("weakened")
                    continue
                if heal_wound(wound, "long_rest"):
                    healed_wounds.append(wound.get("type", "wound").replace("_", " "))
                else:
                    remaining_wounds.append(wound)
            if healed_wounds:
                desc += f" Wounds healed: {', '.join(healed_wounds)}."
            if remaining_wounds:
                desc += f" Wounds still festering: {len(remaining_wounds)}."
            mutations.append(StateMutation(
                target_type="character", target_id=char["id"],
                field="wounds", old_value=None, new_value=json.dumps(remaining_wounds),
            ))

        # Bounty decay on long rest
        if self._repos and self._repos.get("reputation"):
            try:
                # Get current region from location
                region_id = context.location.get("region_id", "")
                if region_id:
                    self._repos["reputation"].decay_bounty(context.game_id, region_id, 5)
                    bounty = self._repos["reputation"].get_bounty(context.game_id, region_id)
                    if bounty.get("amount", 0) > 0:
                        desc += f" Your bounty in the region has decreased slightly."
            except Exception:
                pass

        hp_healed = max_hp - old_hp
        events = [self._rest_event(context, 'long', desc)]
        if hp_healed > 0:
            events.append({
                "event_type": "HEAL",
                "description": f"Healed {hp_healed} HP from long rest.",
                "actor_id": char["id"],
                "mechanical_details": {"amount": hp_healed, "source": "long_rest"},
            })

        return ActionResult(
            action_id=action.id, success=True,
            outcome_description=desc,
            state_mutations=mutations,
            events=events,
        )
