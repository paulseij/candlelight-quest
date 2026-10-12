"""Regression tests for snapshot restore (rewind)."""
from __future__ import annotations

from text_rpg.engine.snapshots import StateSerializer


class FakeWorldStateRepo:
    def __init__(self):
        self.status_updates = []
        self._quests = {
            "q1": {"id": "q1", "status": "active"},
        }

    def get_quest(self, quest_id, game_id):
        return self._quests.get(quest_id)

    def update_quest_status(self, quest_id, game_id, status):
        self.status_updates.append((quest_id, game_id, status))


def test_restore_quests_uses_update_quest_status():
    ws = FakeWorldStateRepo()
    serializer = StateSerializer({"world_state": ws})
    serializer._restore_quests("game1", {"quests": [
        {"id": "q1", "status": "available"},
        {"id": "missing", "status": "active"},
    ]})
    assert ws.status_updates == [("q1", "game1", "available")]
