"""Tests for engine/quest_tracker.py with fake repos."""
from __future__ import annotations

from dataclasses import dataclass, field

from text_rpg.engine.quest_tracker import QuestTracker


@dataclass
class FakeContext:
    character: dict = field(default_factory=lambda: {"id": "char1", "gold": 10})


class FakeWorldState:
    def __init__(self, quests):
        self.quests = {q["id"]: q for q in quests}
        self.status_updates = []
        self.saved = []

    def get_active_quests(self, game_id):
        return [q for q in self.quests.values() if q["status"] == "active"]

    def get_all_quests(self, game_id):
        return list(self.quests.values())

    def save_quest(self, quest):
        self.saved.append(quest["id"])
        self.quests[quest["id"]] = quest

    def update_quest_status(self, quest_id, game_id, status):
        self.status_updates.append((quest_id, status))
        self.quests[quest_id]["status"] = status


class FakeEntities:
    def __init__(self, entities):
        self.entities = {e["id"]: e for e in entities}

    def get(self, entity_id):
        return self.entities.get(entity_id)


class FakeCharacters:
    def __init__(self, char):
        self.char = char

    def get(self, char_id):
        return self.char


def _quest():
    return {
        "id": "wolves", "name": "Wolves", "quest_giver_id": "old_tom",
        "status": "active", "xp_reward": 100, "gold_reward": 25,
        "item_rewards": ["healing_potion"],
        "objectives": [
            {"id": "kill", "description": "Kill the wolf",
             "objective_type": "kill", "target_entity_type": "wolf",
             "target_count": 1, "current_count": 0, "is_complete": False},
        ],
    }


def _tracker(quests, entities=()):
    repos = {
        "world_state": FakeWorldState(quests),
        "entity": FakeEntities(list(entities)),
        "character": FakeCharacters({"id": "char1", "gold": 10}),
    }
    return QuestTracker(repos), repos


def test_death_completes_quest_and_pays_rewards():
    tracker, repos = _tracker(
        [_quest()],
        [{"id": "wolf_1", "name": "Wolf", "entity_type": "wolf", "is_alive": False}],
    )
    events = [{"event_type": "DEATH", "target_id": "wolf_1"}]
    quest_events, xp, mutations = tracker.process(events, FakeContext(), "g1")

    types = [e["event_type"] for e in quest_events]
    assert types.count("QUEST_COMPLETE") == 1
    assert "QUEST_UPDATE" in types
    assert xp == 100
    assert ("wolves", "completed") in repos["world_state"].status_updates

    gold = next(m for m in mutations if m.field == "gold")
    assert gold.new_value == 35
    item = next(m for m in mutations if m.field == "items_add")
    assert item.new_value == {"item_id": "healing_potion", "quantity": 1}


def test_partial_progress_no_completion():
    quest = _quest()
    quest["objectives"][0]["target_count"] = 3
    tracker, repos = _tracker(
        [quest],
        [{"id": "wolf_1", "name": "Wolf", "entity_type": "wolf"}],
    )
    events = [{"event_type": "DEATH", "target_id": "wolf_1"}]
    quest_events, xp, mutations = tracker.process(events, FakeContext(), "g1")
    assert quest_events == []
    assert xp == 0 and mutations == []
    assert repos["world_state"].quests["wolves"]["objectives"][0]["current_count"] == 1


def test_irrelevant_events_are_cheap_noop():
    tracker, repos = _tracker([_quest()])
    quest_events, xp, mutations = tracker.process(
        [{"event_type": "REST"}], FakeContext(), "g1")
    assert (quest_events, xp, mutations) == ([], 0, [])


def test_quest_giver_death_fails_available_orphans_active():
    active = _quest()
    active["objectives"].append({
        "id": "return", "description": "Return to Old Tom",
        "objective_type": "talk", "target_npc_id": "old_tom", "is_complete": False,
    })
    available = _quest()
    available["id"] = "wolves2"
    available["status"] = "available"
    tracker, repos = _tracker(
        [active, available],
        [{"id": "old_tom", "name": "Old Tom", "entity_type": "npc"}],
    )
    events = [{"event_type": "DEATH", "target_id": "old_tom"}]
    quest_events, _, _ = tracker.process(events, FakeContext(), "g1")

    assert ("wolves2", "failed") in repos["world_state"].status_updates
    types = [e["event_type"] for e in quest_events]
    assert "QUEST_FAILED" in types
    # The active quest's turn-in objective auto-completed
    orphaned = repos["world_state"].quests["wolves"]
    turn_in = next(o for o in orphaned["objectives"] if o["id"] == "return")
    assert turn_in["is_complete"] is True
