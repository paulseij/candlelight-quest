"""Idempotent extension for saves initialized during campaign development."""


def upgrade(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS location_conditions (
        game_id TEXT NOT NULL, location_id TEXT NOT NULL, condition TEXT NOT NULL,
        cause_event_id TEXT NOT NULL, PRIMARY KEY(game_id, location_id))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS world_event_checks (
        game_id TEXT NOT NULL, kind TEXT NOT NULL, day INTEGER NOT NULL,
        PRIMARY KEY(game_id, kind, day))""")
