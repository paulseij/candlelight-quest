"""Persistent campaigns and branch-local causal history."""


def upgrade(conn):
    conn.execute("""CREATE TABLE campaign_instances (
        game_id TEXT NOT NULL, id TEXT NOT NULL, data TEXT NOT NULL,
        PRIMARY KEY (game_id, id))""")
    conn.execute("""CREATE TABLE world_history (
        game_id TEXT NOT NULL, id TEXT NOT NULL, subject_id TEXT,
        event_type TEXT NOT NULL, cause_id TEXT, world_time INTEGER NOT NULL,
        description TEXT NOT NULL, data TEXT NOT NULL,
        PRIMARY KEY (game_id, id))""")
    conn.execute("CREATE INDEX world_history_subject ON world_history(game_id, subject_id, world_time)")
    conn.execute("""CREATE TABLE location_conditions (
        game_id TEXT NOT NULL, location_id TEXT NOT NULL, condition TEXT NOT NULL,
        cause_event_id TEXT NOT NULL, PRIMARY KEY(game_id, location_id))""")
    conn.execute("""CREATE TABLE world_event_checks (
        game_id TEXT NOT NULL, kind TEXT NOT NULL, day INTEGER NOT NULL,
        PRIMARY KEY(game_id, kind, day))""")
