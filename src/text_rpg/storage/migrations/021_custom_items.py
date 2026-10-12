"""Migration 021: Player-invented items."""
from __future__ import annotations

import sqlite3


def upgrade(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS custom_items (
            id TEXT PRIMARY KEY,
            game_id TEXT NOT NULL REFERENCES games(id),
            character_id TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT NOT NULL,
            item_type TEXT NOT NULL DEFAULT 'misc',
            rarity TEXT NOT NULL DEFAULT 'common',
            effects TEXT NOT NULL DEFAULT '[]',
            damage_dice TEXT,
            ac_bonus INTEGER NOT NULL DEFAULT 0,
            value INTEGER NOT NULL DEFAULT 1,
            weight REAL NOT NULL DEFAULT 1.0,
            plausibility REAL,
            creation_dc INTEGER,
            cursed_quirk TEXT,
            created_turn INTEGER NOT NULL,
            location_id TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_custom_items_game
            ON custom_items(game_id, character_id);
    """)
