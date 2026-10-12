from __future__ import annotations

import json
from typing import Any

from text_rpg.storage.database import Database

_JSON_FIELDS = frozenset({
    "ability_scores",
    "attacks",
    "behaviors",
    "dialogue_tags",
    "loot_table",
    "properties",
    "schedule",
    "unavailable_periods",
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


class EntityRepo:
    """Repository for NPC and creature entity records."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def save(self, entity_dict: dict) -> None:
        """Insert or update an entity record (UPSERT)."""
        data = _serialize(entity_dict)
        columns = ", ".join(data.keys())
        placeholders = ", ".join("?" for _ in data)
        updates = ", ".join(f"{k} = excluded.{k}" for k in data)
        sql = (
            f"INSERT INTO entities ({columns}) VALUES ({placeholders}) "
            f"ON CONFLICT(game_id, id) DO UPDATE SET {updates}"
        )
        with self.db.get_connection() as conn:
            old = conn.execute('SELECT is_alive FROM entities WHERE game_id=? AND id=?',
                               (data.get('game_id'), data.get('id'))).fetchone()
            if old and not old[0] and data.get('is_alive'):
                raise ValueError('A dead entity requires an explicit revival event')
            conn.execute(sql, list(data.values()))

    def get(self, entity_id: str, game_id: str | None = None) -> dict | None:
        """Fetch an entity by id."""
        game_id = self.db.resolve_game_scope("entities", entity_id, game_id)
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM entities WHERE id = ? AND game_id = ?", (entity_id, game_id)
            ).fetchone()
        return _deserialize(row)

    def get_by_location(self, game_id: str, location_id: str) -> list[dict]:
        """Return all entities at a given location in a game."""
        with self.db.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM entities WHERE game_id = ? AND location_id = ?",
                (game_id, location_id),
            ).fetchall()
        return _deserialize_many(rows)

    def get_by_game(self, game_id: str) -> list[dict]:
        """Return all entities in a game."""
        with self.db.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM entities WHERE game_id = ?", (game_id,)
            ).fetchall()
        return _deserialize_many(rows)

    def update_field(self, entity_id: str, field: str, value: Any, game_id: str | None = None) -> None:
        """Update a single field on an entity."""
        game_id = self.db.resolve_game_scope("entities", entity_id, game_id)
        if field in _JSON_FIELDS and value is not None and not isinstance(value, str):
            value = json.dumps(value)
        with self.db.get_connection() as conn:
            if field == 'is_alive' and value:
                old = conn.execute('SELECT is_alive FROM entities WHERE game_id=? AND id=?', (game_id, entity_id)).fetchone()
                if old and not old[0]:
                    raise ValueError('A dead entity requires an explicit revival event')
            conn.execute(
                f"UPDATE entities SET {field} = ? WHERE id = ? AND game_id = ?",
                (value, entity_id, game_id),
            )

    def revive(self, entity_id: str, game_id: str, *, hp: int, cause_event_id: str) -> None:
        """For a validated revival mechanic only; ordinary saves cannot resurrect.

        The mechanic must first record its REVIVAL event (and pay its costs) in
        the same transaction. No player command currently grants free revival.
        """
        with self.db.get_connection() as conn:
            event = conn.execute("SELECT 1 FROM events WHERE game_id=? AND id=? AND target_id=? AND event_type='REVIVAL'",
                                 (game_id, cause_event_id, entity_id)).fetchone()
            entity = self.get(entity_id, game_id)
            if not event or not entity or not isinstance(hp, int) or isinstance(hp, bool) or not 0 < hp <= entity['hp_max']:
                raise ValueError('Revival requires a matching canonical event and valid restored HP')
            conn.execute('UPDATE entities SET is_alive=1,hp_current=? WHERE game_id=? AND id=?', (hp, game_id, entity_id))

    def delete(self, entity_id: str, game_id: str | None = None) -> None:
        """Delete an entity by id."""
        game_id = self.db.resolve_game_scope("entities", entity_id, game_id)
        with self.db.get_connection() as conn:
            conn.execute("DELETE FROM entities WHERE id = ? AND game_id = ?", (entity_id, game_id))
