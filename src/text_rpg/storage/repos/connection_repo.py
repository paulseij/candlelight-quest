"""Repository for the location_connections table."""
from __future__ import annotations

import uuid
from collections import deque
from typing import Any

from text_rpg.storage.database import Database


class ConnectionRepo:
    """CRUD operations on the location_connections table."""

    def __init__(self, db: Database) -> None:
        self.db = db

    # -- Read --

    def get_connections(self, game_id: str, location_id: str) -> list[dict]:
        """Return all outgoing connections from a location."""
        with self.db.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM location_connections "
                "WHERE game_id = ? AND source_location_id = ? ORDER BY rowid",
                (game_id, location_id),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_connections_to(self, game_id: str, target_location_id: str) -> list[dict]:
        """Return all incoming connections pointing at a location (reverse lookup)."""
        with self.db.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM location_connections "
                "WHERE game_id = ? AND target_location_id = ?",
                (game_id, target_location_id),
            ).fetchall()
        return [dict(r) for r in rows]

    def find_connection(
        self, game_id: str, source_id: str, direction: str,
    ) -> dict | None:
        """Find a specific connection by source + direction."""
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM location_connections "
                "WHERE game_id = ? AND source_location_id = ? AND direction = ?",
                (game_id, source_id, direction),
            ).fetchone()
        return dict(row) if row else None

    # -- Write --

    def add_connection(
        self,
        game_id: str,
        source_id: str,
        target_id: str,
        direction: str,
        description: str = "",
        is_locked: bool = False,
    ) -> None:
        """Add a path, skipping only an identical source, direction and destination."""
        with self.db.get_connection() as conn:
            existing = conn.execute(
                "SELECT 1 FROM location_connections "
                "WHERE game_id = ? AND source_location_id = ? AND direction = ? AND target_location_id = ?",
                (game_id, source_id, direction, target_id),
            ).fetchone()
            if existing:
                return
            conn.execute(
                "INSERT INTO location_connections "
                "(id, game_id, source_location_id, target_location_id, "
                "direction, description, is_locked) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    game_id,
                    source_id,
                    target_id,
                    direction,
                    description,
                    1 if is_locked else 0,
                ),
            )

    def add_bidirectional(
        self,
        game_id: str,
        source_id: str,
        target_id: str,
        direction: str,
        reverse_direction: str,
        description: str = "",
        back_description: str = "",
        is_locked: bool = False,
    ) -> None:
        """Add a connection in both directions atomically."""
        self.add_connection(game_id, source_id, target_id, direction, description, is_locked)
        self.add_connection(game_id, target_id, source_id, reverse_direction, back_description, is_locked)

    def remove_connection(self, game_id: str, source_id: str, direction: str) -> None:
        """Remove a specific connection."""
        with self.db.get_connection() as conn:
            conn.execute(
                "DELETE FROM location_connections "
                "WHERE game_id = ? AND source_location_id = ? AND direction = ?",
                (game_id, source_id, direction),
            )

    def set_locked(self, game_id: str, source_id: str, direction: str, locked: bool) -> None:
        """Lock or unlock a connection."""
        with self.db.get_connection() as conn:
            conn.execute(
                "UPDATE location_connections SET is_locked = ? "
                "WHERE game_id = ? AND source_location_id = ? AND direction = ?",
                (1 if locked else 0, game_id, source_id, direction),
            )

    # -- Graph queries --

    def get_nearby_graph(
        self, game_id: str, start_location_id: str, max_depth: int = 3,
    ) -> dict[str, list[dict]]:
        """BFS from start_location_id up to max_depth hops.

        Returns {location_id: [connection_dicts]} for all reachable locations
        within the depth limit.
        """
        graph: dict[str, list[dict]] = {}
        seen: set[str] = {start_location_id}
        queue: deque[tuple[str, int]] = deque([(start_location_id, 0)])

        while queue:
            loc_id, depth = queue.popleft()
            conns = self.get_connections(game_id, loc_id)
            graph[loc_id] = conns

            if depth < max_depth:
                for c in conns:
                    target = c.get("target_location_id", "")
                    if target and target not in seen:
                        seen.add(target)
                        queue.append((target, depth + 1))

        return graph

    def count_all(self, game_id: str) -> int:
        """Count total unique locations that appear in any connection."""
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT COUNT(DISTINCT source_location_id) + "
                "COUNT(DISTINCT target_location_id) "
                "FROM location_connections WHERE game_id = ?",
                (game_id,),
            ).fetchone()
        # This overcounts (locations appear in both columns), so use UNION approach
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM ("
                "  SELECT source_location_id AS lid FROM location_connections WHERE game_id = ? "
                "  UNION "
                "  SELECT target_location_id AS lid FROM location_connections WHERE game_id = ?"
                ")",
                (game_id, game_id),
            ).fetchone()
        return row[0] if row else 0
