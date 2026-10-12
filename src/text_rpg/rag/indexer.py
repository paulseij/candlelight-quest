from __future__ import annotations

import uuid
import logging
import threading
import time
from typing import Any

from text_rpg.rag.vector_store import VectorStore
from text_rpg.rag.embeddings import EmbeddingUnavailableError, OllamaEmbeddings

logger = logging.getLogger(__name__)


class Indexer:
    """Indexes game events, lore, and content into the vector store."""

    def __init__(self, vector_store: VectorStore, embeddings: OllamaEmbeddings, db=None) -> None:
        self.db = db
        self.store = vector_store
        self.embeddings = embeddings
        self._available: bool | None = None
        self._checked_at = 0.0
        self._worker_lock = threading.Lock()

    @property
    def is_available(self) -> bool:
        """Lazy-check whether the embedding backend is reachable."""
        if self._available is None or time.monotonic()-self._checked_at > 30:
            self._available = self.embeddings.is_available()
            self._checked_at = time.monotonic()
        return self._available

    def _safe_embed(self, text: str) -> list[float] | None:
        """Embed text, or None when the backend is down (write is skipped)."""
        try:
            return self.embeddings.embed(text)
        except EmbeddingUnavailableError as exc:
            logger.warning("Skipping RAG write, embedding backend down: %s", exc)
            return None

    def drain_outbox(self, db, game_id, limit=8):
        """Replay durable event IDs. Caller owns this worker's DB connection."""
        if not self.is_available:
            return 0
        with db.get_connection() as conn:
            rows = conn.execute("SELECT e.* FROM events e JOIN index_outbox o ON e.id=o.event_id WHERE o.game_id=? AND o.status='pending' ORDER BY e.rowid LIMIT ?",(game_id,limit)).fetchall()
        completed=0
        for row in rows:
            event=dict(row)
            ok=self.index_event(game_id,event['event_type'],event['description'] or '',location_id=event.get('location_id'),actor_id=event.get('actor_id'),turn_number=event['turn_number'],event_id=event['id'],target_id=event.get('target_id'),timeline_id=event['timeline_id'])
            with db.get_connection() as conn:
                conn.execute("UPDATE index_outbox SET attempts=attempts+1,status=? WHERE event_id=?",('done' if ok else 'pending',event['id']))
            completed += bool(ok)
        return completed

    def schedule_pending(self, db_path, game_id):
        # One daemon worker with a separate SQLite connection. No game state
        # mutation and no lost event if the process closes before indexing.
        if not self._worker_lock.acquire(blocking=False):
            return
        def run():
            from text_rpg.storage.database import Database
            db=Database(db_path)
            try:
                self.drain_outbox(db,game_id)
            except Exception:
                logger.exception("Index maintenance deferred; durable outbox retained")
            finally:
                db.close()
                self._worker_lock.release()
        threading.Thread(target=run,name='rpg-index-outbox',daemon=True).start()

    # ------------------------------------------------------------------
    # Event indexing
    # ------------------------------------------------------------------

    def index_event(
        self,
        game_id: str,
        event_type: str,
        description: str,
        location_id: str | None = None,
        actor_id: str | None = None,
        turn_number: int = 0,
        event_id: str | None = None,
        target_id: str | None = None,
        timeline_id: str = "prime",
    ) -> None:
        """Index a game event for later retrieval."""
        if not self.is_available:
            return
        doc_id = event_id or str(uuid.uuid5(uuid.NAMESPACE_URL, f"{game_id}:{timeline_id}:{turn_number}:{event_type}:{actor_id}:{target_id}:{description}"))
        embedding = self._safe_embed(description)
        if embedding is None:
            return
        metadata: dict[str, Any] = {
            "game_id": game_id,
            "event_type": event_type,
            "turn_number": turn_number,
            "doc_type": "event",
            "timeline_id":timeline_id,
            "event_id":doc_id,
        }
        if location_id:
            metadata["location_id"] = location_id
        if actor_id:
            metadata["actor_id"] = actor_id
        if target_id:
            metadata["target_id"] = target_id
        self.store.add_documents(
            "events", [description], [metadata], [doc_id], [embedding]
        )
        return True

    # ------------------------------------------------------------------
    # Lore indexing
    # ------------------------------------------------------------------

    def index_lore(
        self,
        content: str,
        category: str,
        tags: dict[str, str] | None = None,
    ) -> None:
        """Index world lore or generated content."""
        if getattr(self.db,"_staging",False) or not self.is_available:
            return
        doc_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"lore:{category}:{tags}:{content}"))
        embedding = self._safe_embed(content)
        if embedding is None:
            return
        metadata: dict[str, Any] = {"category": category, "doc_type": "lore"}
        if tags:
            metadata.update(tags)
        self.store.add_documents(
            "game_lore", [content], [metadata], [doc_id], [embedding]
        )

    # ------------------------------------------------------------------
    # NPC fact indexing
    # ------------------------------------------------------------------

    def index_npc_fact(
        self,
        game_id: str,
        npc_id: str,
        npc_name: str,
        fact: str,
    ) -> None:
        """Index a fact about an NPC."""
        if getattr(self.db,"_staging",False) or not self.is_available:
            return
        doc_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"npc:{game_id}:{npc_id}:{fact}"))
        text = f"{npc_name}: {fact}"
        embedding = self._safe_embed(text)
        if embedding is None:
            return
        metadata: dict[str, Any] = {
            "game_id": game_id,
            "npc_id": npc_id,
            "npc_name": npc_name,
            "doc_type": "npc_fact",
        }
        self.store.add_documents(
            "game_lore", [text], [metadata], [doc_id], [embedding]
        )

    # ------------------------------------------------------------------
    # Bulk / seed data
    # ------------------------------------------------------------------

    def index_seed_data(self, documents: list[dict[str, Any]]) -> None:
        """Bulk index SRD seed data.

        Each document dict must contain at least *content*.  Optional keys:
        *category* (defaults to ``"general"``) and *id* (auto-generated when
        absent).
        """
        if not self.is_available:
            logger.info("Embedding backend not available, skipping seed data indexing.")
            return
        texts = [d["content"] for d in documents]
        ids = [d.get("id", str(uuid.uuid4())) for d in documents]
        metadatas: list[dict[str, Any]] = [
            {"category": d.get("category", "general"), "doc_type": "srd"}
            for d in documents
        ]
        try:
            embeddings = self.embeddings.embed_batch(texts)
        except EmbeddingUnavailableError as exc:
            logger.warning("Skipping seed data indexing, embedding backend down: %s", exc)
            return
        self.store.add_documents("srd_reference", texts, metadatas, ids, embeddings)
        logger.info("Indexed %d seed documents.", len(documents))

    # ------------------------------------------------------------------
    # Reindex from the source of truth
    # ------------------------------------------------------------------

    def reindex_from_db(self, repos: dict[str, Any], game_id: str) -> int:
        """Rebuild this game's vectors by replaying the events table and world rows.

        The SQLite ledger is the source of truth, so a provider/model switch
        (which changes vector dimensions and collection names) can always be
        recovered by reindexing. Returns the number of documents indexed.
        """
        if not self.is_available:
            logger.warning("Cannot reindex: embedding backend unavailable.")
            return 0

        count = 0

        for event in repos["event_ledger"].get_all(game_id):
            description = event.get("description", "")
            if not description:
                continue
            self.index_event(
                game_id,
                event.get("event_type", "CUSTOM"),
                description,
                location_id=event.get("location_id"),
                actor_id=event.get("actor_id"),
                turn_number=event.get("turn_number", 0),
                event_id=event.get("id"), target_id=event.get("target_id"), timeline_id=event.get("timeline_id","prime"),
            )
            count += 1

        for loc in repos["location"].get_all(game_id):
            if loc.get("visited"):
                self.index_lore(
                    f"Location discovered: {loc.get('name', '')} — {loc.get('description', '')}",
                    category="location",
                    tags={"game_id": game_id, "location_id": loc.get("id", "")},
                )
                count += 1

        for quest in repos["world_state"].get_all_quests(game_id):
            self.index_lore(
                f"Quest: {quest.get('name', '')} — {quest.get('description', '')}",
                category="quest",
                tags={"game_id": game_id, "quest_id": quest.get("id", "")},
            )
            count += 1

        for npc in repos["entity"].get_by_game(game_id):
            if npc.get("entity_type") == "npc":
                self.index_npc_fact(
                    game_id, npc.get("id", ""), npc.get("name", ""),
                    npc.get("description", ""),
                )
                count += 1

        logger.info("Reindexed %d documents for game %s.", count, game_id)
        return count
