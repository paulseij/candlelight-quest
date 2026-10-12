from tests.test_engine.test_exploration_pressure import game


def test_long_rest_takes_eight_hours_and_cannot_recharge_repeatedly(game):
    repos, make_loop, char = game
    repos['save_game'].update_location('g', 'saltmere_tavern')
    repos['character'].update_field(char['id'], 'spellcasting_ability', 'wisdom')
    repos['character'].update_field(char['id'], 'spell_slots_max', {'1': 2})
    repos['character'].update_field(char['id'], 'spell_slots_remaining', {'1': 0})
    loop = make_loop()
    before = repos['save_game'].get_game('g')['world_time']
    assert loop.process_turn('rest long', 'g').consumes_turn
    after = before + 480
    assert repos['save_game'].get_game('g')['world_time'] == after
    assert repos['character'].get(char['id'])['spell_slots_remaining'] == {'1': 2}
    repos['character'].update_field(char['id'], 'spell_slots_remaining', {'1': 0})
    repos['save_game'].db.close()
    result = make_loop().process_turn('rest long', 'g')
    assert not result.consumes_turn and '16h' in result.narrative
    assert repos['save_game'].get_game('g')['world_time'] == after
    assert repos['character'].get(char['id'])['spell_slots_remaining'] == {'1': 0}
    repos['save_game'].update_world_time('g', after + 960)
    assert make_loop().process_turn('rest long', 'g').consumes_turn
    assert repos['character'].get(char['id'])['spell_slots_remaining'] == {'1': 2}


def test_short_rest_recovery_without_hit_dice_is_once_per_long_rest(game):
    repos, make_loop, char = game
    repos['save_game'].update_location('g', 'saltmere_tavern')
    for field, value in {'char_class': 'wizard', 'spellcasting_ability': 'intelligence',
                         'hit_dice_remaining': 0, 'spell_slots_max': {'1': 2},
                         'spell_slots_remaining': {'1': 0}}.items():
        repos['character'].update_field(char['id'], field, value)
    before = repos['save_game'].get_game('g')['world_time']
    assert make_loop().process_turn('rest short', 'g').consumes_turn
    assert repos['save_game'].get_game('g')['world_time'] == before + 60
    assert repos['character'].get(char['id'])['spell_slots_remaining'] == {'1': 1}
    repos['character'].update_field(char['id'], 'spell_slots_remaining', {'1': 0})
    repos['save_game'].db.close()
    assert make_loop().process_turn('rest short', 'g').consumes_turn
    assert repos['character'].get(char['id'])['spell_slots_remaining'] == {'1': 0}
    assert repos['character'].get(char['id'])['hit_dice_remaining'] == 0
    assert make_loop().process_turn('rest long', 'g').consumes_turn
    repos['character'].update_field(char['id'], 'spell_slots_remaining', {'1': 0})
    assert make_loop().process_turn('rest short', 'g').consumes_turn
    assert repos['character'].get(char['id'])['spell_slots_remaining'] == {'1': 1}


def test_hostile_presence_prevents_free_rest(game):
    repos, make_loop, char = game
    repos['save_game'].update_location('g', 'saltmere_docks')
    repos['entity'].update_field('harbormaster_kael', 'is_hostile', True)
    before = repos['character'].get(char['id'])
    result = make_loop().process_turn('rest long', 'g')
    assert not result.consumes_turn and 'hostile' in result.narrative
    assert repos['character'].get(char['id']) == before
