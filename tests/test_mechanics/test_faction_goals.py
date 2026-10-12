"""Tests for src/text_rpg/mechanics/faction_goals.py."""
from __future__ import annotations

from text_rpg.mechanics.faction_goals import check_faction_goals


FACTIONS = {
    "guard": {
        "name": "The Guard",
        "goals": [
            {"id": "patrol", "check_interval": 10, "success_chance": 1.0,
             "success_event": "Patrols succeeded."},
        ],
    },
    "merchants": {
        "name": "The Merchants",
        "goals": [
            {"id": "caravan", "check_interval": 30, "success_chance": 1.0,
             "success_event": "Caravan arrived."},
        ],
    },
}


def test_goals_respect_individual_intervals():
    # At turn 15 with no history, both intervals (10 and 30... turn 15 >= both
    # counted from 0) would lapse; last_checked staggers them.
    last_checked = {"guard_patrol": 10, "merchants_caravan": 0}
    events = check_faction_goals(FACTIONS, 25, last_checked)
    fired = {e["mechanical_details"]["goal_id"] for e in events}
    # guard: 25 - 10 = 15 >= 10 -> fires. merchants: 25 - 0 = 25 < 30 -> holds.
    assert fired == {"patrol"}


def test_goal_holds_within_interval():
    last_checked = {"guard_patrol": 20, "merchants_caravan": 20}
    events = check_faction_goals(FACTIONS, 25, last_checked)
    assert events == []


def test_no_history_fires_everything_due():
    events = check_faction_goals(FACTIONS, 100, None)
    fired = {e["mechanical_details"]["goal_id"] for e in events}
    assert fired == {"patrol", "caravan"}
