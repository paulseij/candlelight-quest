"""Tests for src/text_rpg/content/region_seeder.py against a real in-memory DB."""
from __future__ import annotations

import pytest

from text_rpg.content.loader import find_region_for_location, load_all_regions
from text_rpg.content.region_seeder import seed_region
from text_rpg.storage.repos.connection_repo import ConnectionRepo
from text_rpg.storage.repos.entity_repo import EntityRepo
from text_rpg.storage.repos.location_repo import LocationRepo
from text_rpg.storage.repos.shop_repo import ShopRepo
from text_rpg.storage.repos.world_state_repo import WorldStateRepo


@pytest.fixture
def repos(in_memory_db):
    from text_rpg.storage.repos.save_game_repo import SaveGameRepo

    save_game = SaveGameRepo(in_memory_db)
    save_game.create_game("g1", "Test Hero", "char1", "thornfield_village")
    return {
        "world_state": WorldStateRepo(in_memory_db),
        "location": LocationRepo(in_memory_db),
        "connection": ConnectionRepo(in_memory_db),
        "entity": EntityRepo(in_memory_db),
        "shop": ShopRepo(in_memory_db),
        "save_game": save_game,
    }


def test_seed_starting_region(repos):
    result = seed_region("verdant_reach", "g1", repos,
                         visited_location_id="thornfield_village")
    assert result["seeded"] is True
    assert result["entry_location_id"]

    locations = repos["location"].get_all("g1")
    assert len(locations) > 0
    visited = [l for l in locations if l.get("visited")]
    assert [l["id"] for l in visited] == ["thornfield_village"]

    assert repos["connection"].count_all("g1") > 0
    npcs = [e for e in repos["entity"].get_by_game("g1") if e.get("entity_type") == "npc"]
    assert len(npcs) > 0
    ambient = [e for e in npcs if e['id'].startswith('ambient__')]
    populated = [l for l in locations if l.get('location_type') in
                 {'settlement', 'town', 'shop', 'tavern', 'temple', 'residence'}]
    expected_population = {
        'settlement': 2, 'town': 3, 'shop': 1, 'tavern': 3, 'temple': 2, 'residence': 1,
    }
    assert len(ambient) == sum(expected_population[l['location_type']] for l in populated)
    assert all(e['properties']['quest_authority'] is False and e['generated'] for e in ambient)
    quests = repos["world_state"].get_all_quests("g1")
    assert len(quests) == 2  # wolves_of_whisperwood + missing_shipment


def test_seed_is_idempotent(repos):
    seed_region("verdant_reach", "g1", repos)
    count_locations = len(repos["location"].get_all("g1"))
    count_entities = len(repos["entity"].get_by_game("g1"))

    result = seed_region("verdant_reach", "g1", repos)
    assert result["seeded"] is False
    assert len(repos["location"].get_all("g1")) == count_locations
    assert len(repos["entity"].get_by_game("g1")) == count_entities


def test_ambient_population_upgrade_restores_existing_populated_places(repos):
    import importlib
    seed_region('verdant_reach', 'g1', repos)
    with repos['save_game'].db.get_connection() as conn:
        conn.execute("DELETE FROM entities WHERE game_id='g1' AND id LIKE 'ambient__%'")
        importlib.import_module('text_rpg.storage.migrations.028_ambient_population').upgrade(conn)
    ambient = [entity for entity in repos['entity'].get_by_game('g1') if entity['id'].startswith('ambient__')]
    assert ambient
    assert all(entity['properties']['quest_authority'] is False for entity in ambient)


def test_ambient_role_upgrade_preserves_relationship_memory(repos):
    import importlib
    seed_region('verdant_reach', 'g1', repos)
    patron = next(entity for entity in repos['entity'].get_by_game('g1')
                  if entity['id'] == 'ambient__thornfield_inn')
    props = {**patron['properties'], 'relationships': {'player': {'trust': -2}}}
    repos['entity'].update_field(patron['id'], 'profession', 'innkeeper', 'g1')
    repos['entity'].update_field(patron['id'], 'properties', props, 'g1')
    with repos['save_game'].db.get_connection() as conn:
        importlib.import_module('text_rpg.storage.migrations.029_ambient_roles').upgrade(conn)
    upgraded = repos['entity'].get(patron['id'], 'g1')
    assert upgraded['profession'] == 'patron'
    assert upgraded['properties']['relationships']['player']['trust'] == -2


def test_ambient_crowd_upgrade_adds_people_without_replacing_primary(repos):
    import importlib
    seed_region('verdant_reach', 'g1', repos)
    with repos['save_game'].db.get_connection() as conn:
        conn.execute("DELETE FROM entities WHERE game_id='g1' AND id='ambient__thornfield_village__2'")
        importlib.import_module('text_rpg.storage.migrations.030_ambient_crowds').upgrade(conn)
    residents = [entity for entity in repos['entity'].get_by_location('g1', 'thornfield_village')
                 if entity['id'].startswith('ambient__')]
    assert len(residents) == 2
    assert len({entity['name'] for entity in residents}) == 2


def test_all_authored_regions_seed_without_error(repos):
    # Also validates that every region's TOML content parses and inserts.
    for region_id in load_all_regions():
        result = seed_region(region_id, "g1", repos)
        assert result["entry_location_id"], f"region {region_id} has no entry location"
    # Multiple regions now coexist in one game DB
    region_ids = {l.get("region_id") for l in repos["location"].get_all("g1")}
    assert len(region_ids) >= 19


def test_second_save_preserves_independent_worlds(repos):
    # Shared authored IDs must never transfer ownership to a newer save.
    seed_region("verdant_reach", "g1", repos)
    repos["save_game"].create_game("g2", "Second Hero", "char2", "thornfield_village")
    result = seed_region("verdant_reach", "g2", repos)
    assert result["seeded"] is True
    npcs_g2 = [e for e in repos["entity"].get_by_game("g2") if e.get("entity_type") == "npc"]
    assert len(npcs_g2) > 0
    entities = repos["entity"]
    npc_id = npcs_g2[0]["id"]
    entities.update_field(npc_id, "hp_current", 1, game_id="g1")
    assert entities.get(npc_id, "g1")["hp_current"] == 1
    assert entities.get(npc_id, "g2")["hp_current"] == npcs_g2[0]["hp_current"]
    with pytest.raises(ValueError, match="Ambiguous"):
        entities.update_field(npc_id, "hp_current", 0)
    assert len(entities.get_by_game("g1")) == len(entities.get_by_game("g2"))
    assert repos["location"].get("thornfield_village", "g1")
    assert repos["world_state"].get_region("verdant_reach", "g1")
    quests = repos["world_state"].get_all_quests("g1")
    repos["world_state"].update_quest_status(quests[0]["id"], "g1", "completed")
    assert repos["world_state"].get_quest(quests[0]["id"], "g2")["status"] != "completed"
    with entities.db.get_connection() as conn:
        shops = conn.execute("SELECT id FROM shops WHERE game_id='g1'").fetchall()
    assert shops
    shop_id = shops[0][0]
    original = repos["shop"].get_shop(shop_id, "g2")["gold_reserve"]
    repos["shop"].update_gold_reserve(shop_id, 0, game_id="g1")
    assert repos["shop"].get_shop(shop_id, "g2")["gold_reserve"] == original
    entities.db.active_game_id = "g1"
    entities.update_field(npc_id, "hp_current", 2)
    assert entities.get(npc_id)["hp_current"] == 2
    assert seed_region("verdant_reach", "g1", repos)["seeded"] is False
    entities.delete(npc_id)
    assert entities.get(npc_id, "g2")


def test_find_region_for_location_index():
    assert find_region_for_location("thornfield_village") == "verdant_reach"
    assert find_region_for_location("nonexistent_place_xyz") is None


def test_numbered_authored_routes_survive_storage(repos):
    from text_rpg.engine.exits import exit_options
    from text_rpg.models.action import Action
    from text_rpg.systems.base import GameContext
    from text_rpg.systems.exploration.system import ExplorationSystem

    seed_region('ashen_highlands', 'g1', repos)
    for location_id, selector in [('highland_road', 'north 1'), ('cinderhold_forge', 'east 2')]:
        loc = repos['location'].get(location_id, 'g1')
        expected = dict(exit_options(loc['connections']))[selector]['target_location_id']
        loc['connections'] = repos['connection'].get_connections('g1', location_id)
        ctx = GameContext('g1', {'id': 'p'}, loc, [])
        result = ExplorationSystem().resolve(Action('move', 'p', target_id=selector), ctx)
        assert result.success
        assert result.state_mutations[0].new_value == expected
        ambiguous = ExplorationSystem().resolve(Action('move', 'p', target_id=selector.split()[0]), ctx)
        assert not ambiguous.state_mutations
        assert selector in ambiguous.outcome_description
        path = dict(exit_options(loc['connections']))[selector]
        before = len(loc['connections'])
        repos['connection'].add_connection('g1', location_id, path['target_location_id'], path['direction'])
        assert len(repos['connection'].get_connections('g1', location_id)) == before


def test_branch_migration_restores_missing_paths_preserving_locks(repos):
    import importlib
    seed_region('ashen_highlands', 'g1', repos)
    db = repos['connection'].db
    with db.get_connection() as conn:
        conn.execute('DELETE FROM location_connections WHERE rowid NOT IN (SELECT MIN(rowid) FROM location_connections GROUP BY game_id,source_location_id,direction)')
        conn.execute("UPDATE location_connections SET is_locked=1 WHERE source_location_id='highland_road' AND direction='north'")
        importlib.import_module('text_rpg.storage.migrations.027_branching_connections').upgrade(conn)
    paths = repos['connection'].get_connections('g1', 'highland_road')
    north = [p for p in paths if p['direction'] == 'north']
    assert len(north) == 2
    assert north[0]['is_locked'] == 1
    assert north[1]['is_locked'] == 0
