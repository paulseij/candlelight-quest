from text_rpg.mechanics.weather import current_weather


def test_weather_is_stable_within_block_and_changes_only_from_world_inputs():
    first = current_weather('game', 'iron_coast', 'maritime', 480)
    assert first == current_weather('game', 'iron_coast', 'maritime', 719)
    assert first['name']
    assert set(first) == {'name', 'severe', 'climb_dc', 'track_dc', 'search_dc'}


def test_severe_weather_has_real_exploration_costs():
    severe_names = {'rain squall', 'gale winds', 'thunderstorm', 'whiteout', 'sandstorm',
                    'thick fog', 'heavy ashfall', 'reality storm'}
    samples = [current_weather('g', 'r', climate, block * 360)
               for climate in ('maritime_stormy', 'arctic', 'arid', 'swamp_dark', 'volcanic', 'planar')
               for block in range(40)]
    assert any(weather['name'] in severe_names and weather['climb_dc'] >= 4 for weather in samples)
