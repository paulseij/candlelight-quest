"""Tests for Director schema validation — validate_region and NPC scaling."""
from __future__ import annotations

import pytest

from text_rpg.systems.director.schemas import validate_region, validate_npc


class TestValidateRegion:
    """Tests for validate_region schema validation."""

    def test_valid_minimal_region(self):
        data = {"name": "Test Region", "description": "A test region."}
        result = validate_region(data)
        assert result["name"] == "Test Region"
        assert result["description"] == "A test region."
        assert "id" in result
        assert result["level_range_min"] == 1
        assert result["level_range_max"] == 5
        assert result["climate"] == "temperate"
        assert result["locations"] == []
        assert result["npcs"] == []

    def test_missing_name_raises(self):
        with pytest.raises(ValueError, match="missing required fields"):
            validate_region({"description": "No name"})

    def test_missing_description_raises(self):
        with pytest.raises(ValueError, match="missing required fields"):
            validate_region({"name": "No desc"})

    def test_empty_name_raises(self):
        with pytest.raises(ValueError, match="Invalid region name"):
            validate_region({"name": "", "description": "Test"})

    def test_long_name_raises(self):
        with pytest.raises(ValueError, match="Invalid region name"):
            validate_region({"name": "x" * 101, "description": "Test"})

    def test_level_range_clamped(self):
        data = {
            "name": "Test",
            "description": "Test",
            "level_range_min": -5,
            "level_range_max": 30,
        }
        result = validate_region(data)
        assert result["level_range_min"] == 1
        assert result["level_range_max"] == 20

    def test_level_range_max_at_least_min(self):
        data = {
            "name": "Test",
            "description": "Test",
            "level_range_min": 10,
            "level_range_max": 5,
        }
        result = validate_region(data)
        assert result["level_range_max"] >= result["level_range_min"]

    def test_locations_validated(self):
        data = {
            "name": "Test",
            "description": "Test",
            "locations": [
                {"name": "Town", "description": "A town"},
                "invalid_entry",
                {"name": "Cave"},
            ],
        }
        result = validate_region(data)
        assert len(result["locations"]) == 2
        assert result["locations"][0]["name"] == "Town"
        assert result["locations"][1]["name"] == "Cave"
        # Check defaults
        assert result["locations"][1]["location_type"] == "wilderness"
        assert result["locations"][1]["visited"] is False

    def test_npcs_validated(self):
        data = {
            "name": "Test",
            "description": "Test",
            "npcs": [
                {"name": "Bob", "description": "A guard"},
                {"invalid": "no name"},
            ],
        }
        result = validate_region(data)
        assert len(result["npcs"]) == 1
        assert result["npcs"][0]["name"] == "Bob"

    def test_non_list_locations_cleaned(self):
        data = {"name": "Test", "description": "Test", "locations": "not a list"}
        result = validate_region(data)
        assert result["locations"] == []

    def test_preserves_custom_fields(self):
        data = {
            "name": "Test",
            "description": "Test",
            "climate": "arctic",
            "level_range_min": 5,
            "level_range_max": 10,
        }
        result = validate_region(data)
        assert result["climate"] == "arctic"
        assert result["level_range_min"] == 5
        assert result["level_range_max"] == 10


class TestNPCScaling:
    """Test _scale_npc_to_player function."""

    def test_import(self):
        """Verify the scaling function is importable."""
        from text_rpg.systems.director.director import _scale_npc_to_player
        assert callable(_scale_npc_to_player)

    def test_scales_level(self, monkeypatch):
        # These assertions cover normal scaling, not the separate 2% rare spawn.
        monkeypatch.setattr("random.random", lambda: 0.5)
        from text_rpg.systems.director.director import _scale_npc_to_player
        from text_rpg.systems.base import GameContext

        ctx = GameContext(
            game_id="g1",
            character={"id": "c1", "level": 8},
            location={"id": "loc1", "region_id": "verdant_reach"},
            entities=[],
            turn_number=10,
            world_time=480,
        )
        npc = {"level": 1, "hp_max": 10, "hp_current": 10, "ac": 10}
        result = _scale_npc_to_player(npc, ctx)

        # Level should be within player_level ± 2 but clamped to region max (5)
        assert 1 <= result["level"] <= 5  # region max is 5

    def test_scales_hp(self):
        from text_rpg.systems.director.director import _scale_npc_to_player
        from text_rpg.systems.base import GameContext

        ctx = GameContext(
            game_id="g1",
            character={"id": "c1", "level": 3},
            location={"id": "loc1", "region_id": "verdant_reach"},
            entities=[],
            turn_number=10,
            world_time=480,
        )
        npc = {"level": 1, "hp_max": 10, "hp_current": 10, "ac": 10}
        result = _scale_npc_to_player(npc, ctx)

        # HP should be scaled up since level increased
        if result["level"] > 1:
            assert result["hp_max"] >= 10
        assert result["hp_current"] == result["hp_max"]

    def test_hp_never_below_4(self):
        from text_rpg.systems.director.director import _scale_npc_to_player
        from text_rpg.systems.base import GameContext

        ctx = GameContext(
            game_id="g1",
            character={"id": "c1", "level": 1},
            location={"id": "loc1", "region_id": "verdant_reach"},
            entities=[],
            turn_number=10,
            world_time=480,
        )
        npc = {"level": 10, "hp_max": 5, "hp_current": 5, "ac": 10}
        result = _scale_npc_to_player(npc, ctx)
        assert result["hp_max"] >= 4

    def test_no_region_id_uses_wide_range(self, monkeypatch):
        # These assertions cover normal scaling, not the separate 2% rare spawn.
        monkeypatch.setattr("random.random", lambda: 0.5)
        from text_rpg.systems.director.director import _scale_npc_to_player
        from text_rpg.systems.base import GameContext

        ctx = GameContext(
            game_id="g1",
            character={"id": "c1", "level": 10},
            location={"id": "loc1"},
            entities=[],
            turn_number=10,
            world_time=480,
        )
        npc = {"level": 1, "hp_max": 10, "hp_current": 10, "ac": 10}
        result = _scale_npc_to_player(npc, ctx)

        # Without region constraint, level should be 8-12
        assert 8 <= result["level"] <= 12

    def test_ac_capped_at_20(self):
        from text_rpg.systems.director.director import _scale_npc_to_player
        from text_rpg.systems.base import GameContext

        ctx = GameContext(
            game_id="g1",
            character={"id": "c1", "level": 20},
            location={"id": "loc1"},
            entities=[],
            turn_number=10,
            world_time=480,
        )
        npc = {"level": 1, "hp_max": 10, "hp_current": 10, "ac": 18}
        result = _scale_npc_to_player(npc, ctx)
        assert result["ac"] <= 20
