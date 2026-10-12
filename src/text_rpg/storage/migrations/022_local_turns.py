"""Durable turn receipts, conversation memory, branch ancestry and index outbox."""
import sqlite3


def upgrade(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE turn_receipts (
            id TEXT PRIMARY KEY, game_id TEXT NOT NULL, timeline_id TEXT NOT NULL,
            turn_number INTEGER NOT NULL, input TEXT NOT NULL, result TEXT NOT NULL,
            presentation TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE utterances (
            id TEXT PRIMARY KEY, game_id TEXT NOT NULL, timeline_id TEXT NOT NULL,
            event_watermark INTEGER NOT NULL, npc_id TEXT NOT NULL, speaker_id TEXT NOT NULL,
            text TEXT NOT NULL, turn_id TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX utterance_scope ON utterances(game_id, npc_id, timeline_id);
        CREATE VIRTUAL TABLE utterance_search USING fts5(text, content='utterances', content_rowid='rowid');
        CREATE TRIGGER utterance_search_insert AFTER INSERT ON utterances BEGIN
            INSERT INTO utterance_search(rowid,text) VALUES(new.rowid,new.text);
        END;
        CREATE TABLE commitments (
            id TEXT PRIMARY KEY, game_id TEXT NOT NULL, timeline_id TEXT NOT NULL,
            event_watermark INTEGER NOT NULL, npc_id TEXT NOT NULL, source_id TEXT NOT NULL,
            text TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active', subject_id TEXT
        );
        CREATE TABLE timelines (
            game_id TEXT NOT NULL, id TEXT NOT NULL, parent_id TEXT,
            fork_watermark INTEGER NOT NULL DEFAULT 0, snapshot_id TEXT,
            PRIMARY KEY(game_id,id)
        );
        CREATE TABLE index_outbox (
            event_id TEXT PRIMARY KEY, game_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE scene_objects (
            id TEXT NOT NULL, game_id TEXT NOT NULL, location_id TEXT NOT NULL,
            name TEXT NOT NULL, state TEXT NOT NULL, PRIMARY KEY(game_id,id)
        );
    """)
