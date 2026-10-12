"""Costed revival resolution and a shared, event-backed state transition."""
from __future__ import annotations

import uuid

from text_rpg.mechanics.revival import revival_block_reason
from text_rpg.models.action import ActionResult, StateMutation
from text_rpg.utils import safe_json, safe_props


def latest_death(repos, game_id, target_id):
    with repos['save_game'].db.get_connection() as conn:
        row = conn.execute(
            "SELECT h.*, e.turn_number FROM world_history h JOIN events e ON e.game_id=h.game_id AND e.id=h.id "
            "WHERE h.game_id=? AND h.subject_id=? AND h.event_type='DEATH' ORDER BY h.world_time DESC,h.rowid DESC LIMIT 1",
            (game_id, target_id)).fetchone()
    return dict(row) if row else None


def resolve_revivify(action, context, repos):
    """Spell knowledge and slot eligibility have already been checked by caller."""
    def refused(message):
        return ActionResult(action_id=action.id, outcome_description=message)

    name = str(action.parameters.get('spell_target') or '').strip().casefold()
    if not name:
        return refused('Cast Revivify on whom? It needs a body here, a death within one minute, and a 300 gp diamond.')
    exact = [e for e in context.entities if name in {e['id'].casefold(), e['name'].casefold()}]
    matches = exact or [e for e in context.entities if name in e['name'].casefold()]
    if len(matches) != 1:
        return refused('Specify one body here by name.' if not matches else 'That name is ambiguous: ' + ', '.join(e['name'] for e in matches))
    target = matches[0]
    death = latest_death(repos, context.game_id, target['id'])
    if target.get('is_alive', True):
        return refused('That person is already alive.')
    if not death:
        return refused('No recorded death time is available; Revivify’s one-minute window cannot be verified.')
    # Combat pauses the coarse exploration clock; committed turns still age a
    # corpse by at least one six-second combat round per action.
    age = max(context.world_time - death['world_time'], (context.turn_number - death['turn_number']) / 10)
    reason = revival_block_reason(target, age, 1)
    if reason:
        return refused(reason + ' No component or spell slot was spent.')

    # Only authored gems qualify. A generated item claiming a high price cannot
    # mint spell components, and ordinary gold is not a diamond.
    from text_rpg.content.loader import load_all_items
    catalog = load_all_items()
    components = []
    for entry in safe_json((context.inventory or {}).get('items'), []):
        item = catalog.get(entry.get('item_id'), {})
        properties = item.get('properties', {})
        if entry.get('quantity', 0) > 0 and isinstance(properties, dict) and properties.get('spell_component') == 'diamond' and item.get('value_gp', 0) >= 300:
            components.append(item)
    if not components:
        return refused('Revivify requires a diamond worth at least 300 gp in your inventory; it is consumed. Gold alone does not replace it. No spell slot was spent.')
    component = min(components, key=lambda item: item['value_gp'])
    description = f"You cast Revivify on {target['name']}. They return to life with 1 HP. {component['name']} is consumed. Their earlier death remains part of the world’s history."
    return ActionResult(action_id=action.id, success=True, outcome_description=description,
        state_mutations=[StateMutation(target_type='inventory', target_id=context.character['id'],
                                       field='items_remove_one', new_value=component['id'])],
        events=[{'id': str(uuid.uuid4()), 'event_type': 'REVIVAL', 'target_id': target['id'],
                 'actor_id': context.character['id'], 'location_id': context.location['id'],
                 'description': description,
                 'mechanical_details': {'cause_id': death['id'], 'method': 'revivify', 'restored_hp': 1,
                                        'component_item_id': component['id'], 'materials_gp': component['value_gp'],
                                        'consequence': 'returned with 1 HP'}}])


def apply_revivals(repos, game_id, events):
    """Called after recording events, inside the turn's atomic transaction."""
    revived = set()
    for event in events:
        if event.get('event_type') == 'DEATH' and event.get('target_id') in revived:
            # Another actor may kill a revived creature later in the same round.
            repos['entity'].update_field(event['target_id'], 'is_alive', False, game_id)
            repos['entity'].update_field(event['target_id'], 'hp_current', 0, game_id)
        if event.get('event_type') != 'REVIVAL':
            continue
        details = event['mechanical_details']
        repos['entity'].revive(event['target_id'], game_id,
            hp=details['restored_hp'], cause_event_id=event['id'])
        revived.add(event['target_id'])
        entity = repos['entity'].get(event['target_id'], game_id)
        props = safe_props(entity)
        if details.get('restores_body'):
            props['body_destroyed'] = False
            props['missing_vital_parts'] = False
        props['last_revival'] = {'event_id': event['id'], 'method': details['method'],
                                 'consequence': details.get('consequence', '')}
        repos['entity'].update_field(entity['id'], 'properties', props, game_id)
