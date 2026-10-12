"""Tests for src/text_rpg/mechanics/quest_progress.py."""
from __future__ import annotations

import copy

from text_rpg.mechanics.quest_progress import (
    merge_negotiated_objectives,
    Progress,
    apply_progress,
    compute_rewards,
    handle_quest_giver_death,
    is_ready_for_turn_in,
    match_events_to_objectives,
    objective_progress_text,
)


def _wolf_quest():
    return {
        "id": "wolves",
        "name": "Wolves of the Whisperwood",
        "quest_giver_id": "old_tom",
        "status": "active",
        "xp_reward": 100,
        "gold_reward": 25,
        "item_rewards": ["healing_potion"],
        "objectives": [
            {"id": "go", "description": "Travel to the Whisperwood",
             "objective_type": "travel", "target_location_id": "whisperwood_edge",
             "is_complete": False},
            {"id": "kill", "description": "Defeat the wolf pack (0/3)",
             "objective_type": "kill", "target_entity_type": "wolf",
             "target_count": 3, "current_count": 0, "is_complete": False},
            {"id": "return", "description": "Return to Old Tom",
             "objective_type": "talk", "target_npc_id": "old_tom",
             "is_complete": False},
        ],
    }


class TestMatching:
    def test_travel_objective_matches_move(self):
        events = [{"event_type": "MOVE", "location_id": "whisperwood_edge"}]
        progresses = match_events_to_objectives(events, [_wolf_quest()])
        assert [p.objective_id for p in progresses] == ["go"]

    def test_wrong_location_no_match(self):
        events = [{"event_type": "MOVE", "location_id": "millhaven_square"}]
        assert match_events_to_objectives(events, [_wolf_quest()]) == []

    def test_kill_matches_entity_by_lookup(self):
        events = [{"event_type": "DEATH", "target_id": "wolf_a3f2b1"}]
        lookup = {"wolf_a3f2b1": {"id": "wolf_a3f2b1", "name": "Grey Wolf", "entity_type": "wolf"}}
        progresses = match_events_to_objectives(events, [_wolf_quest()], lookup)
        assert [p.objective_id for p in progresses] == ["kill"]

    def test_kill_matches_by_id_suffix_strip(self):
        events = [{"event_type": "DEATH", "target_id": "wolf_1a2b3c4d"}]
        progresses = match_events_to_objectives(events, [_wolf_quest()])
        assert [p.objective_id for p in progresses] == ["kill"]

    def test_unrelated_death_no_match(self):
        events = [{"event_type": "DEATH", "target_id": "bandit_9f"}]
        lookup = {"bandit_9f": {"id": "bandit_9f", "name": "Bandit", "entity_type": "bandit"}}
        assert match_events_to_objectives(events, [_wolf_quest()], lookup) == []

    def test_turn_in_gated_until_others_complete(self):
        quest = _wolf_quest()
        events = [{"event_type": "DIALOGUE", "target_id": "old_tom"}]
        assert match_events_to_objectives(events, [quest]) == []

        for obj in quest["objectives"][:2]:
            obj["is_complete"] = True
        progresses = match_events_to_objectives(events, [quest])
        assert [p.objective_id for p in progresses] == ["return"]

    def test_non_giver_talk_objective_not_gated(self):
        quest = _wolf_quest()
        quest["objectives"][2]["target_npc_id"] = "someone_else"
        events = [{"event_type": "DIALOGUE", "target_id": "someone_else"}]
        progresses = match_events_to_objectives(events, [quest])
        assert [p.objective_id for p in progresses] == ["return"]

    def test_interact_matches_item_pickup(self):
        quest = _wolf_quest()
        quest["objectives"][1] = {
            "id": "fetch", "description": "Recover the goods",
            "objective_type": "interact", "target_item_id": "trade_goods",
            "is_complete": False,
        }
        events = [{"event_type": "ITEM_PICKUP", "mechanical_details": {"item_id": "trade_goods"}}]
        progresses = match_events_to_objectives(events, [quest])
        assert [p.objective_id for p in progresses] == ["fetch"]


class TestApplyProgress:
    def test_kill_counts_to_target(self):
        quest = _wolf_quest()
        quest, done, complete = apply_progress(quest, [Progress("wolves", "kill")] * 2)
        assert quest["objectives"][1]["current_count"] == 2
        assert done == [] and not complete

        quest, done, complete = apply_progress(quest, [Progress("wolves", "kill")])
        assert quest["objectives"][1]["is_complete"] is True
        assert len(done) == 1 and not complete

    def test_quest_completes_when_all_objectives_done(self):
        quest = _wolf_quest()
        quest["objectives"][0]["is_complete"] = True
        quest["objectives"][1]["is_complete"] = True
        quest, done, complete = apply_progress(quest, [Progress("wolves", "return")])
        assert complete is True

    def test_progress_for_other_quest_ignored(self):
        quest = _wolf_quest()
        original = copy.deepcopy(quest)
        quest, _, complete = apply_progress(quest, [Progress("other", "kill")])
        assert quest == original and not complete


class TestMergeNegotiated:
    def _objectives(self):
        return [
            {"id": "kill", "description": "Defeat the wolf pack (0/3)",
             "objective_type": "kill", "target_entity_type": "wolf",
             "target_count": 3, "current_count": 0,
             "is_complete": False, "negotiable": True},
            {"id": "return", "description": "Return to Old Tom",
             "objective_type": "talk", "target_npc_id": "old_tom",
             "target_count": 1, "current_count": 0,
             "is_complete": False, "negotiable": False},
        ]

    def test_negotiable_count_lowered_and_reworded(self):
        merged = merge_negotiated_objectives(self._objectives(), [
            {"id": "kill", "description": "Defeat two wolves", "target_count": 2},
        ])
        assert merged[0]["target_count"] == 2
        assert merged[0]["description"] == "Defeat wolf (2 required)"
        # Canonical fields untouched
        assert merged[0]["objective_type"] == "kill"
        assert merged[0]["target_entity_type"] == "wolf"

    def test_count_cannot_be_raised_or_zeroed(self):
        merged = merge_negotiated_objectives(self._objectives(), [
            {"id": "kill", "target_count": 10},
        ])
        assert merged[0]["target_count"] == 3
        merged = merge_negotiated_objectives(self._objectives(), [
            {"id": "kill", "target_count": 0},
        ])
        assert merged[0]["target_count"] == 1

    def test_non_negotiable_objective_unchanged(self):
        merged = merge_negotiated_objectives(self._objectives(), [
            {"id": "return", "description": "No need to come back", "target_count": 1},
        ])
        assert merged[1]["description"] == "Return to Old Tom"

    def test_objectives_never_added_or_removed(self):
        merged = merge_negotiated_objectives(self._objectives(), [
            {"id": "brand_new", "description": "Also fetch me a dragon", "target_count": 1},
        ])
        assert len(merged) == 2
        assert all(o["id"] in ("kill", "return") for o in merged)

    def test_positional_fallback_when_ids_missing(self):
        merged = merge_negotiated_objectives(self._objectives(), [
            {"description": "Defeat one wolf", "required_count": 1},
            {"description": "ignored"},
        ])
        assert merged[0]["target_count"] == 1
        assert merged[1]["description"] == "Return to Old Tom"


class TestHelpers:
    def test_progress_text_replaces_stale_counts(self):
        obj = {"description": "Defeat the wolf pack (0/3)",
               "target_count": 3, "current_count": 2}
        assert objective_progress_text(obj) == "Defeat the wolf pack (2/3)"

    def test_ready_for_turn_in(self):
        quest = _wolf_quest()
        assert not is_ready_for_turn_in(quest)
        quest["objectives"][0]["is_complete"] = True
        quest["objectives"][1]["is_complete"] = True
        assert is_ready_for_turn_in(quest)
        quest["objectives"][2]["is_complete"] = True
        assert not is_ready_for_turn_in(quest)

    def test_compute_rewards(self):
        rewards = compute_rewards(_wolf_quest())
        assert rewards == {"xp": 100, "gold": 25, "items": ["healing_potion"]}

    def test_giver_death_outcomes(self):
        active = _wolf_quest()
        available = _wolf_quest()
        available["id"] = "wolves2"
        available["status"] = "available"
        outcomes = dict(handle_quest_giver_death([active, available], "old_tom"))
        assert outcomes == {"wolves": "orphan", "wolves2": "fail"}

    def test_giver_death_other_npc_no_outcomes(self):
        assert handle_quest_giver_death([_wolf_quest()], "captain_vale") == []
