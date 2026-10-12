import json

import pytest

from tests.test_engine.test_exploration_pressure import game
from tests.test_engine.test_revival_costs import caster_and_corpse
from tests.test_engine.test_campaign_continuity import fund_ritual
from text_rpg.engine.snapshots import StateSerializer
from text_rpg.mechanics.time_travel import RestoreConfig
from text_rpg.mechanics.revival import REVIVAL_RITES


def service(game, method='temple_intercession'):
    repos, loop, char, death = caster_and_corpse(game)
    target = repos['entity'].get('harbormaster_kael')
    fund_ritual(repos, target)
    rite = REVIVAL_RITES[method]
    repos['entity'].update_field('ritual_caster', 'properties', {'revival_resources': {
        'method': method, 'charges': 1, 'materials_gp': rite.materials_gp,
        'offers_service': True, 'price_gp': rite.materials_gp + 250}})
    repos['character'].update_field(char['id'], 'gold', 3000)
    return repos, loop, char, death


@pytest.mark.parametrize('method', list(REVIVAL_RITES))
@pytest.mark.parametrize('roll', [1, 20])
def test_quote_then_confirm_costs_persist_on_success_and_failure(game, monkeypatch, method, roll):
    repos, loop, char, death = service(game, method)
    rite = REVIVAL_RITES[method]
    options = loop.process_turn('revival options', 'g')
    assert 'Ritual Caster' in options.narrative and 'Harbormaster Kael' in options.narrative
    quote = loop.process_turn('revive Kael with Ritual Caster', 'g')
    assert not quote.consumes_turn and f'DC {rite.dc}' in quote.narrative
    assert 'Failure' in quote.narrative and 'confirm revival' in quote.narrative
    assert repos['character'].get(char['id'])['gold'] == 3000
    repos['save_game'].db.close()
    monkeypatch.setattr('text_rpg.mechanics.dice.random.randint', lambda *_: roll)
    result = loop.process_turn('confirm revival', 'g', request_id='commit')
    assert result.consumes_turn
    assert bool(repos['entity'].get('harbormaster_kael')['is_alive']) == (roll == 20)
    assert repos['character'].get(char['id'])['gold'] == 3000 - rite.materials_gp - 250
    assert repos['save_game'].get_game('g')['world_time'] == 540
    stock = repos['entity'].get('ritual_caster')['properties']['revival_resources']
    assert stock['charges'] == 0 and stock['materials_gp'] == 0
    assert stock['ready_at'] == 1980
    event = next(e for e in result.events if e['event_type'] in {'REVIVAL', 'REVIVAL_ATTEMPT'})
    assert event['mechanical_details']['cause_id'] == death['id']
    with repos['save_game'].db.get_connection() as conn:
        assert conn.execute('SELECT world_time FROM world_history WHERE game_id=? AND id=?', ('g', event['id'])).fetchone()[0] == 540
    assert not loop.process_turn('confirm revival', 'g', request_id='commit').consumes_turn
    assert not loop.process_turn('confirm revival', 'g').consumes_turn
    assert not loop.process_turn('revive Kael with Ritual Caster', 'g').consumes_turn
    assert repos['character'].get(char['id'])['gold'] == 3000 - rite.materials_gp - 250


@pytest.mark.parametrize('change', ['moved', 'caster_dead', 'soul', 'price', 'supplies', 'gold', 'expired', 'new_death'])
def test_changed_offer_cannot_charge_or_revive(game, change):
    repos, loop, char, _ = service(game)
    loop.process_turn('revival Kael with Ritual Caster', 'g')
    if change == 'moved':
        repos['save_game'].update_location('g', 'saltmere_tavern')
    elif change == 'caster_dead':
        repos['entity'].update_field('ritual_caster', 'is_alive', False)
    elif change == 'soul':
        repos['entity'].update_field('harbormaster_kael', 'properties', {'soul_willing': False})
    elif change in {'price', 'supplies'}:
        props = repos['entity'].get('ritual_caster')['properties']
        props['revival_resources']['price_gp' if change == 'price' else 'materials_gp'] = 900
        repos['entity'].update_field('ritual_caster', 'properties', props)
    elif change == 'gold':
        repos['character'].update_field(char['id'], 'gold', 1)
    elif change == 'expired':
        repos['save_game'].update_world_time('g', 540)
    elif change == 'new_death':
        loop._record_events([{'event_type': 'DEATH', 'target_id': 'harbormaster_kael', 'description': 'A later death.'}], 'g', 0, loop._build_context('g'))
    before = repos['character'].get(char['id'])['gold']
    result = loop.process_turn('confirm revival', 'g')
    assert not result.consumes_turn
    assert not repos['entity'].get('harbormaster_kael')['is_alive']
    assert repos['character'].get(char['id'])['gold'] == before
    assert 'no revival offer' in loop.process_turn('confirm revival', 'g').narrative


def test_cancel_and_failed_new_quote_disarm_old_offer(game):
    repos, loop, char, _ = service(game)
    loop.process_turn('revival Kael with Ritual Caster', 'g')
    assert not loop.process_turn('cancel revival', 'g').consumes_turn
    assert 'no revival offer' in loop.process_turn('confirm revival', 'g').narrative
    loop.process_turn('revival Kael with Ritual Caster', 'g')
    loop.process_turn('revival Unknown with Ritual Caster', 'g')
    assert 'no revival offer' in loop.process_turn('confirm revival', 'g').narrative
    assert repos['character'].get(char['id'])['gold'] == 3000


def test_ritual_failure_to_commit_restores_money_stock_and_offer(game, monkeypatch):
    repos, loop, char, _ = service(game)
    loop.process_turn('revival Kael with Ritual Caster', 'g')
    monkeypatch.setattr('text_rpg.mechanics.dice.random.randint', lambda *_: 20)
    def fail(*args, **kwargs):
        raise RuntimeError('commit failed')
    monkeypatch.setattr(repos['entity'], 'revive', fail)
    with pytest.raises(RuntimeError, match='commit failed'):
        loop.process_turn('confirm revival', 'g')
    assert repos['character'].get(char['id'])['gold'] == 3000
    assert repos['entity'].get('ritual_caster')['properties']['revival_resources']['charges'] == 1
    with repos['save_game'].db.get_connection() as conn:
        assert conn.execute("SELECT 1 FROM revival_offers WHERE game_id='g'").fetchone()


def test_rewind_to_before_offer_does_not_keep_future_permission(game):
    repos, loop, char, _ = service(game)
    serializer = StateSerializer(repos)
    snapshot = serializer.capture('g', 'before_quote')
    # Also exercise compatibility with a snapshot made before this migration.
    metadata = json.loads(snapshot['metadata'])
    metadata['tables'].pop('revival_offers')
    snapshot['metadata'] = json.dumps(metadata)
    loop.process_turn('revival Kael with Ritual Caster', 'g')
    serializer.restore('g', snapshot, RestoreConfig(keep_player_stats=False, keep_inventory=False, keep_spells=False))
    assert 'no revival offer' in loop.process_turn('confirm revival', 'g').narrative


def test_existing_save_upgrade_preserves_spent_budgets_and_dead_providers(game):
    from text_rpg.content.region_seeder import seed_region
    repos, loop, _, _ = service(game)
    seed_region('kingsward', 'g', repos)
    seed_region('the_veil', 'g', repos)
    auriel = repos['entity'].get('high_priestess_auriel')
    stock = auriel['properties']['revival_resources']
    stock.update(charges=0, materials_gp=0, ready_at=99999)
    stock.pop('offers_service')
    repos['entity'].update_field(auriel['id'], 'properties', auriel['properties'])
    repos['entity'].update_field('fey_archon', 'properties', {})
    repos['entity'].update_field('fey_archon', 'is_alive', False)
    db = repos['save_game'].db
    with db.get_connection() as conn:
        conn.execute('DROP TABLE revival_offers')
        conn.execute('DELETE FROM schema_version WHERE version=26')
    db.initialize()
    migrated = repos['entity'].get(auriel['id'])['properties']['revival_resources']
    assert migrated['offers_service'] and migrated['charges'] == 0 and migrated['materials_gp'] == 0
    assert migrated['ready_at'] == 99999
    assert not repos['entity'].get('fey_archon')['is_alive']
    props = repos['entity'].get('fey_archon')['properties']
    props['revival_resources']['charges'] = 0
    repos['entity'].update_field('fey_archon', 'properties', props)
    db.initialize()
    assert repos['entity'].get('fey_archon')['properties']['revival_resources']['charges'] == 0
