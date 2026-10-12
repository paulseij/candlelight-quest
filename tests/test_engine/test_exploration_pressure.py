"""Real saved-turn regression tests for exploration discoveries and danger."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from text_rpg.app import GameApp
from text_rpg.content.loader import load_region_encounters
from text_rpg.content.region_seeder import seed_region
from text_rpg.engine.action_dispatcher import ActionDispatcher
from text_rpg.engine.system_registry import SystemRegistry
from text_rpg.engine.turn_loop import TurnLoop
from text_rpg.llm.context_packer import ContextPacker
from text_rpg.mechanics.character_creation import create_character
from text_rpg.models.action import ActionResult


@pytest.fixture
def game(in_memory_db, monkeypatch):
    app = GameApp(); app._db = in_memory_db; app.game_id = 'g'
    repos = app._get_repos()
    char = create_character(name='Tester', race='human', char_class='fighter',
        ability_scores={'strength': 14, 'dexterity': 14, 'constitution': 14, 'intelligence': 16, 'wisdom': 10, 'charisma': 10},
        skill_choices=['athletics', 'perception'], game_id='g')
    repos['save_game'].create_game('g', 'Test', char['id'], 'smuggler_den')
    repos['character'].save(char)
    repos['world_state'].save_inventory({'id': 'inv', 'game_id': 'g', 'owner_id': char['id'], 'items': []})
    seed_region('iron_coast', 'g', repos)
    llm = Mock()
    llm.generate.side_effect = AssertionError('Exploration outcomes must not require a model')
    registry = SystemRegistry(); registry.register_defaults(); registry.inject_all(repos=repos)
    def make_loop():
        return TurnLoop(registry, ActionDispatcher(registry, repos=repos), llm, ContextPacker(), None, None, repos)
    monkeypatch.setattr('text_rpg.mechanics.encounters.roll_encounter', lambda *args: None)
    # Player wins initiative. Encounters must not manufacture a player attack.
    initiative = iter([20, 1] * 20)
    monkeypatch.setattr('text_rpg.systems.combat.system.initiative_roll', lambda bonus: SimpleNamespace(total=next(initiative)))
    return repos, make_loop, char


def search_roll(monkeypatch, success, total):
    roll = Mock(return_value=(success, SimpleNamespace(individual_rolls=[total-3], modifier=3, total=total)))
    monkeypatch.setattr('text_rpg.systems.exploration.system.skill_check', roll)
    return roll


def test_discovery_persists_and_repeat_does_not_roll_or_advance(game, monkeypatch):
    repos, make_loop, char = game
    roll = search_roll(monkeypatch, True, 17)
    loop = make_loop()
    result = loop.process_turn('investigate', 'g')
    assert 'cargo manifest' in result.narrative and result.consumes_turn
    assert repos['save_game'].get_game('g')['world_time'] == 490
    assert roll.call_args.args[0] == char['ability_scores']['intelligence']  # Investigation uses Intelligence.
    repos['save_game'].db.close()
    loop = make_loop()
    repeat = loop.process_turn('investigate', 'g')
    assert not repeat.consumes_turn and not repeat.action_result.dice_rolls
    assert 'already searched' in repeat.narrative
    assert repos['save_game'].get_game('g')['world_time'] == 490
    assert 'cargo manifest' in loop.process_turn('look', 'g').narrative
    roll.assert_called_once()


def test_failed_search_cannot_be_rerolled_by_renaming_action(game, monkeypatch):
    repos, make_loop, char = game
    roll = search_roll(monkeypatch, False, 8)
    loop = make_loop()
    assert loop.process_turn('investigate', 'g').consumes_turn
    repeat = loop.process_turn('search', 'g')
    assert not repeat.consumes_turn
    roll.assert_called_once()
    assert not repos['location'].get('smuggler_den', 'g')['properties'].get('discovered_ids')


def test_new_authored_findings_are_available_in_existing_location(game, monkeypatch):
    repos, make_loop, _ = game
    repos['location'].update_field('smuggler_den', 'g', 'properties', {})
    search_roll(monkeypatch, True, 17)
    assert 'cargo manifest' in make_loop().process_turn('investigate', 'g').narrative


def test_empty_search_has_no_fake_discovery_or_roll(game, monkeypatch):
    repos, make_loop, _ = game
    repos['save_game'].update_location('g', 'saltmere_docks')
    roll = search_roll(monkeypatch, True, 20)
    result = make_loop().process_turn('investigate', 'g')
    assert 'Where do you want to search?' in result.narrative
    assert not any(e['event_type'] == 'DISCOVERY' for e in result.events)
    roll.assert_not_called()


def test_encounter_can_start_on_first_search_and_has_real_combat(game, monkeypatch):
    repos, make_loop, char = game
    search_roll(monkeypatch, False, 8)
    encounter = next(e for e in load_region_encounters('iron_coast') if e['id'] == 'smuggler_lookout')
    monkeypatch.setattr('text_rpg.mechanics.encounters.roll_encounter', lambda *args: encounter)
    loop = make_loop()
    result = loop.process_turn('investigate', 'g')
    combat = repos['world_state'].get_active_combat('g')
    assert combat and combat['is_active']
    assert 'Smuggler Lookout' in result.narrative and 'Choose an action' in result.narrative
    assert any(e['event_type'] == 'COMBAT_START' for e in result.events)
    assert not any(e['event_type'] == 'ATTACK' for e in result.events)
    assert repos['character'].get(char['id'])['hp_current'] == char['hp_current']
    assert combat['turn_order'][0] == char['id']


def test_victory_returns_scene_options_and_persists_aftermath(game, monkeypatch):
    repos, make_loop, _ = game
    search_roll(monkeypatch, False, 8)
    encounter = next(e for e in load_region_encounters('iron_coast') if e['id'] == 'smuggler_lookout')
    monkeypatch.setattr('text_rpg.mechanics.encounters.roll_encounter', lambda *args: encounter)
    loop = make_loop()
    loop.process_turn('investigate', 'g')
    combat = repos['world_state'].get_active_combat('g')
    enemy = next(c for c in combat['combatants'] if c['combatant_type'] == 'enemy')
    enemy['hp']['current'] = 1
    repos['world_state'].update_combat(combat['id'], {'combatants': combat['combatants']})
    monkeypatch.setattr('text_rpg.systems.combat.system.attack_roll',
                        lambda *args: (True, False, SimpleNamespace(individual_rolls=[18], total=24)))
    monkeypatch.setattr('text_rpg.systems.combat.system.damage_roll',
                        lambda *args: SimpleNamespace(individual_rolls=[6], modifier=4, total=10))
    result = loop.process_turn('1', 'g')
    assert 'Victory!' in result.narrative
    assert 'Things you can do here now:' in result.narrative
    props = repos['location'].get('smuggler_den', 'g')['properties']
    assert props['battle_history'][-1]['outcome'] == 'victory'
    assert 'Battle aftermath:' in loop.process_turn('look', 'g').narrative


def test_authored_puzzle_is_discovered_persists_and_rewards_only_once(game, monkeypatch):
    repos, make_loop, char = game
    encounter = next(e for e in load_region_encounters('iron_coast') if e['id'] == 'smuggler_cache')
    repos['save_game'].update_location('g', 'tide_caves')
    monkeypatch.setattr('text_rpg.mechanics.encounters.roll_encounter', lambda *args: encounter)
    loop = make_loop()
    context = loop._build_context('g')
    result = ActionResult(events=[{'event_type': 'SEARCH', 'description': 'searched'}],
                          outcome_description='You search the chamber.')
    loop._maybe_spawn_encounter(result, context, 'g', 1)
    location = repos['location'].get('tide_caves', 'g')
    assert location['properties']['active_puzzle']['encounter_id'] == 'smuggler_cache'
    assert 'Smuggler\'s Cache' in result.outcome_description
    assert 'Obstacle:' in loop.process_turn('look', 'g').narrative

    repos['world_state'].update_inventory('inv', [{'item_id': 'thieves_tools', 'quantity': 1}])
    monkeypatch.setattr('text_rpg.mechanics.puzzles.evaluate_puzzle_attempt', lambda **kwargs: {
        'success': True, 'dc': 14, 'skill_used': 'sleight_of_hand', 'roll_result': None,
        'description': 'The mechanism opens.',
    })
    before_xp = repos['character'].get(char['id'])['xp']
    solved = loop.process_turn('pick lock', 'g')
    assert solved.consumes_turn and any(e['event_type'] == 'PUZZLE_SOLVED' for e in solved.events)
    location = repos['location'].get('tide_caves', 'g')
    assert 'active_puzzle' not in location['properties']
    assert location['properties']['puzzle_states']['smuggler_cache']['solved']
    assert repos['character'].get(char['id'])['xp'] > before_xp
    after_xp = repos['character'].get(char['id'])['xp']
    repeat = loop.process_turn('pick lock', 'g')
    assert not repeat.consumes_turn and 'no unresolved puzzle' in repeat.narrative.lower()
    assert repos['character'].get(char['id'])['xp'] == after_xp


def test_puzzle_missing_tool_does_not_consume_attempt(game, monkeypatch):
    repos, make_loop, _ = game
    encounter = next(e for e in load_region_encounters('iron_coast') if e['id'] == 'smuggler_cache')
    repos['save_game'].update_location('g', 'tide_caves')
    props = repos['location'].get('tide_caves', 'g')['properties']
    props['active_puzzle'] = {'encounter_id': encounter['id'], 'name': encounter['name'],
                              'puzzle_type': 'lock', 'description': encounter['description']}
    props['puzzle_states'] = {}
    repos['location'].update_field('tide_caves', 'g', 'properties', props)
    result = make_loop().process_turn('pick lock', 'g')
    assert not result.consumes_turn and 'need thieves tools' in result.narrative.lower()
    assert not repos['location'].get('tide_caves', 'g')['properties']['puzzle_states']


def test_puzzle_failures_are_bounded_per_level(game, monkeypatch):
    repos, make_loop, _ = game
    encounter = next(e for e in load_region_encounters('iron_coast') if e['id'] == 'smuggler_cache')
    repos['save_game'].update_location('g', 'tide_caves')
    props = repos['location'].get('tide_caves', 'g')['properties']
    props['active_puzzle'] = {'encounter_id': encounter['id'], 'name': encounter['name'],
                              'puzzle_type': 'lock', 'description': encounter['description']}
    props['puzzle_states'] = {}
    repos['location'].update_field('tide_caves', 'g', 'properties', props)
    repos['world_state'].update_inventory('inv', [{'item_id': 'thieves_tools', 'quantity': 1}])
    monkeypatch.setattr('text_rpg.mechanics.puzzles.evaluate_puzzle_attempt', lambda **kwargs: {
        'success': False, 'dc': 14, 'skill_used': 'sleight_of_hand', 'roll_result': None,
        'description': 'The lock resists.',
    })
    loop = make_loop()
    assert all(loop.process_turn('pick lock', 'g').consumes_turn for _ in range(3))
    blocked = loop.process_turn('pick lock', 'g')
    assert not blocked.consumes_turn and 'current level' in blocked.narrative


def test_pressure_survives_reload_and_observation_never_adds_it(game):
    repos, make_loop, _ = game
    loop = make_loop()
    for _ in range(12):
        assert not loop.process_turn('look', 'g').consumes_turn
    assert repos['world_state'].get_event_cooldown('g', 'encounter_pressure_smuggler_den') == 0
    for turn in range(3):
        # Exercise the encounter gate with a completed time-consuming action.
        ctx = loop._build_context('g')
        loop._maybe_spawn_encounter(ActionResult(), ctx, 'g', turn)
        repos['save_game'].db.close(); loop = make_loop()
    assert repos['world_state'].get_active_combat('g')
    count = len(repos['entity'].get_by_location('g', 'smuggler_den'))
    loop._maybe_spawn_encounter(ActionResult(), loop._build_context('g'), 'g', 30)
    assert len(repos['entity'].get_by_location('g', 'smuggler_den')) == count


def test_enemy_winning_initiative_hands_control_back_to_player(game, monkeypatch):
    repos, make_loop, char = game
    search_roll(monkeypatch, False, 8)
    encounter = next(e for e in load_region_encounters('iron_coast') if e['id'] == 'smuggler_lookout')
    monkeypatch.setattr('text_rpg.mechanics.encounters.roll_encounter', lambda *args: encounter)
    values = iter([1, 20])
    monkeypatch.setattr('text_rpg.systems.combat.system.initiative_roll', lambda bonus: SimpleNamespace(total=next(values)))
    monkeypatch.setattr('text_rpg.systems.combat.system.attack_roll', lambda *args: (False, False, SimpleNamespace(individual_rolls=[2], total=5)))
    loop = make_loop()
    result = loop.process_turn('investigate', 'g')
    assert 'enemy has initiative' in result.narrative
    assert not any(e['event_type'] == 'ATTACK' for e in result.events)
    assert repos['character'].get(char['id'])['hp_current'] == char['hp_current']
    combat = repos['world_state'].get_active_combat('g')
    assert combat['turn_order'][combat['current_turn_index']] == char['id']
    from text_rpg.engine.validators import validate_action
    from text_rpg.models.action import Action
    assert validate_action(Action('flee', char['id']), loop._build_context('g'))[0]


def test_pending_opening_survives_reload_and_enemies_act_once_after_choice(game, monkeypatch):
    repos, make_loop, char = game
    search_roll(monkeypatch, False, 8)
    encounter = next(e for e in load_region_encounters('iron_coast') if e['id'] == 'smuggler_lookout')
    monkeypatch.setattr('text_rpg.mechanics.encounters.roll_encounter', lambda *args: encounter)
    values = iter([1, 20])
    monkeypatch.setattr('text_rpg.systems.combat.system.initiative_roll', lambda bonus: SimpleNamespace(total=next(values)))
    attacks = Mock(return_value=(False, False, SimpleNamespace(individual_rolls=[2], total=5)))
    monkeypatch.setattr('text_rpg.systems.combat.system.attack_roll', attacks)
    monkeypatch.setattr('text_rpg.systems.combat.system.npc_choose_action', lambda *args: {'action': 'attack'})
    loop = make_loop()
    loop.process_turn('investigate', 'g')
    attacks.assert_not_called()
    repos['save_game'].db.close(); loop = make_loop()
    loop.process_turn('look', 'g')
    loop.process_turn('2', 'g')  # Opening the spell menu is not a committed action.
    attacks.assert_not_called()
    result = loop.process_turn('dodge', 'g', 'opening-choice')
    attacks.assert_called_once()
    assert 'Dodge action' in result.narrative
    assert not next(c for c in repos['world_state'].get_active_combat('g')['combatants'] if c['entity_id'] == char['id'])['opening_pending']
    loop.process_turn('dodge', 'g', 'opening-choice')
    attacks.assert_called_once()


def test_lethal_opening_stops_remaining_foes_and_does_not_offer_dead_player_turn(game, monkeypatch):
    repos, make_loop, char = game
    repos['character'].update_field(char['id'], 'hp_current', 7)
    search_roll(monkeypatch, False, 8)
    import copy
    encounter = copy.deepcopy(next(e for e in load_region_encounters('iron_coast') if e['id'] == 'smuggler_lookout'))
    encounter['entities'][0].update(count_min=2, count_max=2)
    monkeypatch.setattr('text_rpg.mechanics.encounters.roll_encounter', lambda *args: encounter)
    values = iter([1, 20, 19])
    monkeypatch.setattr('text_rpg.systems.combat.system.initiative_roll', lambda bonus: SimpleNamespace(total=next(values)))
    attacks = Mock(return_value=(True, False, SimpleNamespace(individual_rolls=[15], total=18)))
    monkeypatch.setattr('text_rpg.systems.combat.system.attack_roll', attacks)
    monkeypatch.setattr('text_rpg.systems.combat.system.damage_roll', lambda *args: SimpleNamespace(total=7, individual_rolls=[6]))
    monkeypatch.setattr('text_rpg.systems.combat.system.npc_choose_action', lambda *args: {'action': 'attack'})
    loop = make_loop()
    loop.process_turn('investigate', 'g')
    assert repos['character'].get(char['id'])['hp_current'] == 7
    attacks.assert_not_called()
    result = loop.process_turn('dodge', 'g')
    attacks.assert_called_once()
    assert not repos['world_state'].get_active_combat('g')
    assert 'Your turn' not in result.narrative and 'Dodge action' not in result.narrative
    assert 'darkness claims you' in result.narrative
