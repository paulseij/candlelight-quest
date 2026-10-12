"""Transfer finite ground items to inventory without inventing item effects."""
import re
import uuid

from text_rpg.models.action import ActionResult
from text_rpg.utils import safe_json


def normalize_name(name):
    words = re.findall(r'\w+', str(name).lower().replace('_', ' '))
    if words and words[0] in {'a', 'an', 'the'}:
        words.pop(0)
    return ' '.join(words)


def ground_entries(items, catalog):
    entries = []
    for index, raw in enumerate(safe_json(items, [])):
        if isinstance(raw, str):
            item_id, quantity, name = raw, 1, catalog.get(raw, {}).get('name', raw)
        elif isinstance(raw, dict):
            item_id = raw.get('item_id') or raw.get('id') or ''
            name = catalog.get(item_id, {}).get('name') or raw.get('name') or item_id
            quantity = raw.get('quantity', 1)
        else:
            continue
        if name and isinstance(quantity, int) and not isinstance(quantity, bool) and quantity > 0:
            entries.append({'index': index, 'item_id': item_id, 'name': name, 'quantity': quantity})
    return entries


def pickup(action, context, repos, catalog):
    if context.combat_state and context.combat_state.get('is_active'):
        return ActionResult(action_id=action.id, outcome_description='You cannot collect ground items during combat. Choose a combat action first.')
    db = repos['save_game'].db
    with db.transaction():
        location = repos['location'].get(context.location['id'], context.game_id)
        ground = safe_json(location.get('items'), [])
        entries = ground_entries(ground, catalog)
        if not entries:
            return ActionResult(action_id=action.id, outcome_description='There are no items on the ground to pick up here.')
        target = normalize_name(action.target_id or '')
        all_items = target in {'all', 'everything', 'items', 'all items', 'all the items', 'everything here'}
        matches = entries if all_items else [e for e in entries if target and target in {normalize_name(e['name']), normalize_name(e['item_id'])}]
        if not matches and target:
            matches = [e for e in entries if target in normalize_name(e['name'])]
        if not matches or (len(matches) > 1 and not all_items):
            lead = 'Which item do you want to pick up?' if not target or matches else 'That item is not on the ground here.'
            return ActionResult(action_id=action.id, outcome_description=lead + '\n' + '\n'.join(f"- {e['name']} (x{e['quantity']})" for e in entries))
        inventory = repos['world_state'].get_inventory(context.character['id'], context.game_id)
        if inventory is None:
            inventory = {'id': str(uuid.uuid4()), 'game_id': context.game_id, 'owner_id': context.character['id'], 'items': []}
        owned = [dict(e) for e in safe_json(inventory.get('items'), [])]
        names = []
        for entry in matches:
            item_id = entry['item_id']
            if item_id not in catalog:
                # Legacy generated scenery stores item names, not catalog IDs.
                # Preserve the object as mundane misc gear; its name grants no powers.
                item_id = str(uuid.uuid5(uuid.NAMESPACE_URL, '\0'.join((context.game_id, context.character['id'], location['id'], normalize_name(entry['name'])))))
                if not repos['custom_item'].get_custom_item(item_id):
                    repos['custom_item'].save_custom_item({'id': item_id, 'game_id': context.game_id,
                        'character_id': context.character['id'], 'name': entry['name'],
                        'description': f"Collected at {location.get('name', 'this location')}.",
                        'item_type': 'misc', 'effects': [], 'value': 0, 'weight': 0,
                        'created_turn': context.turn_number, 'location_id': location['id']})
            stack = next((e for e in owned if e.get('item_id') == item_id), None)
            if stack:
                stack['quantity'] = stack.get('quantity', 1) + entry['quantity']
            else:
                owned.append({'item_id': item_id, 'quantity': entry['quantity']})
            names.append(f"{entry['name']} (x{entry['quantity']})")
        picked = {e['index'] for e in matches}
        inventory['items'] = owned
        repos['world_state'].save_inventory(inventory)
        repos['location'].update_field(location['id'], context.game_id, 'items', [raw for i, raw in enumerate(ground) if i not in picked])
        outcome = 'You pick up: ' + ', '.join(names) + '.'
        return ActionResult(action_id=action.id, success=True, outcome_description=outcome,
            events=[{'event_type': 'ITEM_PICKUP', 'actor_id': context.character['id'], 'location_id': location['id'], 'description': outcome}])
