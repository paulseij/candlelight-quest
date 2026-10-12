"""Allow several destinations per direction and recover previously dropped branches."""
import json
import uuid


def upgrade(conn):
    conn.execute('''CREATE TABLE branching_connections (
        id TEXT PRIMARY KEY,
        game_id TEXT NOT NULL,
        source_location_id TEXT NOT NULL,
        target_location_id TEXT NOT NULL,
        direction TEXT NOT NULL,
        description TEXT DEFAULT '',
        is_locked BOOLEAN DEFAULT 0,
        UNIQUE(game_id, source_location_id, direction, target_location_id)
    )''')
    conn.execute('INSERT INTO branching_connections SELECT * FROM location_connections ORDER BY rowid')
    conn.execute('DROP TABLE location_connections')
    conn.execute('ALTER TABLE branching_connections RENAME TO location_connections')
    conn.execute('CREATE INDEX idx_conn_source ON location_connections(game_id, source_location_id)')
    conn.execute('CREATE INDEX idx_conn_target ON location_connections(game_id, target_location_id)')

    for game_id, location_id, raw in conn.execute('SELECT game_id,id,connections FROM locations').fetchall():
        try:
            paths = json.loads(raw or '[]')
        except (ValueError, TypeError):
            continue
        if not isinstance(paths, list):
            continue
        for path in paths:
            if not isinstance(path, dict):
                continue
            direction = path.get('direction')
            target = path.get('target_location_id')
            if not direction or not target:
                continue
            # Recover only branches of an existing direction: do not reopen
            # a direction that gameplay deliberately removed altogether.
            if not conn.execute('SELECT 1 FROM location_connections WHERE game_id=? AND source_location_id=? AND direction=?',
                                (game_id, location_id, direction)).fetchone():
                continue
            conn.execute('''INSERT OR IGNORE INTO location_connections
                (id,game_id,source_location_id,target_location_id,direction,description,is_locked)
                VALUES (?,?,?,?,?,?,?)''',
                (str(uuid.uuid4()), game_id, location_id, target, direction,
                 path.get('description', ''), bool(path.get('is_locked'))))
