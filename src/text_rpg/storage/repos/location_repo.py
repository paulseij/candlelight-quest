from __future__ import annotations

import json
from typing import Any

from text_rpg.storage.database import Database

_JSON_FIELDS = frozenset({
    "connections",
    "entities",
    "items",
    "properties",
})


def _serialize(data: dict) -> dict:
    """Return a copy with JSON fields serialized to strings."""
    out = dict(data)
    for field in _JSON_FIELDS:
        if field in out and out[field] is not None and not isinstance(out[field], str):
            out[field] = json.dumps(out[field])
    return out


def _deserialize(row: Any) -> dict | None:
    """Convert a sqlite3.Row to a dict with JSON fields parsed."""
    if row is None:
        return None
    result = dict(row)
    for field in _JSON_FIELDS:
        raw = result.get(field)
        if raw is not None and isinstance(raw, str):
            result[field] = json.loads(raw)
    return result


def _deserialize_many(rows: list) -> list[dict]:
    """Convert a list of sqlite3.Row objects to dicts."""
    return [_deserialize(r) for r in rows]


class LocationRepo:
    """Repository for location records."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def save(self, location_dict: dict) -> None:
        """Insert or update a location record (UPSERT)."""
        data = _serialize(location_dict)
        columns = ", ".join(data.keys())
        placeholders = ", ".join("?" for _ in data)
        updates = ", ".join(f"{k} = excluded.{k}" for k in data)
        sql = (
            f"INSERT INTO locations ({columns}) VALUES ({placeholders}) "
            f"ON CONFLICT(game_id, id) DO UPDATE SET {updates}"
        )
        with self.db.get_connection() as conn:
            conn.execute(sql, list(data.values()))

    def get(self, location_id: str, game_id: str) -> dict | None:
        """Fetch a location by id and game_id."""
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM locations WHERE id = ? AND game_id = ?",
                (location_id, game_id),
            ).fetchone()
        return _deserialize(row)

    def get_by_region(self, game_id: str, region_id: str) -> list[dict]:
        """Return all locations in a region for a given game."""
        with self.db.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM locations WHERE game_id = ? AND region_id = ?",
                (game_id, region_id),
            ).fetchall()
        return _deserialize_many(rows)

    def get_all(self, game_id: str) -> list[dict]:
        """Return all locations for a given game."""
        with self.db.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM locations WHERE game_id = ?",
                (game_id,),
            ).fetchall()
        return _deserialize_many(rows)

    def update_field(
        self, location_id: str, game_id: str, field: str, value: Any
    ) -> None:
        """Update a single field on a location."""
        if field in _JSON_FIELDS and value is not None and not isinstance(value, str):
            value = json.dumps(value)
        with self.db.get_connection() as conn:
            conn.execute(
                f"UPDATE locations SET {field} = ? WHERE id = ? AND game_id = ?",
                (value, location_id, game_id),
            )
