"""Tests for src/text_rpg/storage/repos/custom_item_repo.py."""
from __future__ import annotations

import pytest

from text_rpg.storage.repos.custom_item_repo import CustomItemRepo
from text_rpg.storage.repos.save_game_repo import SaveGameRepo


@pytest.fixture
def repo(in_memory_db):
    SaveGameRepo(in_memory_db).create_game("g1", "Hero", "char1", "loc1")
    return CustomItemRepo(in_memory_db)


def _item(**overrides):
    base = {
        "game_id": "g1",
        "character_id": "char1",
        "name": "Serrated Knife",
        "description": "A wicked blade.",
        "item_type": "weapon",
        "rarity": "common",
        "effects": [{"type": "damage_dice", "params": {"dice": "1d4"}}],
        "created_turn": 3,
    }
    base.update(overrides)
    return base


def test_save_and_get(repo):
    item_id = repo.save_custom_item(_item())
    row = repo.get_custom_item(item_id)
    assert row["name"] == "Serrated Knife"
    assert row["effects"][0]["type"] == "damage_dice"


def test_get_custom_items_ordered(repo):
    repo.save_custom_item(_item(name="First", created_turn=1))
    repo.save_custom_item(_item(name="Second", created_turn=5))
    items = repo.get_custom_items("g1", "char1")
    assert [i["name"] for i in items] == ["First", "Second"]
    assert repo.get_custom_items("g1", "someone_else") == []


def test_update_effects(repo):
    item_id = repo.save_custom_item(_item())
    repo.update_effects(item_id, [{"type": "light", "params": {}}])
    assert repo.get_custom_item(item_id)["effects"] == [{"type": "light", "params": {}}]


def test_to_catalog_entry_extracts_mechanics(repo):
    item_id = repo.save_custom_item(_item(effects=[
        {"type": "damage_dice", "params": {"dice": "1d6"}},
        {"type": "ac_bonus", "params": {"amount": 1}},
        {"type": "consumable_heal", "params": {"dice": "2d4"}},
    ]))
    entry = CustomItemRepo.to_catalog_entry(repo.get_custom_item(item_id))
    assert entry["custom"] is True
    assert entry["damage_dice"] == "1d6"
    assert entry["ac_bonus"] == 1
    assert entry["heal_dice"] == "2d4"
    assert entry["item_type"] == "weapon"
