"""Correct ambient locals' professions without erasing learned relationships."""
from __future__ import annotations

import json

from text_rpg.content.ambient_population import ambient_entity


def upgrade(conn):
    rows = conn.execute(
        "SELECT e.game_id,e.id AS entity_id,e.properties AS entity_properties,"
        "l.id AS location_id,l.name AS location_name,l.location_type FROM entities e "
        "JOIN locations l ON l.game_id=e.game_id AND l.id=e.location_id "
        "WHERE e.id LIKE 'ambient__%'"
    ).fetchall()
    for row in rows:
        data = dict(row)
        location = {'id': data['location_id'], 'name': data['location_name'],
                    'location_type': data['location_type']}
        desired = ambient_entity(location, data['game_id'])
        if not desired:
            continue
        try:
            existing_props = json.loads(data.get('entity_properties') or '{}')
        except (TypeError, ValueError):
            existing_props = {}
        properties = {**desired['properties'], **existing_props}
        conn.execute(
            "UPDATE entities SET profession=?,description=?,behaviors=?,dialogue_tags=?,properties=? "
            "WHERE game_id=? AND id=?",
            (desired['profession'], desired['description'], json.dumps(desired['behaviors']),
             json.dumps(desired['dialogue_tags']), json.dumps(properties), data['game_id'], data['entity_id']),
        )
