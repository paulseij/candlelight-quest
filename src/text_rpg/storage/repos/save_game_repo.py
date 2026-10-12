from __future__ import annotations

from datetime import datetime, timezone

from text_rpg.storage.database import Database


class SaveGameRepo:
    """Repository for game save records."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def create_game(
        self,
        game_id: str,
        name: str,
        character_id: str,
        starting_location_id: str,
    ) -> None:
        """Create a new game record."""
        now = datetime.now(timezone.utc).isoformat()
        with self.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO games "
                "(id, name, created_at, turn_number, current_location_id, "
                "character_id, is_active) "
                "VALUES (?, ?, ?, 0, ?, ?, 1)",
                (game_id, name, now, starting_location_id, character_id),
            )

    def get_game(self, game_id: str) -> dict | None:
        """Fetch a game record by id."""
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM games WHERE id = ?", (game_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_games(self) -> list[dict]:
        """Return all game records ordered by creation date descending."""
        with self.db.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM games ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def update_turn(self, game_id: str, turn_number: int) -> None:
        """Update the current turn number for a game."""
        with self.db.get_connection() as conn:
            conn.execute(
                "UPDATE games SET turn_number = ? WHERE id = ?",
                (turn_number, game_id),
            )

    def update_location(self, game_id: str, location_id: str) -> None:
        """Update the current location for a game."""
        with self.db.get_connection() as conn:
            conn.execute(
                "UPDATE games SET current_location_id = ? WHERE id = ?",
                (location_id, game_id),
            )

    def update_world_time(self, game_id: str, world_time: int) -> None:
        """Update the world clock time for a game."""
        with self.db.get_connection() as conn:
            conn.execute(
                "UPDATE games SET world_time = ? WHERE id = ?",
                (world_time, game_id),
            )

    def delete_game(self, game_id: str) -> None:
        """Delete a game and all related records."""
        with self.db.get_connection() as conn:
            # Tables added in later migrations (order: leaf tables first)
            for table in (
                "discovered_combinations",
                "custom_spells",
                "character_traits",
                "behavior_events",
                "snapshots",
                "location_connections",
                "companions",
                "housing",
                "shops",
                "faction_reputation",
                "npc_reputation",
                "bounties",
                "trade_skills",
                "known_recipes",
                "known_spells",
                "prepared_spells",
                "story_state",
                "world_event_cooldowns",
                "revival_offers",
            ):
                conn.execute(f"DELETE FROM {table} WHERE game_id = ?", (game_id,))

            # Original tables
            conn.execute("DELETE FROM intents WHERE game_id = ?", (game_id,))
            conn.execute("DELETE FROM canon_entries WHERE game_id = ?", (game_id,))
            # Temporarily disable event immutability triggers for cleanup
            conn.execute("DROP TRIGGER IF EXISTS prevent_event_delete")
            conn.execute("DELETE FROM events WHERE game_id = ?", (game_id,))
            conn.execute(
                "CREATE TRIGGER IF NOT EXISTS prevent_event_delete "
                "BEFORE DELETE ON events BEGIN "
                "SELECT RAISE(ABORT, \"Events are immutable\"); END"
            )
            conn.execute("DELETE FROM combat_instances WHERE game_id = ?", (game_id,))
            conn.execute("DELETE FROM quests WHERE game_id = ?", (game_id,))
            conn.execute("DELETE FROM inventory WHERE game_id = ?", (game_id,))
            conn.execute("DELETE FROM entities WHERE game_id = ?", (game_id,))
            conn.execute("DELETE FROM locations WHERE game_id = ?", (game_id,))
            conn.execute("DELETE FROM regions WHERE game_id = ?", (game_id,))
            conn.execute("DELETE FROM characters WHERE game_id = ?", (game_id,))
            conn.execute("DELETE FROM games WHERE id = ?", (game_id,))
