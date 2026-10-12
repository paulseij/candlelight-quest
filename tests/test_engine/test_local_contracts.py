"""Integration gates for local grounded turns, memory and snapshot restoration."""
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from text_rpg.storage.memory import MemoryStore
from text_rpg.storage.repos.event_ledger import EventLedgerRepo
from text_rpg.engine.snapshots import StateSerializer
from text_rpg.mechanics.time_travel import RestoreConfig


@pytest.fixture
def db(in_memory_db):
    with in_memory_db.get_connection() as c:
        c.execute("INSERT INTO games(id,name,created_at,current_location_id) VALUES('g','Game','now','a')")
        c.execute("INSERT INTO characters(id,game_id,name,hp_current,hp_max,gold) VALUES('p','g','Player',20,20,10)")
        c.execute("INSERT INTO locations(id,game_id,name) VALUES('a','g','Bank'),('b','g','Balcony')")
        c.execute("INSERT INTO inventory(id,game_id,owner_id,items) VALUES('i','g','p','[]')")
    return in_memory_db


def event(db,id,text):
    EventLedgerRepo(db).append({'id':id,'game_id':'g','event_type':'TEST','description':text,'turn_number':0,'timestamp':'now'})


def test_nested_read_does_not_commit_partial_write(db):
    with pytest.raises(ValueError):
        with db.transaction() as c:
            c.execute("UPDATE characters SET gold=500")
            with db.get_connection() as nested:
                assert nested.execute('SELECT gold FROM characters').fetchone()[0]==500
            raise ValueError('fail after read')
    with db.get_connection() as c:
        assert c.execute('SELECT gold FROM characters').fetchone()[0]==10


def test_swallowed_nested_failure_aborts(db):
    with pytest.raises(RuntimeError,match='nested'):
        with db.transaction() as c:
            c.execute('UPDATE characters SET gold=100')
            try:
                with db.get_connection() as nested:
                    nested.execute('INSERT INTO nonexistent VALUES(1)')
            except sqlite3.Error:
                pass
    with db.get_connection() as c:
        assert c.execute('SELECT gold FROM characters').fetchone()[0]==10


def test_staged_resolution_does_not_lock_or_change_save(db):
    reader=sqlite3.connect(db.db_path)
    with db.staged_transaction() as c:
        c.execute('UPDATE characters SET gold=15')
        assert reader.execute('SELECT gold FROM characters').fetchone()[0]==10
        assert not reader.in_transaction
    assert reader.execute('SELECT gold FROM characters').fetchone()[0]==15
    reader.close()


def test_staged_conflict_preserves_concurrent_change(db):
    other=sqlite3.connect(db.db_path)
    with pytest.raises(RuntimeError,match='save changed'):
        with db.staged_transaction() as c:
            c.execute('UPDATE characters SET gold=15')
            other.execute('UPDATE characters SET gold=12')
            other.commit()
    assert other.execute('SELECT gold FROM characters').fetchone()[0]==12
    other.close()


def test_staged_failure_has_no_events_or_outbox(db):
    with pytest.raises(ValueError):
        with db.staged_transaction() as c:
            c.execute('UPDATE characters SET gold=100')
            event(db,'bad','false reward')
            raise ValueError('fail')
    with db.get_connection() as c:
        assert c.execute('SELECT gold FROM characters').fetchone()[0]==10
        assert c.execute('SELECT count(*) FROM events').fetchone()[0]==0
        assert c.execute('SELECT count(*) FROM index_outbox').fetchone()[0]==0


def test_memory_scopes_save_npc_and_fork(db):
    memory=MemoryStore(db)
    event(db,'past','shared past')
    memory.record_exchange('g','marta','p','Keep my key','I will keep it in the desk.','t1')
    with db.get_connection() as c:
        watermark=c.execute('SELECT MAX(rowid) FROM events').fetchone()[0]
    event(db,'future','discarded future')
    memory.record_exchange('g','marta','p','Move the key','I moved it to the cellar.','t2')
    with db.get_connection() as c:
        c.execute("INSERT INTO timelines VALUES('g','branch','prime',?,NULL)",(watermark,))
        c.execute("UPDATE games SET timeline_id='branch'")
    rows=memory.conversation('g','marta')
    assert len(rows)==2 and 'desk' in rows[-1]['text']
    assert memory.conversation('other','marta')==[]
    assert memory.conversation('g','other_npc')==[]
    assert [e['id'] for e in EventLedgerRepo(db).get_recent('g')]==['past']


def test_snapshot_roundtrip_replaces_future_rows_and_quest_progress(db):
    serializer=StateSerializer({'save_game':SimpleNamespace(db=db)})
    with db.get_connection() as c:
        c.execute("INSERT INTO quests(id,game_id,name,objectives,gold_reward) VALUES('q','g','Quest','[1]',5)")
        c.execute("INSERT INTO bounties(id,game_id,region,amount) VALUES('bounty','g','f',7)")
    event(db,'past','past')
    meta=serializer._capture_tables('g')
    with db.get_connection() as c:
        c.execute("UPDATE quests SET objectives='[9]',gold_reward=99")
        c.execute("DELETE FROM bounties")
        c.execute("INSERT INTO entities(id,game_id,name) VALUES('future','g','Future NPC')")
    event(db,'future','future')
    snapshot={'id':'snap','metadata':json.dumps(meta),'location_id':'a','world_time':480,'turn_number':0}
    serializer.restore('g',snapshot,RestoreConfig(keep_player_stats=False,keep_inventory=False,keep_spells=False))
    with db.get_connection() as c:
        assert c.execute('SELECT objectives,gold_reward FROM quests').fetchone()[:]==('[1]',5)
        assert c.execute('SELECT amount FROM bounties').fetchone()[0]==7
        assert c.execute("SELECT count(*) FROM entities WHERE id='future'").fetchone()[0]==0
        assert c.execute('SELECT loop_count FROM games').fetchone()[0]==1
    assert [e['id'] for e in EventLedgerRepo(db).get_recent('g')]==['past']


def test_repeat_request_never_dispatches_twice(db):
    from text_rpg.engine.turn_loop import TurnLoop,TurnResult
    from text_rpg.models.action import ActionResult
    loop=TurnLoop(Mock(),Mock(),Mock(),Mock(),Mock(),Mock(),{'save_game':SimpleNamespace(db=db)})
    class Save:
        def get_game(self,g):
            with db.get_connection() as c:return dict(c.execute('SELECT * FROM games WHERE id=?',(g,)).fetchone())
        def update_turn(self,g,t):
            with db.get_connection() as c:c.execute('UPDATE games SET turn_number=? WHERE id=?',(t,g))
    save=Save();save.db=db;loop.repos['save_game']=save
    count=[]
    def resolve(raw,g):
        count.append(1)
        with db.get_connection() as c:c.execute('UPDATE characters SET gold=gold+5')
        return TurnResult(action_result=ActionResult(success=True,outcome_description='Gained 5 gold.'),consumes_turn=True)
    loop._process_turn=resolve
    loop._build_context=lambda g:None
    loop._narrate=lambda r,c:r.outcome_description
    assert loop.process_turn('take reward','g','request1').narrative=='Gained 5 gold.'
    assert loop.process_turn('take reward','g','request1').narrative=='Gained 5 gold.'
    assert len(count)==1
    with db.get_connection() as c:assert c.execute('SELECT gold FROM characters').fetchone()[0]==15


def test_rope_balcony_traversal_persists_and_cannot_duplicate_rope(db,monkeypatch):
    from text_rpg.engine.scene_actions import SceneActions
    from text_rpg.models.action import Action
    from text_rpg.systems.base import GameContext
    class Location:
        def get(self,id,g):
            with db.get_connection() as c:
                r=c.execute('SELECT * FROM locations WHERE id=? AND game_id=?',(id,g)).fetchone()
            return dict(r) if r else None
    class World:
        def get_inventory(self,p,g):
            with db.get_connection() as c:return dict(c.execute('SELECT * FROM inventory WHERE owner_id=? AND game_id=?',(p,g)).fetchone())
        def update_inventory(self,id,items):
            with db.get_connection() as c:c.execute('UPDATE inventory SET items=? WHERE id=?',(json.dumps(items),id))
    class Save:
        def update_location(self,g,id):
            with db.get_connection() as c:c.execute('UPDATE games SET current_location_id=? WHERE id=?',(id,g))
    save=Save();save.db=db
    repos={'save_game':save,'location':Location(),'world_state':World()}
    scene=SceneActions(repos,Mock())
    with db.get_connection() as c:c.execute('UPDATE inventory SET items=?',(json.dumps([{'item_id':'rope','quantity':1}]),))
    ctx=GameContext('g',{'id':'p'}, {'id':'a','name':'Bank'},[])
    scene.register('g','a','balcony','balcony',{'operations':['attach','detach','traverse'],'attachment_item_id':'rope','destination':'b','dc':10})
    a=Action('custom','p',raw_input='attach rope to balcony')
    assert scene.execute(a,ctx,{'operation':'attach','object_id':'balcony','item_id':'rope'}).success
    assert not scene.execute(a,ctx,{'operation':'attach','object_id':'balcony','item_id':'rope'}).success
    db.close() # Force a real reload.
    scene=SceneActions(repos,Mock())
    assert scene.objects(ctx)[0]['state']['attached']=='rope'
    monkeypatch.setattr('text_rpg.engine.scene_actions.skill_check',lambda *args:(True,SimpleNamespace(individual_rolls=[20],modifier=0,total=20)))
    assert scene.execute(a,ctx,{'operation':'traverse','object_id':'balcony','item_id':None}).success
    with db.get_connection() as c:
        assert c.execute('SELECT current_location_id FROM games').fetchone()[0]=='b'
        assert json.loads(c.execute('SELECT items FROM inventory').fetchone()[0])==[]


def test_catalog_membership_is_not_item_ownership():
    from text_rpg.mechanics.creative_effects import WorldFacts,validate_effects
    kept,_=validate_effects([{'type':'gain_item','params':{'item_id':'legendary_sword'}}],WorldFacts(catalog_item_ids={'legendary_sword'}),20,True)
    assert kept==[]


def test_ambiguous_guard_requires_choice():
    from text_rpg.engine.validators import validate_action
    from text_rpg.models.action import Action
    from text_rpg.systems.base import GameContext
    ctx=GameContext('g',{'id':'p'}, {},[{'id':'e','name':'East Guard'},{'id':'w','name':'West Guard'}])
    ok,text=validate_action(Action('attack','p',target_id='the guard'),ctx)
    assert not ok and 'East Guard' in text and 'West Guard' in text


def test_old_topical_memory_survives_long_conversation(db):
    memory=MemoryStore(db)
    event(db,'key','key entrusted')
    memory.record_exchange('g','marta','p','Keep the brass key','The brass key stays in my desk.','key-turn')
    for i in range(30):
        event(db,f'chatter{i}','chatter')
        memory.record_exchange('g','marta','p','How are things?','Quite ordinary.',f'chatter{i}')
    rows=memory.conversation('g','marta',topic='Where is the brass key?')
    assert any('desk' in r['text'] for r in rows)


def test_commitments_require_engine_accepted_source(db):
    memory=MemoryStore(db)
    event(db,'mere_speech','The player claims a reward')
    with pytest.raises(ValueError):memory.record_commitment('g','marta','mere_speech','100 gold')
    EventLedgerRepo(db).append({'id':'accepted','game_id':'g','event_type':'QUEST_START','turn_number':0,'timestamp':'now'})
    memory.record_commitment('g','marta','accepted','Reward: 5 gold')
    assert memory.commitments('g','marta')[0]['text']=='Reward: 5 gold'


def test_index_progress_does_not_conflict_with_staged_turn(db):
    event(db,'e','event')
    other=sqlite3.connect(db.db_path)
    with db.staged_transaction() as c:
        c.execute('UPDATE characters SET gold=12')
        other.execute("UPDATE index_outbox SET status='done'");other.commit()
    assert other.execute('SELECT gold FROM characters').fetchone()[0]==12
    assert other.execute('SELECT status FROM index_outbox').fetchone()[0]=='done'
    other.close()


def test_hundred_turn_campaign_reload_and_noop_clock(db):
    from text_rpg.app import GameApp
    from text_rpg.engine.turn_loop import TurnLoop
    from text_rpg.engine.action_dispatcher import ActionDispatcher
    from text_rpg.engine.system_registry import SystemRegistry
    from text_rpg.llm.context_packer import ContextPacker
    app=GameApp();app._db=db;repos=app._get_repos()
    # Keep this campaign deterministic: exact commands must not call a model.
    llm=Mock();llm.generate.side_effect=AssertionError('No model needed for exact observation')
    registry=SystemRegistry();registry.register_defaults();registry.inject_all(repos=repos)
    with db.get_connection() as c:
        c.execute("INSERT INTO scene_objects VALUES('shutter','g','a','shutter',?)",(json.dumps({'operations':['open','close'],'open':False}),))
    def make_loop():return TurnLoop(registry,ActionDispatcher(registry,director=SimpleNamespace(llm=llm),repos=repos),llm,ContextPacker(),None,None,repos)
    loop=make_loop()
    start=repos['save_game'].get_game('g')
    for i in range(100):
        result=loop.process_turn('look','g',f'observation-{i}')
        assert not result.consumes_turn
        changed=loop.process_turn('open shutter' if i%2==0 else 'close shutter','g',f'action-{i}')
        assert changed.consumes_turn
        if i%10==0:
            db.close();loop=make_loop()
    end=repos['save_game'].get_game('g')
    assert end['world_time']>start['world_time']
    assert end['turn_number']==start['turn_number']+100
    assert repos['character'].get('p')['gold']==10
    with db.get_connection() as c:assert c.execute('SELECT count(*) FROM turn_receipts').fetchone()[0]==200


@pytest.mark.parametrize('seed',range(100))
def test_arbitrary_effect_bundles_cannot_escape_action_permissions(seed):
    import random
    from text_rpg.mechanics.creative_effects import WorldFacts,validate_effects
    rng=random.Random(seed)
    effects=[{'type':rng.choice(['gain_gold','gain_item','hp_change','spawn_entity','lose_item','narrative_only']),
              'params':{'amount':rng.randint(-10000,10000),'item_id':'legendary_sword','quantity':100,'name':'Dragon'}} for _ in range(30)]
    kept,_=validate_effects(effects,WorldFacts(catalog_item_ids={'legendary_sword'},allowed_effects={'narrative_only'}),20,True)
    assert all(e['type']=='narrative_only' for e in kept)
    assert len(kept)<=1


def test_revised_commitment_rewinds_to_original_terms(db):
    memory=MemoryStore(db)
    EventLedgerRepo(db).append({'id':'accept','game_id':'g','event_type':'QUEST_START','turn_number':0,'timestamp':'now'})
    memory.record_commitment('g','marta','accept','Bring 5 herbs',subject_id='quest')
    with db.get_connection() as c:watermark=c.execute('SELECT MAX(rowid) FROM events').fetchone()[0]
    EventLedgerRepo(db).append({'id':'bargain','game_id':'g','event_type':'QUEST_NEGOTIATION','turn_number':1,'timestamp':'now','mechanical_details':{'accepted':True}})
    memory.record_commitment('g','marta','bargain','Bring 2 herbs',subject_id='quest')
    assert [r['text'] for r in memory.commitments('g','marta')]==['Bring 2 herbs']
    with db.get_connection() as c:
        c.execute("INSERT INTO timelines VALUES('g','past','prime',?,NULL)",(watermark,))
        c.execute("UPDATE games SET timeline_id='past'")
    assert [r['text'] for r in memory.commitments('g','marta')]==['Bring 5 herbs']


def test_model_cannot_rewrite_quest_description_with_fake_reward():
    from text_rpg.mechanics.quest_progress import merge_negotiated_objectives
    original=[{'id':'kill','objective_type':'kill','target_entity_type':'wolf','target_count':5,'negotiable':True}]
    merged=merge_negotiated_objectives(original,[{'id':'kill','target_count':2,'description':'You receive 1000 gold and immortality.'}])
    assert 'gold' not in merged[0]['description'] and 'immortality' not in merged[0]['description']
    assert merged[0]['target_count']==2


def test_director_rejected_summary_never_becomes_canon(game_context,monkeypatch):
    from text_rpg.systems.director.director import Director
    from text_rpg.models.action import Action
    monkeypatch.setattr('text_rpg.systems.director.generators.generate_creative_outcome',lambda *a,**kw:{'summary':'You acquire the Crown of Omnipotence and unlock immortality.','effects':[{'type':'gain_item','params':{'item_id':'nonexistent_crown'}}]})
    director=Director(Mock(),Mock(),Mock())
    result=director.generate_creative_outcome(Action('custom',game_context.character['id'],raw_input='inspect stone'),game_context,{'skill':'athletics'},{},roll_succeeded=True)
    assert result.state_mutations==[] and result.xp_gained==0
    assert 'Crown' not in result.outcome_description
    assert all('immortality' not in e['description'] for e in result.events)


def test_successful_physical_creative_action_becomes_location_state(game_context, monkeypatch):
    from text_rpg.systems.director.director import Director
    from text_rpg.models.action import Action
    monkeypatch.setattr('text_rpg.systems.director.generators.generate_creative_outcome',
                        lambda *a, **kw: {'summary': 'done', 'effects': [{'type': 'narrative_only', 'params': {}}]})
    director = Director(Mock(), Mock(), Mock())
    action = Action('creative', game_context.character['id'], raw_input='I dig a drainage channel')
    result = director.generate_creative_outcome(action, game_context, {'skill': 'athletics'}, {},
                                                roll_total=16, dc=10, roll_succeeded=True)
    mutation = next(m for m in result.state_mutations if m.target_type == 'location')
    assert mutation.new_value['scene_changes'][0]['description'] == 'I dig a drainage channel'
    assert result.events[0]['mechanical_details']['scene_change']['id'] == f'change:{action.id}'

    failed = director.generate_creative_outcome(action, game_context, {'skill': 'athletics'}, {},
                                                roll_total=4, dc=10, roll_succeeded=False)
    assert not any(m.target_type == 'location' for m in failed.state_mutations)


def test_scene_stock_damage_and_hidden_route_are_real_state(db):
    from text_rpg.app import GameApp
    from text_rpg.engine.scene_actions import SceneActions
    from text_rpg.models.action import Action
    from text_rpg.systems.base import GameContext
    app=GameApp();app._db=db;repos=app._get_repos()
    scene=SceneActions(repos,Mock())
    ctx=GameContext('g',repos['character'].get('p'),repos['location'].get('a','g'),[])
    scene.register('g','a','chest','chest',{'operations':['transfer','consume','damage','reveal'],'stock':{'rope':1,'healing_potion':1},'consumables':{'healing_potion':{'healing':4}},'hp':2,'destination':'b','direction':'up'})
    action=Action('custom','p')
    def execute(op,item=None):return scene.execute(action,ctx,{'operation':op,'object_id':'chest','item_id':item})
    assert execute('transfer','rope').success
    assert not execute('transfer','rope').success
    assert not execute('transfer','legendary_sword').success
    assert execute('consume','healing_potion').success
    assert not execute('consume','healing_potion').success
    assert execute('damage').success
    assert execute('damage').success
    assert not execute('damage').success
    assert execute('reveal').success
    assert not execute('reveal').success
    assert repos['connection'].find_connection('g','a','up')['target_location_id']=='b'
    items=repos['world_state'].get_inventory('p','g')['items']
    assert items==[{'item_id':'rope','quantity':1}]
    assert repos['character'].get('p')['hp_current']==20


def test_distraction_expires_and_only_opens_its_guarded_route(db,monkeypatch):
    from text_rpg.app import GameApp
    from text_rpg.engine.scene_actions import SceneActions
    from text_rpg.models.action import Action
    from text_rpg.systems.base import GameContext
    app=GameApp();app._db=db;repos=app._get_repos()
    repos['entity'].save({'id':'guard','game_id':'g','name':'Guard','location_id':'a','is_alive':True,'properties':{}})
    scene=SceneActions(repos,Mock())
    scene.register('g','a','guard_post','guard post',{'operations':['distract','traverse'],'entity_id':'guard','guard_id':'guard','destination':'b','dc':10})
    def context(t):return GameContext('g',repos['character'].get('p'),repos['location'].get('a','g'),repos['entity'].get_by_location('g','a'),world_time=t)
    action=Action('custom','p')
    def execute(op,t):return scene.execute(action,context(t),{'operation':op,'object_id':'guard_post','item_id':None})
    assert not execute('traverse',480).success
    monkeypatch.setattr('text_rpg.engine.scene_actions.skill_check',lambda *a:(True,SimpleNamespace(individual_rolls=[20],modifier=0,total=20)))
    assert execute('distract',480).success
    assert not execute('traverse',491).success
    assert execute('traverse',485).success


def test_rewind_preserves_reputation_independently_of_bounties(db):
    serializer=StateSerializer({'save_game':SimpleNamespace(db=db)})
    with db.get_connection() as c:
        c.execute("INSERT INTO entities(id,game_id,name,properties) VALUES('n','g','NPC',?)",(json.dumps({'relationships':{'player':{'trust':0}}}),))
        c.execute("INSERT INTO bounties(id,game_id,region,amount) VALUES('bounty','g','f',7)")
    meta=serializer._capture_tables('g')
    with db.get_connection() as c:
        c.execute("UPDATE entities SET properties=?",(json.dumps({'relationships':{'player':{'trust':4}}}),))
        c.execute('UPDATE bounties SET amount=50')
    snapshot={'id':'s','game_id':'g','metadata':json.dumps(meta),'location_id':'a','world_time':480,'turn_number':0}
    serializer.restore('g',snapshot,RestoreConfig(keep_reputation=True,keep_bounties=False))
    with db.get_connection() as c:
        assert json.loads(c.execute('SELECT properties FROM entities').fetchone()[0])['relationships']['player']['trust']==4
        assert c.execute('SELECT amount FROM bounties').fetchone()[0]==7


def test_semantic_observation_commits_receipt_without_advancing_world(db):
    from text_rpg.app import GameApp
    from text_rpg.engine.turn_loop import TurnLoop
    from text_rpg.engine.action_dispatcher import ActionDispatcher
    from text_rpg.engine.system_registry import SystemRegistry
    from text_rpg.llm.context_packer import ContextPacker
    app = GameApp(); app._db = db; repos = app._get_repos()
    llm = Mock()
    llm.scene_temperature = 0
    llm.scene_max_tokens = 256
    llm.generate_structured.return_value = {'operation': 'list_people', 'object_id': None, 'item_id': None, 'person_reference': None}
    registry = SystemRegistry(); registry.register_defaults(); registry.inject_all(repos=repos)
    dispatcher = ActionDispatcher(registry, director=SimpleNamespace(llm=llm), repos=repos)
    loop = TurnLoop(registry, dispatcher, llm, ContextPacker(), None, None, repos)
    before = repos['save_game'].get_game('g')
    result = loop.process_turn('Any friendly faces about?', 'g', 'people-query')
    assert not result.consumes_turn
    assert 'nobody here' in result.narrative
    assert repos['save_game'].get_game('g') == before
    assert repos['character'].get('p')['gold'] == 10
    llm.generate.assert_not_called()
    again = loop.process_turn('Any friendly faces about?', 'g', 'people-query')
    assert again.narrative == result.narrative
    llm.generate_structured.assert_called_once()
