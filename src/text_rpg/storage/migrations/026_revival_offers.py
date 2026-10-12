"""Reviewable ritual offers, owned by a save and its current timeline."""
import json


def upgrade(conn):
    conn.execute('''CREATE TABLE revival_offers (
        game_id TEXT PRIMARY KEY,
        data TEXT NOT NULL
    )''')
    # Introduce the service to existing authored NPCs once. Existing budgets,
    # cooldowns, explicit refusals and life state are never reset by an upgrade.
    providers = {
        'high_priestess_auriel': {'method': 'temple_intercession', 'charges': 1,
            'materials_gp': 500, 'target_ids': ['healer_rosa', 'lord_baric'],
            'ready_at': 0, 'offers_service': True, 'price_gp': 750},
        'fey_archon': {'method': 'fey_bargain', 'charges': 1, 'materials_gp': 1000,
            'ready_at': 0, 'offers_service': True, 'price_gp': 1500},
    }
    for row in conn.execute("SELECT game_id,id,properties,generated FROM entities WHERE id IN ('high_priestess_auriel','fey_archon')").fetchall():
        if row['generated']:
            continue
        props = json.loads(row['properties'] or '{}')
        if not isinstance(props, dict):
            continue
        defaults = providers[row['id']]
        if 'revival_resources' not in props:
            props['revival_resources'] = defaults.copy()
        elif isinstance(props['revival_resources'], dict):
            props['revival_resources'].setdefault('offers_service', True)
            props['revival_resources'].setdefault('price_gp', defaults['price_gp'])
        conn.execute('UPDATE entities SET properties=? WHERE game_id=? AND id=?',
                     (json.dumps(props), row['game_id'], row['id']))
