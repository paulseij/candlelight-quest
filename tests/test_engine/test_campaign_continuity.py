"""Real saved-turn campaign paths; no model required to progress or remember facts."""
import json
from unittest.mock import Mock

import pytest

from tests.test_engine.test_exploration_pressure import game
from text_rpg.content.region_seeder import seed_region
from text_rpg.engine.campaign import CampaignRuntime
from text_rpg.engine.continuity import Continuity
from text_rpg.engine.snapshots import StateSerializer
from text_rpg.mechanics.time_travel import RestoreConfig
from text_rpg.systems.world_sim import WorldSimulator
from text_rpg.content.loader import load_region_encounters
from text_rpg.mechanics.encounters import instantiate_encounter
from text_rpg.llm.errors import LLMMalformedOutputError
from text_rpg.systems.director.director import Director


def setup(game, region='iron_coast'):
    repos, make_loop, char = game
    if region != 'iron_coast':
        seed_region(region, 'g', repos)
        repos['save_game'].update_location('g', 'cinderhold_gate')
    campaign = CampaignRuntime(repos)
    assert campaign.ensure('g')
    loop = make_loop()
    # Campaign paths are tested independently of the already-covered random
    # encounter gate; this verifies progression without a model or combat.
    loop._maybe_spawn_encounter = lambda *args: None
    return repos, loop, char, campaign


def fund_ritual(repos, target):
    source = repos['entity'].get('captain_mora')
    repos['entity'].save({**source, 'id': 'ritual_caster', 'name': 'Ritual Caster',
        'location_id': target['location_id'], 'schedule': {}, 'level': 13, 'generated': False,
        'properties': {'revival_resources': {'method': 'temple_intercession', 'charges': 1,
            'materials_gp': 500, 'target_ids': [target['id']], 'ready_at': 0}}})


@pytest.mark.parametrize('region', ['iron_coast', 'ashen_highlands'])
@pytest.mark.parametrize('choice', ['expose', 'secure'])
def test_complete_campaign_choices_rewards_reload_and_no_reset(game, region, choice):
    repos, loop, char, campaign = setup(game, region)
    initial_gold = repos['character'].get(char['id'])['gold']
    for stage in range(2):
        state = campaign.get('g')
        assert state['stage'] == stage
        for loc, name, _ in state['template']['chapters'][stage]['sites'][:2]:
            repos['save_game'].update_location('g', loc)
            result = loop.process_turn('inspect ' + name, 'g')
            assert result.consumes_turn and 'Evidence recorded' in result.narrative
            assert not loop.process_turn('inspect ' + name, 'g').consumes_turn
            repos['save_game'].db.close()
        repos['save_game'].update_location('g', state['template']['hub'])
        result = loop.process_turn('resolve ' + choice, 'g', request_id=f'decision-{stage}')
        assert any(e['event_type'] == 'QUEST_COMPLETE' for e in result.events)
        assert not loop.process_turn('resolve ' + choice, 'g', request_id=f'decision-{stage}').consumes_turn
    assert campaign.get('g')['status'] == 'completed'
    assert campaign.get('g')['choices'] == [choice, choice]
    assert repos['character'].get(char['id'])['gold'] == initial_gold + 40
    assert not campaign.ensure('g')
    assert not loop.process_turn('resolve ' + choice, 'g').consumes_turn
    assert campaign.advance(loop._build_context('g')) == []
    with campaign.db.get_connection() as conn:
        rows = conn.execute("SELECT * FROM world_history WHERE game_id='g'").fetchall()
    ids = {r['id'] for r in rows}
    assert all(r['cause_id'] in ids for r in rows if r['cause_id'])
    assert len([r for r in rows if r['event_type'] == 'CAMPAIGN_COMPLETE']) == 1


def test_reads_invalid_actions_and_failed_investigation_do_not_block_evidence(game, monkeypatch):
    repos, loop, _, campaign = setup(game)
    before = repos['save_game'].get_game('g')['world_time']
    for command in ('leads', 'history', 'resolve expose', 'personal goal', 'look'):
        assert not loop.process_turn(command, 'g').consumes_turn
    assert repos['save_game'].get_game('g')['world_time'] == before
    repos['save_game'].update_location('g', 'saltmere_watchtower')
    repos['location'].update_field('saltmere_watchtower', 'g', 'properties', {'search_attempts': {'someone': 1}})
    assert loop.process_turn('inspect signal records', 'g').consumes_turn
    assert len(campaign.get('g')['evidence']) == 1


def test_pressure_only_once_and_after_death_records_still_work(game):
    repos, loop, _, campaign = setup(game)
    npc_id = 'harbormaster_kael'
    repos['entity'].update_field(npc_id, 'is_alive', False)
    event = campaign.event('DEATH', 'Harbormaster Kael died.', npc_id)
    loop._record_events([event], 'g', 0, loop._build_context('g'))
    repos['save_game'].update_world_time('g', 700)
    events = campaign.advance(loop._build_context('g'))
    assert sum(e['event_type'] == 'CAMPAIGN_CONSEQUENCE' for e in events) == 3
    assert campaign.advance(loop._build_context('g')) == []
    assert 'Harbormaster Kael — dead' in campaign.journal('g')
    assert 'dead; cannot speak' in Continuity(repos).facts('g', 'saltmere_docks')
    seed_region('iron_coast', 'g', repos)
    assert not repos['entity'].get(npc_id)['is_alive']
    with pytest.raises(ValueError, match='revival'):
        repos['entity'].update_field(npc_id, 'is_alive', True)
    with pytest.raises(ValueError, match='revival'):
        repos['entity'].save({**repos['entity'].get(npc_id), 'is_alive': True})
    repos['save_game'].update_location('g', 'saltmere_watchtower')
    assert loop.process_turn('inspect signal records', 'g').consumes_turn


def test_explicit_revival_retains_death_history(game):
    repos, loop, _, campaign = setup(game)
    npc_id = 'harbormaster_kael'
    repos['entity'].update_field(npc_id, 'is_alive', False)
    death = campaign.event('DEATH', 'Harbormaster Kael died.', npc_id)
    loop._record_events([death], 'g', 0, loop._build_context('g'))
    campaign.advance(loop._build_context('g'))
    with pytest.raises(ValueError):
        repos['entity'].revive(npc_id, 'g', hp=5, cause_event_id='missing')
    revival = campaign.event('REVIVAL', 'Harbormaster Kael was revived.', npc_id, cause_id=death['id'])
    with campaign.db.transaction():
        loop._record_events([revival], 'g', 1, loop._build_context('g'))
        repos['entity'].revive(npc_id, 'g', hp=5, cause_event_id=revival['id'])
    assert 'alive again' in campaign.advance(loop._build_context('g'))[0]['description']
    facts = Continuity(repos).facts('g', 'saltmere_docks')
    assert 'Kael: alive.' in facts and 'Kael died.' in facts and 'Kael was revived.' in facts


def test_snapshot_rewinds_evidence_deaths_and_history(game):
    repos, loop, _, campaign = setup(game)
    serializer = StateSerializer(repos)
    snapshot = serializer.capture('g', 'test')
    repos['save_game'].update_location('g', 'saltmere_watchtower')
    loop.process_turn('inspect signal records', 'g')
    repos['entity'].update_field('harbormaster_kael', 'is_alive', False)
    damaged = campaign.event('LOCATION_DESTROYED', 'The watchtower collapsed.', 'saltmere_watchtower')
    loop._record_events([damaged], 'g', 1, loop._build_context('g'))
    serializer.restore('g', snapshot, RestoreConfig(keep_player_stats=False, keep_inventory=False, keep_spells=False))
    assert campaign.get('g')['evidence'] == {}
    assert repos['entity'].get('harbormaster_kael')['is_alive']
    assert Continuity(repos).location_condition('g', 'saltmere_watchtower') is None
    with campaign.db.get_connection() as conn:
        assert conn.execute("SELECT count(*) FROM world_history WHERE event_type='CAMPAIGN_EVIDENCE'").fetchone()[0] == 0


def test_campaign_and_evidence_rollback_together(game):
    repos, loop, _, campaign = setup(game)
    repos['save_game'].update_location('g', 'saltmere_watchtower')
    original = loop._track_quest_progress
    loop._track_quest_progress = lambda *args: (_ for _ in ()).throw(RuntimeError('commit failed'))
    with pytest.raises(RuntimeError, match='commit failed'):
        loop.process_turn('inspect signal records', 'g')
    assert campaign.get('g')['evidence'] == {}
    loop._track_quest_progress = original
    assert loop.process_turn('inspect signal records', 'g').consumes_turn


def test_new_save_variants_are_frozen_after_first_introduction(game, monkeypatch):
    repos, _, _, campaign = setup(game)
    state = campaign.get('g')
    assert state['motive'] in state['template']['chapters'][1]['sites'][0][2]
    monkeypatch.setattr('text_rpg.engine.campaign.secrets.randbits', lambda _: 0)
    assert not campaign.ensure('g')
    assert campaign.get('g') == state


@pytest.mark.parametrize('region', [
    'cragmoor', 'crownhold', 'deadmarsh', 'drake_wastes', 'dusthollow', 'embercrown',
    'frostmere', 'greenvale', 'kingsward', 'mistwood', 'silverpeak', 'stormhaven',
    'sunken_reach', 'the_hollow', 'the_veil', 'thornwild', 'verdant_reach',
])
def test_every_other_region_gets_a_frozen_reachable_campaign(game, region):
    repos, _, _, = game
    seeded = seed_region(region, 'g', repos)
    repos['save_game'].update_location('g', seeded['entry_location_id'])
    campaign = CampaignRuntime(repos)
    assert campaign.ensure('g')
    state = campaign.get('g')
    assert state['region'] == region and len(state['template']['chapters']) == 2
    assert campaign.quest_id(state).startswith(f'campaign_{region}_')
    assert all(len(chapter['sites']) >= 2 for chapter in state['template']['chapters'])
    assert all(campaign.route('g', seeded['entry_location_id'], site[0]) is not None
               for chapter in state['template']['chapters'] for site in chapter['sites'])
    frozen = json.dumps(state, sort_keys=True)
    assert not campaign.ensure('g')
    assert json.dumps(campaign.get('g'), sort_keys=True) == frozen


def test_new_regional_campaigns_do_not_overwrite_each_others_quests(game):
    repos, _, _ = game
    first = CampaignRuntime(repos)
    assert first.ensure('g')
    first_id = first.quest_id(first.get('g'))
    seeded = seed_region('verdant_reach', 'g', repos)
    repos['save_game'].update_location('g', seeded['entry_location_id'])
    second = CampaignRuntime(repos)
    assert second.ensure('g')
    second_id = second.quest_id(second.get('g'))
    assert first_id != second_id
    assert repos['world_state'].get_quest(first_id, 'g')
    assert repos['world_state'].get_quest(second_id, 'g')


def test_destroy_and_rebuild_are_causal_and_close_services(game):
    repos, loop, _, campaign = setup(game)
    repos['save_game'].update_location('g', 'saltmere_docks')
    assert repos['shop'].get_shop_by_location('g', 'saltmere_docks')
    event = campaign.event('LOCATION_DESTROYED', 'Saltmere Docks was destroyed by a tidal surge.', 'saltmere_docks')
    loop._record_events([event], 'g', 0, loop._build_context('g'))
    assert Continuity(repos).location_condition('g', 'saltmere_docks')['condition'] == 'destroyed'
    assert loop._build_context('g').location['description'].startswith('This place was destroyed')
    assert campaign.decision_hub('g', campaign.get('g')) != 'saltmere_docks'
    from text_rpg.systems.shop.system import ShopSystem
    shop_system = ShopSystem(); shop_system.inject(repos=repos)
    assert not shop_system._get_shops_at_location(loop._build_context('g'))
    assert not repos['entity'].get('harbormaster_kael')['location_id'] == 'saltmere_docks'
    rebuilt = campaign.event('LOCATION_REBUILT', 'Saltmere Docks was rebuilt.', 'saltmere_docks', cause_id=event['id'])
    loop._record_events([rebuilt], 'g', 1, loop._build_context('g'))
    assert Continuity(repos).location_condition('g', 'saltmere_docks')['condition'] == 'rebuilt'
    assert 'was destroyed' in campaign.handle('history', loop._build_context('g')).outcome_description
    assert 'was rebuilt' in campaign.handle('history', loop._build_context('g')).outcome_description


def test_remote_named_death_and_destruction_are_available_to_npc_grounding(game):
    repos, loop, _, campaign = setup(game)
    npc = repos['entity'].get('harbormaster_kael')
    repos['entity'].update_field(npc['id'], 'is_alive', False)
    loop._record_events([campaign.event('DEATH', 'Kael died.', npc['id'])], 'g', 0, loop._build_context('g'))
    loop._record_events([campaign.event('LOCATION_DESTROYED', 'The Saltmere Market was destroyed.', 'saltmere_market')], 'g', 0, loop._build_context('g'))
    repos['save_game'].update_location('g', 'saltmere_tavern')
    facts = Continuity(repos).facts('g', 'saltmere_tavern', 'What happened to Harbormaster Kael and Saltmere Market?', include_history=False)
    assert 'Harbormaster Kael: dead' in facts
    assert 'Saltmere Market: currently destroyed' in facts
    assert 'Kael died.' not in facts  # this is live status, not a memory dump


def test_dead_shop_owner_cannot_trade_until_explicit_revival(game):
    repos, loop, _, campaign = setup(game)
    repos['save_game'].update_location('g', 'saltmere_docks')
    from text_rpg.systems.shop.system import ShopSystem
    shop = ShopSystem(); shop.inject(repos=repos)
    assert 'kael_supplies' in [s['id'] for s in shop._get_shops_at_location(loop._build_context('g'))]
    repos['entity'].update_field('harbormaster_kael', 'is_alive', False)
    assert 'kael_supplies' not in [s['id'] for s in shop._get_shops_at_location(loop._build_context('g'))]
    death = campaign.event('DEATH', 'Kael died.', 'harbormaster_kael')
    loop._record_events([death], 'g', 0, loop._build_context('g'))
    revival = campaign.event('REVIVAL', 'Kael was restored to life.', 'harbormaster_kael', cause_id=death['id'])
    with repos['save_game'].db.transaction():
        loop._record_events([revival], 'g', 1, loop._build_context('g'))
        repos['entity'].revive('harbormaster_kael', 'g', hp=5, cause_event_id=revival['id'])
    assert 'kael_supplies' in [s['id'] for s in shop._get_shops_at_location(loop._build_context('g'))]


def test_rare_background_revival_requires_recorded_death_and_is_not_repeatable(game, monkeypatch):
    repos, loop, _, campaign = setup(game)
    npc = repos['entity'].get('harbormaster_kael')
    fund_ritual(repos, npc)
    repos['entity'].update_field(npc['id'], 'is_hostile', True)
    repos['entity'].update_field(npc['id'], 'is_alive', False)
    death = campaign.event('DEATH', 'Kael died in a fight.', npc['id'])
    loop._record_events([death], 'g', 0, loop._build_context('g'))
    repos['save_game'].update_world_time('g', 3 * 1440 + 470)
    monkeypatch.setattr('text_rpg.systems.world_sim.secrets.randbelow', lambda _: 0)
    monkeypatch.setattr('text_rpg.mechanics.dice.random.randint', lambda *_: 20)
    loop.world_sim = WorldSimulator(repos)
    result = loop.process_turn('personal goal justice', 'g')
    assert any(e['event_type'] == 'REVIVAL' for e in result.events)
    assert repos['entity'].get(npc['id'])['is_alive']
    stock = repos['entity'].get('ritual_caster')['properties']['revival_resources']
    assert stock['charges'] == 0 and stock['materials_gp'] == 0
    revival = next(e for e in result.events if e['event_type'] == 'REVIVAL')
    assert repos['entity'].get(npc['id'])['properties']['last_revival']['method'] == revival['mechanical_details']['method']
    assert campaign.get('g')['known_deaths'] == []
    facts = Continuity(repos).facts('g', 'saltmere_docks')
    assert 'Kael died' in facts and 'revived' in facts and 'Kael: alive' in facts
    assert WorldSimulator(repos)._rare_revival('g', 3 * 1440 + 490) == []


def test_revival_can_target_a_real_encounter_enemy(game, monkeypatch):
    repos, loop, _, campaign = setup(game)
    encounter = next(e for e in load_region_encounters('iron_coast') if e['id'] == 'smuggler_lookout')
    enemy = instantiate_encounter(encounter, 'g', 'tide_caves')[0]
    assert enemy['entity_type'] == 'enemy'
    repos['entity'].save(enemy)
    fund_ritual(repos, enemy)
    repos['entity'].update_field(enemy['id'], 'is_alive', False)
    death = campaign.event('DEATH', f"{enemy['name']} died.", enemy['id'])
    loop._record_events([death], 'g', 0, loop._build_context('g'))
    monkeypatch.setattr('text_rpg.systems.world_sim.secrets.randbelow', lambda _: 0)
    monkeypatch.setattr('text_rpg.mechanics.dice.random.randint', lambda *_: 20)
    event = WorldSimulator(repos)._rare_revival('g', 3 * 1440 + 480)
    assert len(event) == 1 and event[0]['target_id'] == enemy['id']
    loop._record_events(event, 'g', 3, loop._build_context('g'))
    repos['entity'].revive(enemy['id'], 'g', hp=event[0]['mechanical_details']['restored_hp'], cause_event_id=event[0]['id'])
    assert repos['entity'].get(enemy['id'])['is_alive']


def test_generated_overlook_failure_shows_real_exit_and_campaign_route(game):
    repos, make_loop, _ = game
    seed_region('ashen_highlands', 'g', repos)
    repos['location'].save({
        'id': 'test_outlook', 'game_id': 'g', 'region_id': 'ashen_highlands', 'name': 'Test Outlook',
        'location_type': 'wilderness', 'description': 'A shelf above the road.',
        'connections': [], 'entities': [], 'items': [], 'properties': {},
    })
    repos['connection'].add_bidirectional('g', 'test_outlook', 'highland_road', 'back', 'up', 'Highland Road', 'Test Outlook')
    repos['save_game'].update_location('g', 'test_outlook')
    assert CampaignRuntime(repos).ensure('g')
    loop = make_loop(); loop._maybe_spawn_encounter = lambda *args: None
    message = loop.process_turn('go north', 'g').narrative
    assert 'go back' in message and 'No route north' in message
    assert 'go back' in loop.process_turn('leads', 'g').narrative


def test_truncated_generation_still_saves_a_traversable_site(game):
    repos, make_loop, _ = game
    seed_region('ashen_highlands', 'g', repos)
    repos['location'].save({
        'id': 'test_outlook', 'game_id': 'g', 'region_id': 'ashen_highlands',
        'name': 'Test Outlook', 'location_type': 'wilderness',
        'description': 'A shelf above the road.', 'connections': [],
        'entities': [], 'items': [], 'properties': {},
    })
    repos['connection'].add_bidirectional('g', 'test_outlook', 'highland_road', 'back', 'up', 'Highland Road', 'Test Outlook')
    repos['save_game'].update_location('g', 'test_outlook')
    llm = Mock()
    llm.generate_structured.side_effect = LLMMalformedOutputError('truncated')
    loop = make_loop()
    loop.director = Director(llm, None, None)
    loop.registry.get_system('exploration').inject(director=loop.director, repos=repos)
    loop._maybe_spawn_encounter = lambda *args: None
    result = loop.process_turn('go north', 'g')
    assert result.consumes_turn
    new_id = repos['save_game'].get_game('g')['current_location_id']
    assert new_id != 'test_outlook'
    new_place = repos['location'].get(new_id, 'g')
    assert new_place['location_type'] == 'wilderness'
    assert new_place['properties']['discoveries']
    assert any(c['target_location_id'] == 'test_outlook' for c in repos['connection'].get_connections('g', new_id))
    assert llm.generate_structured.call_count == 2
    assert loop.process_turn('go south', 'g').consumes_turn
    assert repos['save_game'].get_game('g')['current_location_id'] == 'test_outlook'


def test_failed_background_revival_is_recorded_and_does_not_revive(game, monkeypatch):
    repos, loop, _, campaign = setup(game)
    npc = repos['entity'].get('harbormaster_kael')
    fund_ritual(repos, npc)
    repos['entity'].update_field(npc['id'], 'is_hostile', True)
    repos['entity'].update_field(npc['id'], 'is_alive', False)
    death = campaign.event('DEATH', 'Kael died.', npc['id'])
    loop._record_events([death], 'g', 0, loop._build_context('g'))
    monkeypatch.setattr('text_rpg.systems.world_sim.secrets.randbelow', lambda _: 0)
    monkeypatch.setattr('text_rpg.mechanics.dice.random.randint', lambda *_: 1)
    events = WorldSimulator(repos)._rare_revival('g', 3 * 1440 + 480)
    assert [e['event_type'] for e in events] == ['REVIVAL_ATTEMPT']
    loop._record_events(events, 'g', 1, loop._build_context('g'))
    assert not repos['entity'].get(npc['id'])['is_alive']
    stock = repos['entity'].get('ritual_caster')['properties']['revival_resources']
    assert stock['charges'] == 0 and stock['materials_gp'] == 0
    assert WorldSimulator(repos)._rare_revival('g', 3 * 1440 + 490) == []
