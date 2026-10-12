"""Playable scene contracts tested through saved turns, without inference."""
from io import StringIO
from types import SimpleNamespace
from unittest.mock import Mock

from rich.console import Console
from text_rpg.cli.combat_display import CombatDisplay
from text_rpg.engine.scene_actions import SceneActions
from text_rpg.engine.scene_detail import details, scene_summary
from text_rpg.models.action import Action
from tests.test_engine.test_exploration_pressure import game


def test_combat_panel_has_no_literal_markup_or_wrong_opening_label():
    out = StringIO()
    display = CombatDisplay()
    display.console = Console(file=out, width=60, color_system=None)
    display.show_combat_menu({'round_number': 1, 'combatants': [{'opening_pending': True}]},
                             {'hp_current': 12, 'hp_max': 12, 'char_class': 'paladin'},
                             [{'name': 'Magma Mephit', 'hp': {'current': 22, 'max': 22}}])
    rendered = out.getvalue()
    assert '[green]' not in rendered and '[bold]' not in rendered and '[cyan bold]' not in rendered
    assert 'Choose your opening action' in rendered and 'Your Turn' not in rendered
    assert '[6] Lay on Hands' in rendered and '22/22' in rendered


def test_targeted_search_persists_without_repeated_rolls(game, monkeypatch):
    from text_rpg.content.region_seeder import seed_region
    repos, make_loop, char = game
    seed_region('ashen_highlands', 'g', repos)
    repos['save_game'].update_location('g', 'lava_tubes')
    roll = Mock(return_value=(True, SimpleNamespace(individual_rolls=[15], modifier=3, total=18)))
    monkeypatch.setattr('text_rpg.engine.scene_actions.skill_check', roll)
    loop = make_loop()
    before = repos['save_game'].get_game('g')['world_time']
    assert 'glassy wall' in loop.process_turn('look', 'g').narrative
    assert 'Where do you want' in loop.process_turn('search', 'g').narrative
    assert repos['save_game'].get_game('g')['world_time'] == before
    result = loop.process_turn('search behind the glassy wall', 'g')
    assert result.consumes_turn and 'Soot is rubbed' in result.narrative
    repos['save_game'].db.close()
    repeat = make_loop().process_turn('search glassy wall', 'g')
    assert not repeat.consumes_turn and 'already searched' in repeat.narrative
    roll.assert_called_once()


def test_unknown_search_target_is_not_a_generic_reward(game):
    repos, make_loop, _ = game
    result = make_loop().process_turn('search behind the imaginary tree', 'g')
    assert not result.consumes_turn
    assert 'cannot identify' in result.narrative


def test_contextual_options_only_offer_real_current_actions(game):
    repos, make_loop, _ = game
    campaign = __import__('text_rpg.engine.campaign', fromlist=['CampaignRuntime']).CampaignRuntime(repos)
    assert campaign.ensure('g')
    result = make_loop().process_turn('what can I do here?', 'g')
    assert not result.consumes_turn
    assert 'Things you can do here now:' in result.narrative
    assert '`search behind ' in result.narrative
    assert '`leads`' in result.narrative
    assert '`hunt`' in result.narrative


def test_hunt_starts_encounter_without_a_model_and_observation_is_free(game):
    repos, make_loop, _ = game
    loop = make_loop()
    danger = loop.process_turn('danger', 'g')
    assert not danger.consumes_turn
    assert 'Reachable combat zones:' in danger.narrative
    assert 'you are here' in danger.narrative and 'Smuggler' in danger.narrative
    assert 'for you' in danger.narrative
    result = loop.process_turn('hunt', 'g')
    assert result.consumes_turn
    assert repos['world_state'].get_active_combat('g')['is_active']


def test_personal_action_leaves_trace_but_grants_nothing(game):
    repos, make_loop, char = game
    ctx = make_loop()._build_context('g')
    ctx.location['location_type'] = 'road'
    llm = Mock()
    llm.generate_structured.side_effect = AssertionError('ordinary actions should be immediate')
    scene = SceneActions(repos, llm)
    result = scene.propose(Action('custom', char['id'], raw_input='I poop on the road'), ctx)
    assert result.success and not result.dice_rolls and not result.xp_gained
    assert result.state_mutations[0].new_value['player_traces'][0]['kind'] == 'relieve_self'
    assert result.events[0]['event_type'] == 'PERSONAL_ACTION'


def test_search_reward_is_real_finite_and_collectible(game, monkeypatch):
    from text_rpg.content.region_seeder import seed_region
    repos, make_loop, char = game
    seed_region('ashen_highlands', 'g', repos)
    repos['save_game'].update_location('g', 'old_mine_shaft')
    monkeypatch.setattr('text_rpg.engine.scene_actions.skill_check', lambda *args: (True, SimpleNamespace(individual_rolls=[18], modifier=3, total=21)))
    loop = make_loop()
    found = loop.process_turn('search behind warning signs', 'g')
    assert found.consumes_turn
    assert repos['location'].get('old_mine_shaft', 'g')['items'].count('iron_ingot') == 1
    picked = loop.process_turn('pick up iron ingot', 'g')
    assert picked.consumes_turn
    inventory = repos['world_state'].get_inventory(char['id'], 'g')['items']
    assert any(i['item_id'] == 'iron_ingot' and i['quantity'] == 1 for i in inventory)
    again = loop.process_turn('search warning signs', 'g')
    assert not again.consumes_turn
    assert 'iron_ingot' not in repos['location'].get('old_mine_shaft', 'g')['items']


def test_every_authored_location_has_a_real_interaction_floor():
    from text_rpg.content.loader import load_all_regions, load_region
    allowed = {'inspect','search','touch','listen','smell','read','sit','kneel','climb','track',
               'wade','knock','hide_behind','open','close','unlock','lock','attach','detach',
               'traverse','transfer','consume','damage','repair','mark','clean','reveal','distract'}
    locations = [location for region_id in load_all_regions()
                 for location in load_region(region_id).get('locations', [])]
    assert len(locations) == 421
    for location in locations:
        features = details(location)
        assert 3 <= len(features) <= 6, location['id']
        assert len({feature['name'] for feature in features}) == len(features), location['id']
        assert all(set(feature['state']['operations']) <= allowed for feature in features), location['id']
        assert all('inspect' in feature['state']['operations'] for feature in features), location['id']
        summary = scene_summary(location, features)
        assert 'Activity:' in summary and 'Visible features:' in summary and 'Try:' in summary


def test_sensory_and_physical_feature_actions_bypass_model(game):
    from text_rpg.content.region_seeder import seed_region
    repos, make_loop, _ = game
    seed_region('ashen_highlands', 'g', repos)
    repos['save_game'].update_location('g', 'lava_tubes')
    loop = make_loop()
    before = repos['save_game'].get_game('g')['world_time']
    heard = loop.process_turn('listen to floor cracks', 'g')
    assert 'listen' in heard.narrative and not heard.consumes_turn
    assert repos['save_game'].get_game('g')['world_time'] == before
    sat = loop.process_turn('sit on glassy wall', 'g')
    assert sat.consumes_turn and 'settle' in sat.narrative
    assert repos['save_game'].get_game('g')['world_time'] == before + 10


def test_freeform_creative_action_is_forwarded_to_bounded_adjudicator(game):
    repos, make_loop, char = game
    ctx = make_loop()._build_context('g')
    llm = Mock()
    llm.scene_temperature = 0
    llm.scene_max_tokens = 128
    llm.generate_structured.return_value = {'operation': 'creative', 'object_id': None,
                                             'item_id': None, 'person_reference': None,
                                             'personal_action': None}
    result = SceneActions(repos, llm).propose(Action('custom', char['id'], raw_input='I dig a drainage trench'), ctx)
    assert isinstance(result, Action)
    assert result.action_type == 'creative' and result.parameters['_interpreted']
    llm.generate_structured.assert_not_called()


def test_witnessed_mess_persists_and_changes_npc_opinion(game):
    repos, make_loop, char = game
    repos['save_game'].update_location('g', 'saltmere_docks')
    ctx = make_loop()._build_context('g')
    witness = next(entity for entity in ctx.entities if entity.get('entity_type') == 'npc')
    before = repos['reputation'].get_npc_rep('g', witness['id'])
    result = SceneActions(repos, Mock()).propose(Action('custom', char['id'], raw_input='I poop on the ground'), ctx)
    assert result.events[0]['event_type'] == 'SOCIAL_OFFENSE'
    assert repos['reputation'].get_npc_rep('g', witness['id']) == before - 1
    assert repos['entity'].get(witness['id'], 'g')['properties']['relationships']['player']['disposition'] == 'suspicious'
    assert result.state_mutations[0].new_value['player_traces'][-1]['kind'] == 'relieve_self'


def test_scene_damage_and_marks_survive_reload(game):
    repos, make_loop, _ = game
    loop = make_loop()
    damaged = loop.process_turn('break crate', 'g')
    assert damaged.consumes_turn and 'remains intact' in damaged.narrative
    marked = loop.process_turn('carve crate', 'g')
    assert marked.consumes_turn and 'visible mark' in marked.narrative
    repos['save_game'].db.close()
    inspected = make_loop().process_turn('inspect crate', 'g')
    assert 'visible damage' in inspected.narrative and 'player-made mark' in inspected.narrative


def test_scene_damage_can_be_repaired_with_real_tools(game, monkeypatch):
    repos, make_loop, _ = game
    loop = make_loop()
    loop.process_turn('break crate', 'g')
    crate_id = next(obj['id'] for obj in SceneActions(repos, None).objects(loop._build_context('g'))
                    if obj['name'] == 'crate')
    with repos['save_game'].db.get_connection() as conn:
        damaged = __import__('json').loads(conn.execute(
            'SELECT state FROM scene_objects WHERE game_id=? AND id=?', ('g', crate_id)).fetchone()[0])['hp']
    repos['world_state'].update_inventory('inv', [{'item_id': 'woodcarvers_tools', 'quantity': 1}])
    monkeypatch.setattr('text_rpg.engine.scene_actions.skill_check',
                        lambda *args, **kwargs: (True, SimpleNamespace(individual_rolls=[15], modifier=3, total=18)))
    repaired = loop.process_turn('repair crate', 'g')
    assert repaired.consumes_turn and 'restore' in repaired.narrative
    with repos['save_game'].db.get_connection() as conn:
        restored = __import__('json').loads(conn.execute(
            'SELECT state FROM scene_objects WHERE game_id=? AND id=?', ('g', crate_id)).fetchone()[0])['hp']
    assert restored > damaged


def test_player_can_remove_persistent_mess_and_marks(game):
    repos, make_loop, char = game
    loop = make_loop()
    ctx = loop._build_context('g')
    scene = SceneActions(repos, Mock())
    mess = scene.propose(Action('custom', char['id'], raw_input='I poop on the ground'), ctx)
    loop._apply_mutations(mess.state_mutations, 'g')
    cleaned = scene.propose(Action('custom', char['id'], raw_input='clean up the mess'), loop._build_context('g'))
    loop._apply_mutations(cleaned.state_mutations, 'g')
    assert cleaned.success and not repos['location'].get('smuggler_den', 'g')['properties']['player_traces']

    assert loop.process_turn('carve crate', 'g').consumes_turn
    wiped = loop.process_turn('clean crate', 'g')
    assert wiped.consumes_turn and 'clean the player-made marks' in wiped.narrative
    assert 'player-made mark' not in loop.process_turn('inspect crate', 'g').narrative


def test_durable_freeform_change_is_grounded_and_rendered():
    from text_rpg.engine.scene_detail import durable_scene_change
    change = durable_scene_change('I dig a drainage channel beside the wharf', 'Kami', 'a1', 510, [], 'A wooden wharf')
    assert change and change['id'] == 'change:a1'
    location = {'id': 'dock', 'description': 'A wharf.', 'location_type': 'settlement',
                'properties': {'scene_changes': [change]}}
    assert 'Kami: I dig a drainage channel beside the wharf' in scene_summary(location, details(location))
    assert durable_scene_change('I summon treasure', 'Kami', 'a2', 510, []) is None
    assert durable_scene_change('I juggle stones', 'Kami', 'a3', 510, []) is None
    assert durable_scene_change('I redirect the stream', 'Kami', 'a4', 510, [], 'A dry wharf') is None
