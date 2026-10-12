"""Paid state changes, failed prerequisites, and replay protection in real saves."""
import pytest

from tests.test_engine.test_exploration_pressure import game
from tests.test_engine.test_campaign_continuity import setup, fund_ritual
from text_rpg.systems.world_sim import WorldSimulator
from text_rpg.mechanics.revival import REVIVAL_RITES


def caster_and_corpse(game, *, diamonds=1, slots=2):
    repos, loop, char, campaign = setup(game)
    for field, value in {'char_class': 'cleric', 'level': 5, 'spellcasting_ability': 'wisdom',
                         'spell_slots_remaining': {'3': slots}, 'spell_slots_max': {'3': 2}}.items():
        repos['character'].update_field(char['id'], field, value)
    repos['spell'].learn_spell('g', char['id'], 'revivify')
    repos['spell'].prepare_spell('g', char['id'], 'revivify')
    repos['world_state'].save_inventory({'id': 'inv', 'game_id': 'g', 'owner_id': char['id'],
        'items': [{'item_id': 'revivify_diamond', 'quantity': diamonds}] if diamonds else []})
    repos['save_game'].update_location('g', 'saltmere_docks')
    repos['entity'].update_field('harbormaster_kael', 'is_alive', False)
    repos['entity'].update_field('harbormaster_kael', 'hp_current', 0)
    death = campaign.event('DEATH', 'Harbormaster Kael died.', 'harbormaster_kael')
    loop._record_events([death], 'g', 0, loop._build_context('g'))
    return repos, loop, char, death


def test_revivify_pays_once_and_alive_target_cannot_be_spammed(game):
    repos, loop, char, death = caster_and_corpse(game)
    result = loop.process_turn('cast revivify on Kael', 'g', request_id='revival')
    assert result.consumes_turn and result.action_result.success
    target = repos['entity'].get('harbormaster_kael')
    assert target['is_alive'] and target['hp_current'] == 1
    assert repos['character'].get(char['id'])['spell_slots_remaining']['3'] == 1
    assert repos['world_state'].get_inventory(char['id'], 'g')['items'] == []
    revival = next(e for e in result.events if e['event_type'] == 'REVIVAL')
    assert revival['mechanical_details']['cause_id'] == death['id']
    assert revival['mechanical_details']['materials_gp'] == 300
    assert not result.action_result.dice_rolls  # Requirements fulfilled: no invented failure roll.
    repos['save_game'].db.close()
    assert not loop.process_turn('cast revivify on Kael', 'g', request_id='revival').consumes_turn
    again = loop.process_turn('cast revivify on Kael', 'g')
    assert not again.consumes_turn and 'already alive' in again.narrative
    assert repos['character'].get(char['id'])['spell_slots_remaining']['3'] == 1


@pytest.mark.parametrize('blocked', ['diamond', 'slot', 'level', 'late', 'soul', 'body', 'distant', 'rounds'])
def test_revivify_rejects_unmet_requirements_without_spending(game, blocked):
    repos, loop, char, _ = caster_and_corpse(game, diamonds=0 if blocked == 'diamond' else 1,
                                          slots=0 if blocked == 'slot' else 2)
    if blocked == 'diamond':
        repos['character'].update_field(char['id'], 'gold', 100000)
    if blocked == 'level':
        repos['character'].update_field(char['id'], 'level', 1)
    if blocked == 'late':
        repos['save_game'].update_world_time('g', 482)
    if blocked == 'rounds':
        repos['save_game'].update_turn('g', 11)
    if blocked in {'soul', 'body'}:
        repos['entity'].update_field('harbormaster_kael', 'properties',
            {'soul_willing': False} if blocked == 'soul' else {'missing_vital_parts': True})
    if blocked == 'distant':
        repos['save_game'].update_location('g', 'saltmere_tavern')
    before_char = repos['character'].get(char['id'])
    before_items = repos['world_state'].get_inventory(char['id'], 'g')['items']
    result = loop.process_turn('cast revivify on Kael', 'g')
    assert not result.consumes_turn and not result.action_result.success
    assert not repos['entity'].get('harbormaster_kael')['is_alive']
    assert repos['character'].get(char['id'])['spell_slots_remaining'] == before_char['spell_slots_remaining']
    assert repos['world_state'].get_inventory(char['id'], 'g')['items'] == before_items


def test_revival_failure_rolls_back_component_slot_and_life_state(game, monkeypatch):
    repos, loop, char, _ = caster_and_corpse(game)
    def fail(*args, **kwargs):
        raise RuntimeError('write failed')
    monkeypatch.setattr(repos['entity'], 'revive', fail)
    with pytest.raises(RuntimeError, match='write failed'):
        loop.process_turn('cast revivify on Kael', 'g')
    assert not repos['entity'].get('harbormaster_kael')['is_alive']
    assert repos['character'].get(char['id'])['spell_slots_remaining']['3'] == 2
    assert repos['world_state'].get_inventory(char['id'], 'g')['items'][0]['quantity'] == 1
    with repos['save_game'].db.get_connection() as conn:
        assert not conn.execute("SELECT 1 FROM world_history WHERE event_type='REVIVAL'").fetchone()


def test_component_scan_handles_ordinary_weapons_and_does_not_spend_them(game):
    repos, loop, char, _ = caster_and_corpse(game)
    repos['world_state'].save_inventory({'id': 'inv', 'game_id': 'g', 'owner_id': char['id'],
        'items': [{'item_id': 'longsword', 'quantity': 1}, {'item_id': 'revivify_diamond', 'quantity': 1}]})
    assert loop.process_turn('cast revivify on Kael', 'g').action_result.success
    assert repos['world_state'].get_inventory(char['id'], 'g')['items'] == [{'item_id': 'longsword', 'quantity': 1}]


def test_revivify_reactivates_combatant_and_spends_resources(game, monkeypatch):
    repos, loop, char, _ = caster_and_corpse(game)
    system = loop.registry.get_system('combat')
    context = loop._build_context('g')
    combat = system.start_combat(context, ['harbormaster_kael', 'captain_mora'])
    target = next(c for c in combat['combatants'] if c['entity_id'] == 'harbormaster_kael')
    target['is_active'] = False
    combat['turn_order'] = [char['id'], 'harbormaster_kael', 'captain_mora']
    combat['current_turn_index'] = 0
    repos['world_state'].save_combat(combat)
    monkeypatch.setattr(system, '_resolve_all_npc_turns', lambda *a, **k: {
        'mutations': [], 'events': [], 'dice_rolls': [], 'description': ''})
    result = loop.process_turn('cast revivify on Kael', 'g')
    assert result.action_result.success
    active = repos['world_state'].get_active_combat('g')
    target = next(c for c in active['combatants'] if c['entity_id'] == 'harbormaster_kael')
    assert target['is_active'] and target['hp']['current'] == 1
    assert repos['entity'].get('harbormaster_kael')['is_alive']
    assert repos['character'].get(char['id'])['spell_slots_remaining']['3'] == 1


@pytest.mark.parametrize('blocked', ['absent', 'dead', 'broke', 'spent', 'weak', 'distant', 'unrelated', 'destroyed', 'generated'])
def test_offscreen_revival_requires_real_capable_funded_source(game, monkeypatch, blocked):
    repos, loop, _, _ = caster_and_corpse(game)
    target = repos['entity'].get('harbormaster_kael')
    if blocked != 'absent':
        fund_ritual(repos, target)
        source = repos['entity'].get('ritual_caster')
        stock = source['properties']['revival_resources']
        if blocked == 'dead':
            repos['entity'].update_field(source['id'], 'is_alive', False)
        elif blocked == 'weak':
            repos['entity'].update_field(source['id'], 'level', 1)
        elif blocked == 'generated':
            repos['entity'].update_field(source['id'], 'generated', True)
        elif blocked == 'distant':
            repos['entity'].update_field(source['id'], 'location_id', 'saltmere_tavern')
        elif blocked == 'destroyed':
            loop._record_events([{'event_type': 'LOCATION_DESTROYED', 'target_id': target['location_id']}], 'g', 0, loop._build_context('g'))
        else:
            stock.update({'broke': {'materials_gp': 0}, 'spent': {'charges': 0}, 'unrelated': {'target_ids': []}}[blocked])
            repos['entity'].update_field(source['id'], 'properties', {'revival_resources': stock})
    monkeypatch.setattr('text_rpg.systems.world_sim.secrets.randbelow', lambda _: 0)
    assert WorldSimulator(repos)._rare_revival('g', 3 * 1440 + 480) == []
    assert not repos['entity'].get(target['id'])['is_alive']


@pytest.mark.parametrize('method', list(REVIVAL_RITES))
def test_each_background_method_spends_real_stock(game, monkeypatch, method):
    repos, loop, _, _ = caster_and_corpse(game)
    target = repos['entity'].get('harbormaster_kael')
    fund_ritual(repos, target)
    rite = REVIVAL_RITES[method]
    repos['entity'].update_field('ritual_caster', 'properties', {'revival_resources': {
        'method': method, 'charges': 1, 'materials_gp': rite.materials_gp, 'target_ids': [target['id']]}})
    monkeypatch.setattr('text_rpg.systems.world_sim.secrets.randbelow', lambda _: 0)
    monkeypatch.setattr('text_rpg.mechanics.dice.random.randint', lambda *_: 20)
    events = WorldSimulator(repos)._rare_revival('g', 3 * 1440 + 480)
    assert len(events) == 1 and events[0]['event_type'] == 'REVIVAL'
    assert events[0]['mechanical_details']['method'] == method
    assert repos['entity'].get('ritual_caster')['properties']['revival_resources']['materials_gp'] == 0
