from unittest.mock import Mock
import pytest

from tests.test_engine.test_exploration_pressure import game
from text_rpg.cli.input_handler import InputHandler


@pytest.fixture
def ground(game):
    repos, make_loop, char = game
    repos['save_game'].update_location('g', 'saltmere_docks')
    repos['location'].update_field('saltmere_docks', 'g', 'items', [
        'A half-eaten piece of hard tack', 'A small bundle of dried mountain herbs'])
    return repos, make_loop, char


@pytest.mark.parametrize('text', ['pick up half eaten piece of hard tack', 'pick up half-eaten piece of hard tack', 'take the half eaten piece of hard tack'])
def test_named_pickup_removes_one_item_and_persists_display_name(ground, text):
    repos, make_loop, char = ground
    loop = make_loop()
    result = loop.process_turn(text, 'g', 'pickup-one')
    assert result.consumes_turn and 'You pick up' in result.narrative
    assert len(repos['location'].get('saltmere_docks','g')['items']) == 1
    inv = repos['world_state'].get_inventory(char['id'],'g')['items']
    assert len(inv) == 1 and inv[0]['quantity'] == 1
    item = repos['custom_item'].get_custom_item(inv[0]['item_id'])
    assert item['name'] == 'A half-eaten piece of hard tack' and item['effects'] == []
    repos['save_game'].db.close(); loop = make_loop()
    assert loop.process_turn(text, 'g', 'pickup-one').narrative == result.narrative
    retry = loop.process_turn(text, 'g')
    assert not retry.consumes_turn
    assert repos['world_state'].get_inventory(char['id'],'g')['items'] == inv


def test_pick_all_and_empty_ground(ground):
    repos, make_loop, char = ground
    loop = make_loop()
    assert loop.process_turn('pick up items','g').consumes_turn
    assert len(repos['world_state'].get_inventory(char['id'],'g')['items']) == 2
    assert repos['location'].get('saltmere_docks','g')['items'] == []
    assert 'no items on the ground' in loop.process_turn('pick up items','g').narrative


def test_missing_or_ambiguous_target_does_not_choose_arbitrarily(ground):
    repos, make_loop, char = ground
    repos['location'].update_field('saltmere_docks','g','items',['Red herb','Blue herb'])
    loop = make_loop()
    for text in ['pick up', 'pick up herb', 'pick up sword']:
        result = loop.process_turn(text,'g')
        assert not result.consumes_turn
        assert 'Red herb' in result.narrative and 'Blue herb' in result.narrative
    assert len(repos['location'].get('saltmere_docks','g')['items']) == 2


def test_catalog_stacks_keep_identity_and_quantity(ground):
    repos, make_loop, char = ground
    repos['location'].update_field('saltmere_docks','g','items',[{'item_id':'healing_potion','quantity':2}])
    result = make_loop().process_turn('pick up healing potion','g')
    assert result.consumes_turn
    assert repos['world_state'].get_inventory(char['id'],'g')['items'] == [{'item_id':'healing_potion','quantity':2}]
    assert not repos['custom_item'].get_custom_items('g', char['id'])


def test_pickup_failure_rolls_back_inventory_and_custom_item(ground, monkeypatch):
    repos, make_loop, char = ground
    monkeypatch.setattr(repos['location'],'update_field',Mock(side_effect=RuntimeError('write failed')))
    with pytest.raises(RuntimeError):
        make_loop().process_turn('pick up items','g')
    assert repos['world_state'].get_inventory(char['id'],'g')['items'] == []
    assert not repos['custom_item'].get_custom_items('g',char['id'])
    assert len(repos['location'].get('saltmere_docks','g')['items']) == 2


def test_pickup_does_not_steal_lock_quest_or_storage_commands():
    handler = InputHandler()
    for text, action in [('pick up','pickup'), ('pick lock','puzzle'), ('take quest','accept_quest'), ('take out sword','retrieve'), ('take off armor','unequip')]:
        assert handler.classify(text)['action_type'] == action
