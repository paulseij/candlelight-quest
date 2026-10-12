"""Engine contracts for model-selected intents; model accuracy is tested separately."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from text_rpg.cli.input_handler import InputHandler
from text_rpg.engine.action_dispatcher import ActionDispatcher
from text_rpg.engine.scene_actions import SceneActions
from text_rpg.engine.turn_loop import TurnLoop
from text_rpg.llm.errors import LLMUnavailableError
from text_rpg.models.action import Action, ActionResult
from text_rpg.systems.base import GameContext


@pytest.fixture
def scene(in_memory_db):
    ctx = GameContext('g', {'id': 'p'}, {'id': 'dock', 'name': 'Docks', 'description': 'A wooden wharf.',
        'connections': [{'direction': 'east', 'target_location_id': 'village', 'description': 'A coastal path.'}]}, [
        {'id': 'mora', 'name': 'Captain Mora', 'profession': 'merchant_captain', 'entity_type': 'npc', 'description': 'A tall elven captain.'},
        {'id': 'sal', 'name': 'Barkeep Sal', 'profession': 'barkeep', 'entity_type': 'npc'},
    ])
    llm = Mock()
    llm.scene_temperature = 0
    llm.scene_max_tokens = 256
    repos = {'save_game': SimpleNamespace(db=in_memory_db)}
    return SceneActions(repos, llm), ctx


def proposal(scene, text, operation, target=None, reference=None):
    interpreter, ctx = scene
    interpreter.llm.generate_structured.return_value = {'operation': operation, 'object_id': target,
        'item_id': None, 'person_reference': reference}
    return interpreter.propose(Action('custom', 'p', raw_input=text), ctx)


@pytest.mark.parametrize('text', ['I could use someone to chat with', 'any freindly faces about', 'Who is working on these docks?', 'Could you point me towards some company?'])
def test_indirect_people_queries_have_no_world_effects(scene, text):
    result = proposal(scene, text, 'list_people')
    assert 'Captain Mora' in result.outcome_description and 'Barkeep Sal' in result.outcome_description
    assert not result.events and not result.state_mutations and not result.dice_rolls


def test_roles_in_model_packet_and_filtered_results(scene):
    result = proposal(scene, 'Any captains around?', 'list_people', reference='captain')
    assert 'Captain Mora' in result.outcome_description and 'Barkeep Sal' not in result.outcome_description
    packet = json.loads(scene[0].llm.generate_structured.call_args.args[0])
    assert packet['entities'][0]['profession'] == 'merchant_captain'


def test_unknown_role_does_not_create_person(scene):
    result = proposal(scene, 'Is there a wizard nearby?', 'list_people', reference='wizard')
    assert 'Nobody here matches' in result.outcome_description
    assert 'Captain Mora' in result.outcome_description


def test_conversation_by_role_uses_existing_id(scene):
    result = proposal(scene, 'Let me speak to the captain', 'talk', 'mora', 'captain')
    assert isinstance(result, Action) and result.target_id == 'mora'


def test_invented_id_refused(scene):
    result = proposal(scene, 'Let me speak to the captain', 'talk', 'invented', 'captain')
    assert isinstance(result, ActionResult) and not result.events


def test_model_cannot_pick_one_of_two_captains(scene):
    scene[1].entities.append({'id': 'kael', 'name': 'Captain Kael', 'profession': 'captain', 'entity_type': 'npc'})
    result = proposal(scene, 'Let me speak to the captain', 'talk', 'mora', 'Captain Mora')
    assert isinstance(result, ActionResult)
    assert 'Captain Mora' in result.outcome_description and 'Captain Kael' in result.outcome_description


def test_person_inspection_returns_authored_description(scene):
    result = proposal(scene, 'What does the captain look like?', 'inspect_person', 'mora', 'captain')
    assert result.outcome_description == 'Captain Mora: A tall elven captain.'
    assert not result.events


def test_exit_query_does_not_move(scene):
    result = proposal(scene, 'Where could I head next?', 'list_exits')
    assert 'east: A coastal path.' in result.outcome_description
    assert not result.events and not result.state_mutations


def test_unsupported_request_clarifies_without_fabricating(scene):
    result = proposal(scene, "What is the captain's secret bank balance?", 'unsupported')
    assert 'cannot resolve' in result.outcome_description and not result.events


def test_unknown_question_reaches_interpreter_and_failure_is_helpful(scene):
    interpreter, ctx = scene
    loop = object.__new__(TurnLoop)
    loop.input_handler = InputHandler()
    action = loop._normalize_input('Any friendly faces about?', ctx)
    assert action.action_type == 'custom'
    interpreter.llm.generate_structured.side_effect = LLMUnavailableError('offline')
    result = ActionDispatcher(Mock(), SimpleNamespace(llm=interpreter.llm), interpreter.repos).dispatch(action, ctx)
    assert 'could not interpret' in result.outcome_description
    assert 'Captain Mora' in result.outcome_description and not result.events


def test_direct_named_conversation_bypasses_interpreter(scene):
    interpreter, ctx = scene
    registry = Mock()
    registry.find_system_for_action.return_value.resolve.return_value = ActionResult()
    ActionDispatcher(registry, SimpleNamespace(llm=interpreter.llm), interpreter.repos).dispatch(
        Action('talk', 'p', target_id='the captain', raw_input='talk to the captain'), ctx)
    resolved = registry.find_system_for_action.return_value.resolve.call_args.args[0]
    assert resolved.target_id == 'mora'
    interpreter.llm.generate_structured.assert_not_called()
