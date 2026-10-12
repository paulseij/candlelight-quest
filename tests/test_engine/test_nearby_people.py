from unittest.mock import Mock

import pytest

from text_rpg.cli.input_handler import InputHandler
from text_rpg.engine.action_dispatcher import ActionDispatcher
from text_rpg.engine.turn_loop import TurnLoop
from text_rpg.systems.base import GameContext


def context(entities):
    return GameContext('game', {'id': 'hero'}, {'name': 'Docks', 'description': 'Dockhands shout.'}, entities)


def resolve(text, ctx):
    loop = object.__new__(TurnLoop)
    loop.input_handler = InputHandler()
    action = loop._normalize_input(text, ctx)
    registry = Mock()
    result = ActionDispatcher(registry).dispatch(action, ctx)
    registry.find_system_for_action.assert_not_called()
    assert not result.events and not result.state_mutations and not result.dice_rolls
    return result.outcome_description


@pytest.mark.parametrize('text', ['who is out here?', 'who is here', "who's nearby?", 'who can I talk to?', 'I want to talk to someone?', 'talk to anyone'])
def test_lists_actual_people_without_model_or_turn_effects(text):
    from text_rpg.content.loader import load_region
    npcs = [npc for npc in load_region('iron_coast')['npcs'] if npc.get('location_id') == 'saltmere_docks']
    result = resolve(text, context(npcs))
    assert 'Harbormaster Kael' in result and 'Captain Mora' in result
    assert 'talk to Harbormaster Kael' in result
    assert "called 'someone'" not in result


def test_scenery_does_not_create_people():
    assert 'nobody here' in resolve('who is out here?', context([]))


def test_dead_npcs_excluded_and_unavailable_people_labelled():
    ctx = context([
        {'id': 'dead', 'name': 'Dead NPC', 'entity_type': 'npc', 'is_alive': False},
        {'id': 'busy', 'name': 'Busy NPC', 'entity_type': 'npc', 'unavailable_periods': ['morning']},
    ])
    result = resolve('talk to someone', ctx)
    assert 'Dead NPC' not in result
    assert 'Busy NPC (unavailable right now)' in result
    assert 'Nobody here is available' in result
