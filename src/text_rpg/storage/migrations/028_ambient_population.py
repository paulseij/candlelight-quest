"""Give existing saves deterministic minor inhabitants in populated places."""
from __future__ import annotations

import json

from text_rpg.content.ambient_population import ambient_entity


def upgrade(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(entities)").fetchall()}
    for location in conn.execute("SELECT * FROM locations").fetchall():
        condition = conn.execute("SELECT condition FROM location_conditions WHERE game_id=? AND location_id=?",
                                 (location["game_id"], location["id"])).fetchone()
        if condition and condition[0] == 'destroyed':
            continue
        entity = ambient_entity(dict(location), location["game_id"])
        if not entity:
            continue
        if conn.execute("SELECT 1 FROM entities WHERE game_id=? AND id=?",
                        (location["game_id"], entity["id"])).fetchone():
            continue
        data = {key: value for key, value in entity.items() if key in columns}
        for key in ("ability_scores", "attacks", "behaviors", "dialogue_tags", "loot_table",
                    "properties", "schedule", "unavailable_periods"):
            if key in data and data[key] is not None and not isinstance(data[key], str):
                data[key] = json.dumps(data[key])
        names = ",".join(data)
        placeholders = ",".join("?" for _ in data)
        conn.execute(f"INSERT INTO entities ({names}) VALUES ({placeholders})", tuple(data.values()))
