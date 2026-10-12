from text_rpg.engine.exits import exit_options
from text_rpg.models.action import Action
from text_rpg.systems.base import GameContext
from text_rpg.systems.exploration.system import ExplorationSystem


def context():
    return GameContext('g', {'id':'p'}, {'id':'road','connections':[
        {'direction':'north','target_location_id':'gates','description':'Fortified gates.'},
        {'direction':'west','target_location_id':'plateau'},
        {'direction':'north','target_location_id':'ash_pass','description':'Ash-filled pass.'}]}, [])


def test_direction_requires_choice_without_moving():
    result = ExplorationSystem().resolve(Action('move','p',target_id='north'),context())
    assert not result.state_mutations and not result.events
    assert 'go north 1' in result.outcome_description and 'go north 2' in result.outcome_description


def test_numbered_exit_selects_second_path():
    result = ExplorationSystem().resolve(Action('move','p',target_id='north 2'),context())
    assert result.state_mutations[0].new_value == 'ash_pass'


def test_unique_direction_and_destination_still_work():
    for target, expected in [('west','plateau'), ('ash pass','ash_pass')]:
        result = ExplorationSystem().resolve(Action('move','p',target_id=target),context())
        assert result.state_mutations[0].new_value == expected


def test_look_uses_same_numbering_and_duplicates_are_removed():
    ctx=context()
    ctx.location['connections'].append(dict(ctx.location['connections'][0]))
    assert [label for label,_ in exit_options(ctx.location['connections'])] == ['north 1','north 2','west']
    assert 'north 2' in ExplorationSystem().resolve(Action('look','p'),ctx).outcome_description


def test_many_exits_grouped_and_numbered_without_four_exit_limit():
    connections = [{'direction': direction, 'target_location_id': str(i)} for i, direction in enumerate(
        ['west', 'north', 'up', 'east', 'north', 'northeast', 'west', 'south', 'down', 'northwest', 'southeast', 'southwest'])]
    labels = [label for label, _ in exit_options(connections)]
    assert labels == ['north 1', 'north 2', 'northeast', 'east', 'southeast', 'south', 'southwest', 'west 1', 'west 2', 'northwest', 'up', 'down']


def test_numbered_direction_shortcuts():
    from text_rpg.cli.input_handler import InputHandler
    handler = InputHandler()
    for command in ('north 2', 'n 2', 'go north 2', 'go n 2'):
        result = handler.classify(command)
        assert result['action_type'] == 'move' and result['target'] == 'north 2'
