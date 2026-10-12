from unittest.mock import Mock

import pytest

from text_rpg.llm.errors import LLMMalformedOutputError, LLMUnavailableError
from text_rpg.llm.schemas import LocationProposal
from text_rpg.systems.director.generators import generate_location
from text_rpg.systems.director.director import Director
from tests.test_engine.test_exploration_pressure import game


PROPOSAL = {'name': 'Stone Shelf', 'description': 'Weathered stone rises over a dry gully.',
            'location_type': 'wilderness', 'atmosphere': 'Quiet and windswept',
            'discovery': 'Layers of old flood sediment lie beneath the surface dust.'}


def test_truncated_location_retries_once_with_schema_and_makes_real_discovery(game):
    _, make_loop, _ = game
    context = make_loop()._build_context('g')
    llm = Mock()
    llm.generate_structured.side_effect = [LLMMalformedOutputError('truncated'), PROPOSAL]
    result = generate_location(llm, context, 'north', context.location)
    assert result['name'] == 'Stone Shelf'
    assert result['properties']['discoveries'][0]['description'] == PROPOSAL['discovery']
    assert result['properties']['generation_source'] == 'model'
    assert len(llm.generate_structured.call_args_list) == 2
    assert all(c.kwargs['schema'] is LocationProposal and c.kwargs['retries'] == 0
               for c in llm.generate_structured.call_args_list)


@pytest.mark.parametrize('invalid', [None, {'id': 'smuggler_den'}, {'items': ['free_diamond']}, {'name': ''}])
def test_unavailable_or_invalid_model_cannot_overwrite_existing_world(game, invalid):
    repos, make_loop, _ = game
    context = make_loop()._build_context('g')
    before = repos['location'].get('smuggler_den', 'g')
    llm = Mock()
    if invalid is None:
        llm.generate_structured.side_effect = LLMUnavailableError('offline')
    else:
        llm.generate_structured.return_value = {**PROPOSAL, **invalid}
    location = Director(llm, None, None).generate_location_for_direction('north', context, repos)
    assert location['id'] != 'smuggler_den'
    assert repos['location'].get('smuggler_den', 'g') == before
    assert location['properties']['generation_source'] == 'fallback'
    assert not location['items']
