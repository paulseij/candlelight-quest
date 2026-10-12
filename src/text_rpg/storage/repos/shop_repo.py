"""Repository for shop CRUD operations."""
from __future__ import annotations

import json
import uuid
from typing import Any

from text_rpg.storage.database import Database


class ShopRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def get_shop_by_location(self, game_id: str, location_id: str) -> list[dict]:
        """Get all shops at a location."""
        with self.db.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM shops WHERE game_id = ? AND location_id = ?",
                (game_id, location_id),
            ).fetchall()
        results = []
        for row in rows:
            shop = dict(row)
            stock = shop.get("stock", "[]")
            if isinstance(stock, str):
                shop["stock"] = json.loads(stock) if stock else []
            results.append(shop)
        return results

    def get_shop(self, shop_id: str, game_id: str | None = None) -> dict | None:
        """Get a single shop by ID."""
        game_id = self.db.resolve_game_scope("shops", shop_id, game_id)
        with self.db.get_connection() as conn:
            row = conn.execute("SELECT * FROM shops WHERE id = ? AND game_id = ?", (shop_id, game_id)).fetchone()
        if not row:
            return None
        shop = dict(row)
        stock = shop.get("stock", "[]")
        if isinstance(stock, str):
            shop["stock"] = json.loads(stock) if stock else []
        return shop

    def get_shop_by_owner(self, game_id: str, owner_entity_id: str) -> dict | None:
        """Get a shop by its owner NPC entity ID."""
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM shops WHERE game_id = ? AND owner_entity_id = ?",
                (game_id, owner_entity_id),
            ).fetchone()
        if not row:
            return None
        shop = dict(row)
        stock = shop.get("stock", "[]")
        if isinstance(stock, str):
            shop["stock"] = json.loads(stock) if stock else []
        return shop

    def save_shop(self, shop: dict) -> None:
        """Insert or update a shop."""
        with self.db.get_connection() as conn:
            stock = shop.get("stock", [])
            if not isinstance(stock, str):
                stock = json.dumps(stock)
            conn.execute(
                """INSERT OR REPLACE INTO shops
                   (id, game_id, owner_entity_id, location_id, shop_type, stock, gold_reserve, price_modifier, restock_turn)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    shop.get("id", str(uuid.uuid4())),
                    shop["game_id"],
                    shop["owner_entity_id"],
                    shop["location_id"],
                    shop.get("shop_type", "general"),
                    stock,
                    shop.get("gold_reserve", 500),
                    shop.get("price_modifier", 1.0),
                    shop.get("restock_turn", 0),
                ),
            )

    def update_stock(self, shop_id: str, stock: list[dict], game_id: str | None = None) -> None:
        """Update a shop's stock."""
        game_id = self.db.resolve_game_scope("shops", shop_id, game_id)
        with self.db.get_connection() as conn:
            conn.execute(
                "UPDATE shops SET stock = ? WHERE id = ? AND game_id = ?",
                (json.dumps(stock), shop_id, game_id),
            )

    def update_gold_reserve(self, shop_id: str, gold: int, game_id: str | None = None) -> None:
        """Update a shop's gold reserve."""
        game_id = self.db.resolve_game_scope("shops", shop_id, game_id)
        with self.db.get_connection() as conn:
            conn.execute(
                "UPDATE shops SET gold_reserve = ? WHERE id = ? AND game_id = ?",
                (gold, shop_id, game_id),
            )

    def update_price_modifier(self, shop_id: str, modifier: float, game_id: str | None = None) -> None:
        """Update a shop's price modifier."""
        game_id = self.db.resolve_game_scope("shops", shop_id, game_id)
        with self.db.get_connection() as conn:
            conn.execute(
                "UPDATE shops SET price_modifier = ? WHERE id = ? AND game_id = ?",
                (modifier, shop_id, game_id),
            )
