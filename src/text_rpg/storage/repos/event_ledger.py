from __future__ import annotations

import json
from typing import Any

from text_rpg.storage.database import Database


def _deserialize(row: Any) -> dict | None:
    """Convert a sqlite3.Row to a dict with the mechanical_details field parsed."""
    if row is None:
        return None
    result = dict(row)
    raw = result.get("mechanical_details")
    if raw is not None and isinstance(raw, str):
        result["mechanical_details"] = json.loads(raw)
    return result


def _deserialize_many(rows: list) -> list[dict]:
    """Convert a list of sqlite3.Row objects to dicts."""
    return [_deserialize(r) for r in rows]


class EventLedgerRepo:
    """Append-only repository for the event ledger."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def append(self, event_dict: dict) -> None:
        """Insert a new event. Events are immutable once written."""
        data = dict(event_dict)
        md = data.get("mechanical_details")
        if md is not None and not isinstance(md, str):
            data["mechanical_details"] = json.dumps(md)
        columns = ", ".join(data.keys())
        placeholders = ", ".join("?" for _ in data)
        sql = f"INSERT INTO events ({columns}) VALUES ({placeholders})"
        with self.db.get_connection() as conn:
            game = conn.execute("SELECT timeline_id FROM games WHERE id=?",(data["game_id"],)).fetchone()
            data.setdefault("timeline_id", game[0] if game else "prime")
            columns = ", ".join(data)
            placeholders = ", ".join("?" for _ in data)
            conn.execute(f"INSERT INTO events ({columns}) VALUES ({placeholders})", list(data.values()))
            conn.execute("INSERT OR IGNORE INTO index_outbox(event_id,game_id) VALUES(?,?)",(data["id"],data["game_id"]))

    def get_recent(self, game_id: str, limit: int = 20) -> list[dict]:
        """Return the most recent events for a game."""
        from text_rpg.storage.memory import lineage
        with self.db.get_connection() as conn:
            branches = lineage(conn,game_id)
            rows = conn.execute("SELECT rowid AS watermark,* FROM events WHERE game_id=? ORDER BY rowid DESC",(game_id,)).fetchall()
            rows = [row for row in rows if row["watermark"] <= branches.get(row["timeline_id"],-1)][:limit]
        return _deserialize_many(rows)

    def get_by_type(
        self, game_id: str, event_type: str, limit: int = 50
    ) -> list[dict]:
        """Return events of a given type for a game."""
        with self.db.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE game_id = ? AND event_type = ? "
                "ORDER BY timestamp DESC LIMIT ?",
                (game_id, event_type, limit),
            ).fetchall()
        return _deserialize_many(rows)

    def get_by_actor(
        self, game_id: str, actor_id: str, limit: int = 50
    ) -> list[dict]:
        """Return events involving a given actor."""
        with self.db.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE game_id = ? AND actor_id = ? "
                "ORDER BY timestamp DESC LIMIT ?",
                (game_id, actor_id, limit),
            ).fetchall()
        return _deserialize_many(rows)

    def get_by_location(
        self, game_id: str, location_id: str, limit: int = 50
    ) -> list[dict]:
        """Return events at a given location."""
        with self.db.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE game_id = ? AND location_id = ? "
                "ORDER BY timestamp DESC LIMIT ?",
                (game_id, location_id, limit),
            ).fetchall()
        return _deserialize_many(rows)

    def get_all(self, game_id: str) -> list[dict]:
        """Return every event for a game in chronological order."""
        with self.db.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE game_id = ? ORDER BY timestamp ASC",
                (game_id,),
            ).fetchall()
        return _deserialize_many(rows)

    def count(self, game_id: str) -> int:
        """Return the total number of events for a game."""
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM events WHERE game_id = ?",
                (game_id,),
            ).fetchone()
        return row["cnt"] if row else 0
