"""Player-requested rituals with an explicit quote before committing resources."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import asdict

from text_rpg.engine.continuity import Continuity
from text_rpg.engine.revival import latest_death
from text_rpg.mechanics.ability_scores import modifier
from text_rpg.mechanics.dice import roll_d20
from text_rpg.mechanics.revival import REVIVAL_RITES, revival_block_reason
from text_rpg.models.action import ActionResult, DiceRoll, StateMutation
from text_rpg.systems.base import GameSystem
from text_rpg.utils import safe_props, safe_json


class RevivalSystem(GameSystem):
    system_id = 'revival'
    handled_action_types = {'revival_options', 'revival_quote', 'revival_confirm', 'revival_cancel'}

    def __init__(self):
        self.repos = {}

    def inject(self, *, repos=None, **kwargs):
        if repos is not None:
            self.repos = repos

    def can_handle(self, action, context):
        return action.action_type in self.handled_action_types

    def get_available_actions(self, context):
        return [{'action_type': 'revival_options', 'description': 'Ask about local revival rituals and their costs'}]

    def _clear(self, game_id):
        with self.repos['save_game'].db.get_connection() as conn:
            conn.execute('DELETE FROM revival_offers WHERE game_id=?', (game_id,))

    @staticmethod
    def _match(reference, entities):
        reference = reference.casefold().strip()
        exact = [e for e in entities if reference in {e['name'].casefold(), e['id'].casefold()}]
        return exact or [e for e in entities if reference and reference in e['name'].casefold()]

    def _terms(self, context, source, target):
        if context.combat_state and context.combat_state.get('is_active'):
            raise ValueError('An hour-long ritual cannot begin during combat.')
        if any(e.get('is_alive', True) and e.get('is_hostile') for e in context.entities):
            raise ValueError('A hostile creature is present. Secure the area before attempting an hour-long ritual.')
        if source.get('location_id') != context.location['id'] or target.get('location_id') != context.location['id']:
            raise ValueError('The caster and the body must both be here.')
        if not source.get('is_alive', True) or source.get('is_hostile') or source.get('generated'):
            raise ValueError('That person cannot provide an established revival ritual.')
        condition = Continuity(self.repos).location_condition(context.game_id, context.location['id'])
        if condition and condition['condition'] == 'destroyed':
            raise ValueError('The ritual site has been destroyed.')
        stock = safe_props(source).get('revival_resources', {})
        method = stock.get('method')
        rite = REVIVAL_RITES.get(method)
        if not rite or not stock.get('offers_service', False):
            raise ValueError('That person does not offer a revival service.')
        if source.get('level', 1) < rite.min_level:
            raise ValueError('The caster lacks the power required for this rite.')
        if stock.get('charges', 0) < 1 or stock.get('materials_gp', 0) < rite.materials_gp:
            raise ValueError('The caster lacks the reserved materials or ritual charge. More gold alone cannot replace missing supplies.')
        if stock.get('ready_at', 0) > context.world_time:
            raise ValueError('The caster has not recovered from their previous ritual.')
        death = latest_death(self.repos, context.game_id, target['id'])
        if not death:
            raise ValueError('There is no recorded death for that person.')
        reason = revival_block_reason(target, context.world_time + 60 - death['world_time'],
                                      rite.max_age_minutes, restores_body=rite.restores_body)
        if reason:
            raise ValueError(reason)
        price = max(rite.materials_gp, int(stock.get('price_gp', rite.materials_gp + rite.min_level * 10)))
        bonus = modifier(safe_json(source.get('ability_scores'), {}).get(rite.ability, 10)) + 2 + (source['level'] - 1) // 4
        state = {'source_id': source['id'], 'target_id': target['id'], 'death_id': death['id'],
                 'location_id': context.location['id'], 'method': method, 'price': price,
                 'stock': stock, 'bonus': bonus, 'target_properties': safe_props(target)}
        state['rite_contract'] = asdict(rite)
        signature = hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()
        return rite, stock, {**state, 'signature': signature}

    def resolve(self, action, context):
        def response(text):
            return ActionResult(action_id=action.id, outcome_description=text)

        if action.action_type == 'revival_cancel':
            self._clear(context.game_id)
            return response('The revival offer is cancelled. Nothing was spent.')

        if action.action_type == 'revival_options':
            lines = ['Revival rituals require a capable caster, a body here, finite materials and one hour. '
                     'Use revival <name> with <caster> to review the exact cost and risks before deciding.']
            for source in context.entities:
                stock = safe_props(source).get('revival_resources', {})
                rite = REVIVAL_RITES.get(stock.get('method'))
                if rite and stock.get('offers_service') and source.get('is_alive', True) and not source.get('generated') and not source.get('is_hostile'):
                    price = max(rite.materials_gp, int(stock.get('price_gp', rite.materials_gp + rite.min_level * 10)))
                    lines.append(f"{source['name']}: {rite.name}, {price} gp per attempt, DC {rite.dc}; {stock.get('charges', 0)} ritual charges remaining.")
            if len(lines) == 1:
                lines.append('No established ritual provider is here. A caster who knows Revivify can instead use cast revivify on <name>, with its normal requirements.')
            bodies = [e['name'] for e in context.entities if not e.get('is_alive', True)]
            if bodies:
                lines.append('Recorded dead here: ' + ', '.join(bodies))
            return response('\n'.join(lines))

        if action.action_type == 'revival_quote':
            # A failed new request must never leave an older offer armed.
            self._clear(context.game_id)
            match = re.fullmatch(r'(.+?)\s+(?:with|through)\s+(.+)', action.target_id or '', re.I)
            if not match:
                return response('Use revival <person> with <caster>, or revival options to see local providers.')
            targets = self._match(match[1], context.entities)
            sources = self._match(match[2], context.entities)
            if len(targets) != 1 or len(sources) != 1:
                return response('Specify one person and one caster here by name; neither may be ambiguous.')
            target, source = targets[0], sources[0]
            try:
                rite, stock, terms = self._terms(context, source, target)
            except ValueError as exc:
                return response(str(exc))
            if context.character.get('gold', 0) < terms['price']:
                return response(f"The attempt costs {terms['price']} gp, but you have {context.character.get('gold', 0)} gp. Nothing was spent.")
            terms['expires_at'] = context.world_time + 60
            with self.repos['save_game'].db.get_connection() as conn:
                conn.execute('INSERT OR REPLACE INTO revival_offers VALUES (?,?)', (context.game_id, json.dumps(terms)))
            return response(f"{source['name']} offers {rite.name} for {target['name']}.\n"
                f"Cost: {terms['price']} gp and one hour. The caster spends {rite.materials_gp} gp of reserved materials and one charge.\n"
                f"Check: 1d20 + {terms['bonus']} against DC {rite.dc}. Success returns them with 1 HP and {rite.consequence}.\n"
                'Failure leaves them dead; the payment, materials and time are still spent. The caster needs a day to recover before another attempt.\n'
                'Type confirm revival to proceed, or cancel revival. This offer expires in one game hour.')

        with self.repos['save_game'].db.get_connection() as conn:
            row = conn.execute('SELECT data FROM revival_offers WHERE game_id=?', (context.game_id,)).fetchone()
        if not row:
            return response('There is no revival offer to confirm. Use revival <person> with <caster> first.')
        offer = json.loads(row['data'])
        source = self.repos['entity'].get(offer['source_id'], context.game_id)
        target = self.repos['entity'].get(offer['target_id'], context.game_id)
        if not source or not target or context.world_time >= offer['expires_at']:
            self._clear(context.game_id)
            return response('The revival offer has expired or its participants are no longer available. Nothing was spent.')
        try:
            rite, stock, terms = self._terms(context, source, target)
        except ValueError as exc:
            self._clear(context.game_id)
            return response(str(exc) + ' Nothing was spent.')
        if terms['signature'] != offer['signature'] or context.character.get('gold', 0) < terms['price']:
            self._clear(context.game_id)
            return response('The ritual terms or available resources have changed. Request a new offer; nothing was spent.')

        check = roll_d20(terms['bonus'])
        passed = check.total >= rite.dc
        props = safe_props(source)
        props['revival_resources'] = {**stock, 'charges': stock['charges'] - 1,
            'materials_gp': stock['materials_gp'] - rite.materials_gp, 'ready_at': context.world_time + 60 + 1440}
        props['ritual_service_income_gp'] = props.get('ritual_service_income_gp', 0) + terms['price']
        self._clear(context.game_id)
        outcome = (f"{target['name']} returns with 1 HP and {rite.consequence}." if passed
                   else f"{target['name']} remains dead. The ritual has failed.")
        description = f"{source['name']} performs {rite.name}. {check.total} against DC {rite.dc}. {outcome} You spent {terms['price']} gp and one hour; the caster consumed materials and a ritual charge."
        return ActionResult(action_id=action.id, success=passed, outcome_description=description,
            dice_rolls=[DiceRoll(dice_expression='1d20', rolls=check.individual_rolls, modifier=check.modifier,
                                total=check.total, purpose=f'{rite.name} (DC {rite.dc})')],
            state_mutations=[
                StateMutation(target_type='character', target_id=context.character['id'], field='gold',
                              old_value=context.character['gold'], new_value=context.character['gold'] - terms['price']),
                StateMutation(target_type='entity', target_id=source['id'], field='properties', new_value=props)],
            events=[{'id': str(uuid.uuid4()), 'event_type': 'REVIVAL' if passed else 'REVIVAL_ATTEMPT',
                'world_time': context.world_time + 60,
                'actor_id': source['id'], 'target_id': target['id'], 'location_id': context.location['id'],
                'description': description, 'mechanical_details': {
                    'cause_id': terms['death_id'], 'method': terms['method'], 'restored_hp': 1 if passed else 0,
                    'restores_body': rite.restores_body, 'consequence': rite.consequence if passed else 'failed ritual; resources spent',
                    'duration_minutes': 60, 'player_cost_gp': terms['price'], 'materials_gp': rite.materials_gp,
                    'charge_consumed': 1, 'roll': check.total, 'dc': rite.dc}}])
