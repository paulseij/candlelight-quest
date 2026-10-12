"""Repository for player-invented items."""
from __future__ import annotations

import json
import uuid
from typing import Any

from text_rpg.storage.database import Database


class CustomItemRepo:
    """CRUD for the custom_items table."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def save_custom_item(self, item_data: dict[str, Any]) -> str:
        """Save a player-invented item. Returns the item id."""
        item_id = item_data.get("id", str(uuid.uuid4()))
        with self.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO custom_items "
                "(id, game_id, character_id, name, description, item_type, rarity, "
                "effects, damage_dice, ac_bonus, value, weight, plausibility, "
                "creation_dc, cursed_quirk, created_turn, location_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    item_id,
                    item_data["game_id"],
                    item_data["character_id"],
                    item_data["name"],
                    item_data["description"],
                    item_data.get("item_type", "misc"),
                    item_data.get("rarity", "common"),
                    json.dumps(item_data.get("effects", [])),
                    item_data.get("damage_dice"),
                    item_data.get("ac_bonus", 0),
                    item_data.get("value", 1),
                    item_data.get("weight", 1.0),
                    item_data.get("plausibility"),
                    item_data.get("creation_dc"),
                    item_data.get("cursed_quirk"),
                    item_data["created_turn"],
                    item_data.get("location_id"),
                ),
            )
        return item_id

    def get_custom_items(self, game_id: str, char_id: str) -> list[dict[str, Any]]:
        """Return all custom items invented by this character."""
        with self.db.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM custom_items WHERE game_id = ? AND character_id = ? "
                "ORDER BY created_turn",
                (game_id, char_id),
            ).fetchall()
        return [self._deserialize(r) for r in rows]

    def get_custom_item(self, item_id: str) -> dict[str, Any] | None:
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM custom_items WHERE id = ?", (item_id,),
            ).fetchone()
        return self._deserialize(row) if row else None

    def update_effects(self, item_id: str, effects: list[dict]) -> None:
        """Replace an item's effects (used by enchanting)."""
        with self.db.get_connection() as conn:
            conn.execute(
                "UPDATE custom_items SET effects = ? WHERE id = ?",
                (json.dumps(effects), item_id),
            )

    @staticmethod
    def _deserialize(row: Any) -> dict[str, Any]:
        result = dict(row)
        raw = result.get("effects")
        if isinstance(raw, str):
            result["effects"] = json.loads(raw)
        return result

    @staticmethod
    def to_catalog_entry(item: dict[str, Any]) -> dict[str, Any]:
        """Convert a custom_items row into the load_all_items() catalog shape."""
        entry: dict[str, Any] = {
            "id": item["id"],
            "name": item["name"],
            "description": item.get("description", ""),
            "item_type": item.get("item_type", "misc"),
            "rarity": item.get("rarity", "common"),
            "value": item.get("value", 1),
            "weight": item.get("weight", 1.0),
            "custom": True,
        }
        if item.get("damage_dice"):
            entry["damage_dice"] = item["damage_dice"]
        if item.get("ac_bonus"):
            entry["ac_bonus"] = item["ac_bonus"]
        for effect in item.get("effects", []):
            etype = effect.get("type")
            params = effect.get("params", {})
            if etype == "damage_dice" and "damage_dice" not in entry:
                entry["damage_dice"] = params.get("dice", "1d4")
            elif etype == "ac_bonus" and "ac_bonus" not in entry:
                entry["ac_bonus"] = params.get("amount", 1)
            elif etype == "consumable_heal":
                entry["consumable"] = True
                entry["heal_dice"] = params.get("dice", "2d4")
        if item.get("cursed_quirk"):
            entry["cursed_quirk"] = item["cursed_quirk"]
        return entry
