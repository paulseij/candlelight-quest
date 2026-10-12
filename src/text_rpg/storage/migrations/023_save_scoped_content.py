"""Keep authored identifiers independent in each saved game."""
import re
import sqlite3


def upgrade(conn: sqlite3.Connection) -> None:
    # No other table has a foreign key to these authored IDs. Preserve the
    # complete schema (including columns added by previous migrations).
    for table in ("entities", "locations", "regions", "quests", "shops"):
        ddl = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()[0]
        indexes = [row[0] for row in conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL", (table,))]
        ddl = re.sub(r"\bid\s+TEXT\s+PRIMARY\s+KEY\b", "id TEXT NOT NULL", ddl, count=1, flags=re.I)
        ddl = ddl[:ddl.rfind(")")] + ", PRIMARY KEY(game_id, id))"
        replacement = table + "_save_scoped"
        ddl = re.sub(r"(CREATE TABLE\s+(?:IF NOT EXISTS\s+)?)" + table + r"\b", lambda m: m[1] + replacement, ddl, count=1, flags=re.I)
        conn.execute(ddl)
        conn.execute(f'INSERT INTO "{replacement}" SELECT * FROM "{table}"')
        conn.execute(f'DROP TABLE "{table}"')
        conn.execute(f'ALTER TABLE "{replacement}" RENAME TO "{table}"')
        for index in indexes:
            conn.execute(index)
