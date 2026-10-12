from __future__ import annotations

import json
from typing import Any

from text_rpg.storage.database import Database

_REGION_JSON = frozenset({"locations"})
_INVENTORY_JSON = frozenset({"items"})
_QUEST_JSON = frozenset({"objectives", "item_rewards"})
_COMBAT_JSON = frozenset({"combatants", "turn_order"})


def _serialize_fields(data: dict, json_fields: frozenset[str]) -> dict:
    out = dict(data)
    for field in json_fields:
        if field in out and out[field] is not None and not isinstance(out[field], str):
            out[field] = json.dumps(out[field])
    return out


def _deserialize_row(row: Any, json_fields: frozenset[str]) -> dict | None:
    if row is None:
        return None
    result = dict(row)
    for field in json_fields:
        raw = result.get(field)
        if raw is not None and isinstance(raw, str):
            result[field] = json.loads(raw)
    return result


def _upsert(conn: Any, table: str, data: dict) -> None:
    columns = ", ".join(data.keys())
    placeholders = ", ".join("?" for _ in data)
    updates = ", ".join(f"{k} = excluded.{k}" for k in data)
    conflict = "game_id, id" if table in {"regions", "quests"} else "id"
    sql = (
        f"INSERT INTO {table} ({columns}) VALUES ({placeholders}) "
        f"ON CONFLICT({conflict}) DO UPDATE SET {updates}"
    )
    conn.execute(sql, list(data.values()))


class WorldStateRepo:
    """Repository for regions, inventory, quests, and combat instances."""

    def __init__(self, db: Database) -> None:
        self.db = db

    # -- Regions --

    def save_region(self, region_dict: dict) -> None:
        data = _serialize_fields(region_dict, _REGION_JSON)
        with self.db.get_connection() as conn:
            _upsert(conn, "regions", data)

    def get_region(self, region_id: str, game_id: str) -> dict | None:
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM regions WHERE id = ? AND game_id = ?",
                (region_id, game_id),
            ).fetchone()
        return _deserialize_row(row, _REGION_JSON)

    # -- Inventory --

    def save_inventory(self, inventory_dict: dict) -> None:
        data = _serialize_fields(inventory_dict, _INVENTORY_JSON)
        with self.db.get_connection() as conn:
            _upsert(conn, "inventory", data)

    def get_inventory(self, owner_id: str, game_id: str) -> dict | None:
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM inventory WHERE owner_id = ? AND game_id = ?",
                (owner_id, game_id),
            ).fetchone()
        return _deserialize_row(row, _INVENTORY_JSON)

    def update_inventory(self, inventory_id: str, items: list[dict]) -> None:
        with self.db.get_connection() as conn:
            conn.execute(
                "UPDATE inventory SET items = ? WHERE id = ?",
                (json.dumps(items), inventory_id),
            )

    # -- Quests --

    def save_quest(self, quest_dict: dict) -> None:
        data = _serialize_fields(quest_dict, _QUEST_JSON)
        with self.db.get_connection() as conn:
            _upsert(conn, "quests", data)

    def get_quest(self, quest_id: str, game_id: str) -> dict | None:
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM quests WHERE id = ? AND game_id = ?",
                (quest_id, game_id),
            ).fetchone()
        return _deserialize_row(row, _QUEST_JSON)

    def get_active_quests(self, game_id: str) -> list[dict]:
        with self.db.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM quests WHERE game_id = ? AND status = ?",
                (game_id, "active"),
            ).fetchall()
        return [_deserialize_row(r, _QUEST_JSON) for r in rows]

    def get_all_quests(self, game_id: str) -> list[dict]:
        """Retrieve all quests for a game (active, completed, failed)."""
        with self.db.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM quests WHERE game_id = ? ORDER BY "
                "CASE status WHEN 'active' THEN 0 WHEN 'available' THEN 1 "
                "WHEN 'completed' THEN 2 WHEN 'failed' THEN 3 END",
                (game_id,),
            ).fetchall()
        return [_deserialize_row(r, _QUEST_JSON) for r in rows]

    def update_quest_status(
        self, quest_id: str, game_id: str, status: str
    ) -> None:
        with self.db.get_connection() as conn:
            conn.execute(
                "UPDATE quests SET status = ? WHERE id = ? AND game_id = ?",
                (status, quest_id, game_id),
            )

    # -- Combat --

    def save_combat(self, combat_dict: dict) -> None:
        data = _serialize_fields(combat_dict, _COMBAT_JSON)
        with self.db.get_connection() as conn:
            _upsert(conn, "combat_instances", data)

    def get_active_combat(self, game_id: str) -> dict | None:
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM combat_instances "
                "WHERE game_id = ? AND is_active = 1",
                (game_id,),
            ).fetchone()
        return _deserialize_row(row, _COMBAT_JSON)

    def update_combat(self, combat_id: str, updates: dict) -> None:
        data = _serialize_fields(updates, _COMBAT_JSON)
        set_clause = ", ".join(f"{k} = ?" for k in data)
        sql = f"UPDATE combat_instances SET {set_clause} WHERE id = ?"
        with self.db.get_connection() as conn:
            conn.execute(sql, list(data.values()) + [combat_id])

    # -- Story State --

    _STORY_JSON = frozenset({
        "resolved_variables", "activated_beats", "beat_turn_numbers", "quest_ids", "data",
    })

    def save_story_state(self, state_dict: dict) -> None:
        data = _serialize_fields(state_dict, self._STORY_JSON)
        with self.db.get_connection() as conn:
            _upsert(conn, "story_state", data)

    def get_active_stories(self, game_id: str) -> list[dict]:
        with self.db.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM story_state WHERE game_id = ? AND status = 'active'",
                (game_id,),
            ).fetchall()
        return [_deserialize_row(r, self._STORY_JSON) for r in rows]

    def get_story_state(self, game_id: str, seed_id: str) -> dict | None:
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM story_state WHERE game_id = ? AND seed_id = ?",
                (game_id, seed_id),
            ).fetchone()
        return _deserialize_row(row, self._STORY_JSON)

    def update_story_beat(
        self, game_id: str, seed_id: str, beat: str,
        activated_beats: list[str], quest_ids: list[str],
    ) -> None:
        with self.db.get_connection() as conn:
            conn.execute(
                "UPDATE story_state SET current_beat = ?, activated_beats = ?, "
                "quest_ids = ? WHERE game_id = ? AND seed_id = ?",
                (beat, json.dumps(activated_beats), json.dumps(quest_ids), game_id, seed_id),
            )

    def complete_story(self, game_id: str, seed_id: str, status: str) -> None:
        with self.db.get_connection() as conn:
            conn.execute(
                "UPDATE story_state SET status = ? WHERE game_id = ? AND seed_id = ?",
                (status, game_id, seed_id),
            )

    def get_completed_story_ids(self, game_id: str) -> list[str]:
        with self.db.get_connection() as conn:
            rows = conn.execute(
                "SELECT seed_id FROM story_state WHERE game_id = ? AND status IN ('completed', 'failed')",
                (game_id,),
            ).fetchall()
        return [r["seed_id"] for r in rows]

    # -- World Event Cooldowns --

    def get_event_cooldown(self, game_id: str, event_id: str) -> int:
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT last_triggered_turn FROM world_event_cooldowns "
                "WHERE game_id = ? AND event_id = ?",
                (game_id, event_id),
            ).fetchone()
        return row["last_triggered_turn"] if row else 0

    def set_event_cooldown(self, game_id: str, event_id: str, turn: int) -> None:
        with self.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO world_event_cooldowns (id, game_id, event_id, last_triggered_turn) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(game_id, event_id) DO UPDATE SET last_triggered_turn = ?",
                (f"{game_id}_{event_id}", game_id, event_id, turn, turn),
            )
