"""Tests for src/text_rpg/mechanics/encounters.py."""
from __future__ import annotations

import random

from text_rpg.mechanics.encounters import (
    instantiate_encounter,
    roll_encounter,
)


ENCOUNTER = {
    "id": "wolf_pack",
    "name": "Wolf Pack",
    "location_ids": ["whisperwood_edge"],
    "encounter_chance": 1.0,
    "loot": {
        "items": [{"id": "beast_hide", "chance": 1.0}, {"id": "gem", "chance": 0.0}],
        "gold_min": 1, "gold_max": 3,
    },
    "entities": [{
        "id": "wolf", "name": "Wolf", "entity_type": "enemy",
        "level": 1, "hp_max": 11, "ac": 13,
        "count_min": 2, "count_max": 4, "xp_value": 25,
        "ability_scores": {"strength": 12},
        "attacks": [{"name": "Bite", "damage_dice": "2d4"}],
    }],
}


class TestRollEncounter:
    def test_wrong_location_never_triggers(self):
        rng = random.Random(1)
        assert roll_encounter([ENCOUNTER], "millhaven_square", rng) is None

    def test_certain_encounter_triggers_with_damper(self):
        # chance 1.0 * damper 0.5 -> triggers when rng.random() < 0.5
        rng = random.Random(2)  # first random() of seed 2 is ~0.956 -> miss
        first = rng.random()
        rng2 = random.Random(2)
        result = roll_encounter([ENCOUNTER], "whisperwood_edge", rng2)
        assert (result is not None) == (first < 0.5)

    def test_zero_chance_never_triggers(self):
        enc = dict(ENCOUNTER, encounter_chance=0.0)
        rng = random.Random(3)
        for _ in range(20):
            assert roll_encounter([enc], "whisperwood_edge", rng) is None


class TestInstantiate:
    def test_count_within_bounds_and_hostile(self):
        rng = random.Random(42)
        entities = instantiate_encounter(ENCOUNTER, "g1", "whisperwood_edge", rng)
        assert 2 <= len(entities) <= 4
        for e in entities:
            assert e["is_hostile"] is True
            assert e["is_alive"] is True
            assert e["location_id"] == "whisperwood_edge"
            assert e["hp_current"] == e["hp_max"] == 11
            assert e["id"].startswith("wolf_")
            assert e["properties"]["xp_value"] == 25

    def test_unique_ids(self):
        rng = random.Random(7)
        entities = instantiate_encounter(ENCOUNTER, "g1", "whisperwood_edge", rng)
        ids = [e["id"] for e in entities]
        assert len(ids) == len(set(ids))

    def test_loot_respects_chances(self):
        rng = random.Random(5)
        entities = instantiate_encounter(ENCOUNTER, "g1", "whisperwood_edge", rng)
        loot = entities[0]["loot_table"]
        item_ids = [d.get("item_id") for d in loot if "item_id" in d]
        assert "beast_hide" in item_ids
        assert "gem" not in item_ids
