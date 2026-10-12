from __future__ import annotations

import logging
import time
from typing import Any

from text_rpg.rag.vector_store import VectorStore
from text_rpg.rag.embeddings import OllamaEmbeddings

logger = logging.getLogger(__name__)


class RetrievalResult:
    """A single result from a vector-store query."""

    def __init__(self, text: str, metadata: dict[str, Any], distance: float) -> None:
        self.text = text
        self.metadata = metadata
        self.distance = distance

    def __repr__(self) -> str:
        return f"RetrievalResult(distance={self.distance:.3f}, text={self.text[:60]}...)"


class Retriever:
    """Retrieves relevant context from the vector store."""

    def __init__(
        self,
        vector_store: VectorStore,
        embeddings: OllamaEmbeddings,
        top_k: int = 5,
        db_path: str | None = None,
    ) -> None:
        self.store = vector_store
        self.embeddings = embeddings
        self.top_k = top_k
        self.db_path = db_path
        self._available: bool | None = None
        self._checked_at=0.0
        self._embedding_cache = {}

    @property
    def is_available(self) -> bool:
        """Lazy-check whether Ollama is reachable."""
        if self._available is None or time.monotonic()-self._checked_at>30:
            self._available = self.embeddings.is_available()
            self._checked_at=time.monotonic()
        return self._available

    # ------------------------------------------------------------------
    # High-level retrieval methods
    # ------------------------------------------------------------------

    def retrieve_relevant_lore(
        self, query: str, top_k: int | None = None, game_id: str | None = None
    ) -> list[RetrievalResult]:
        """Find lore relevant to the current scene."""
        if self.db_path and game_id:
            from text_rpg.storage.database import Database
            db=Database(self.db_path)
            try:
                with db.get_connection() as conn:
                    rows=conn.execute("SELECT name,description FROM locations WHERE game_id=? AND visited=1 UNION ALL SELECT name,description FROM quests WHERE game_id=? AND status='active'",(game_id,game_id)).fetchall()
                words={w.lower().strip('.,?!') for w in query.split() if len(w)>3}
                candidates=[f"{r[0]}: {r[1]}" for r in rows]
                candidates.sort(key=lambda text:sum(w in text.lower() for w in words),reverse=True)
                return [RetrievalResult(text,{"game_id":game_id},0) for text in candidates[:top_k or self.top_k] if any(w in text.lower() for w in words)]
            finally:
                db.close()
        if not self.is_available:
            return []
        return self._query_collection("game_lore", query, top_k or self.top_k, where={"game_id":game_id}) if game_id else []

    def retrieve_relevant_events(
        self, query: str, game_id: str, top_k: int | None = None
    ) -> list[RetrievalResult]:
        """Find past events relevant to the current context."""
        if self.db_path:
            from text_rpg.storage.database import Database
            from text_rpg.storage.repos.event_ledger import EventLedgerRepo
            db=Database(self.db_path)
            try:
                rows=EventLedgerRepo(db).get_recent(game_id,limit=200)
                words={w.lower().strip('.,?!') for w in query.split() if len(w)>3}
                rows.sort(key=lambda r:sum(w in (r.get('description') or '').lower() for w in words),reverse=True)
                return [RetrievalResult(r.get('description') or '',r,0) for r in rows[:top_k or self.top_k]]
            finally:
                db.close()
        if not self.is_available:
            return []
        return self._query_collection(
            "events", query, top_k or self.top_k, where={"game_id": game_id}
        )

    def retrieve_srd_reference(
        self, query: str, top_k: int | None = None
    ) -> list[RetrievalResult]:
        """Find SRD reference material."""
        if not self.is_available:
            return []
        return self._query_collection("srd_reference", query, top_k or self.top_k)

    def retrieve_npc_history(
        self, npc_id: str, game_id: str, top_k: int | None = None
    ) -> list[RetrievalResult]:
        """Retrieve only witnessed speech and accepted commitments for this NPC."""
        if self.db_path:
            from text_rpg.storage.database import Database
            from text_rpg.storage.memory import MemoryStore
            db=Database(self.db_path)
            try:
                memory=MemoryStore(db)
                return [RetrievalResult(r['text'],r,0) for r in memory.conversation(game_id,npc_id,limit=top_k or self.top_k)+memory.commitments(game_id,npc_id)]
            finally:
                db.close()
        if not self.is_available:
            return []
        results: list[RetrievalResult] = []
        # NPC facts from the lore collection
        lore_results = self._query_collection(
            "game_lore",
            f"NPC {npc_id}",
            top_k or self.top_k,
            where={"$and": [{"game_id":game_id},{"npc_id": npc_id}]},
        )
        results.extend(lore_results)
        # Events involving this NPC
        event_results = self._query_collection(
            "events",
            f"NPC {npc_id}",
            top_k or self.top_k,
            where={"$and": [{"game_id":game_id},{"$or":[{"actor_id": npc_id},{"target_id":npc_id}]}]},
        )
        results.extend(event_results)
        return results

    # ------------------------------------------------------------------
    # Context builder (convenience for LLM prompt assembly)
    # ------------------------------------------------------------------

    def build_context(
        self,
        scene_description: str,
        game_id: str,
        location_id: str | None = None,
    ) -> dict[str, list[str]]:
        """Build a complete context packet for the LLM.

        Returns a dict with keys ``relevant_lore``, ``past_events``, and
        ``srd_reference``, each mapping to a list of text snippets.
        """
        context: dict[str, list[str]] = {
            "relevant_lore": [],
            "past_events": [],
            "srd_reference": [],
        }
        if not self.db_path and not self.is_available:
            return context

        # Relevant lore
        lore = self.retrieve_relevant_lore(scene_description, top_k=3, game_id=game_id)
        context["relevant_lore"] = [r.text for r in lore]

        # Relevant past events
        events = self.retrieve_relevant_events(scene_description, game_id, top_k=5)
        context["past_events"] = [r.text for r in events]

        # SRD reference
        srd = self.retrieve_srd_reference(scene_description, top_k=2)
        context["srd_reference"] = [r.text for r in srd]

        return context

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _query_collection(
        self,
        collection_name: str,
        query: str,
        top_k: int,
        where: dict[str, Any] | None = None,
    ) -> list[RetrievalResult]:
        """Query a specific collection and return typed results."""
        try:
            if self.store.count(collection_name) == 0:
                return []
            embedding = self._embedding_cache.get(query)
            if embedding is None:
                embedding = self.embeddings.embed(query)
                self._embedding_cache = {query:embedding}
            results = self.store.query(
                collection_name, [embedding], n_results=top_k, where=where
            )
            output: list[RetrievalResult] = []
            docs = results.get("documents", [[]])[0]
            metas = results.get("metadatas", [[]])[0]
            dists = results.get("distances", [[]])[0]
            for doc, meta, dist in zip(docs, metas, dists):
                output.append(RetrievalResult(doc, meta, dist))
            return output
        except Exception as exc:
            logger.warning("RAG query failed for %s: %s", collection_name, exc)
            return []
