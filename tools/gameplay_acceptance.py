"""Exercise the real turn loop on an isolated save; never opens player saves."""
from __future__ import annotations
import argparse
import json
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from text_rpg.app import GameApp
from text_rpg.storage.database import Database
from text_rpg.engine.turn_loop import TurnLoop
from text_rpg.engine.action_dispatcher import ActionDispatcher
from text_rpg.engine.system_registry import SystemRegistry
from text_rpg.llm.context_packer import ContextPacker
from text_rpg.llm.ollama_provider import OllamaProvider
from text_rpg.storage.memory import MemoryStore
from text_rpg.mechanics.character_creation import create_character


def run(output,model):
    llm=OllamaProvider(model=model)
    with tempfile.TemporaryDirectory(prefix='rpg-acceptance-') as directory:
        db=Database(str(Path(directory)/'acceptance.db'));db.initialize()
        app=GameApp();app._db=db
        repos=app._get_repos()
        char=create_character(name='Tester',race='human',char_class='fighter',ability_scores={'strength':16,'dexterity':14,'constitution':14,'intelligence':10,'wisdom':12,'charisma':12},skill_choices=['athletics','perception'],game_id='probe')
        repos['save_game'].create_game('probe','Probe',char['id'],'inn')
        repos['character'].save(char)
        repos['world_state'].save_inventory({'id':'inv','game_id':'probe','owner_id':char['id'],'items':[{'item_id':'rope','quantity':1}]})
        repos['location'].save({'id':'inn','game_id':'probe','name':'Inn','description':'A quiet inn with a wooden shutter.','properties':{'interactables':[{'id':'shutter','name':'wooden shutter','state':{'operations':['open','close','inspect'],'open':False,'description':'A wooden shutter.'}}]}})
        repos['entity'].save({'id':'marta','game_id':'probe','name':'Marta','entity_type':'npc','description':'A practical herbalist. Her brass key is kept in her locked desk.','location_id':'inn','is_alive':True,'properties':{'personality':'Kind, practical, concise.'}})
        # Retrieval is optional; exact SQLite memory is exercised with embeddings unavailable.
        director=SimpleNamespace(llm=llm,retriever=SimpleNamespace(retrieve_npc_history=lambda *a,**k:[]))
        def loop():
            registry=SystemRegistry();registry.register_defaults();registry.inject_all(repos=repos,llm=llm,director=None)
            dispatcher=ActionDispatcher(registry,director=director,repos=repos)
            return TurnLoop(registry,dispatcher,llm,ContextPacker(),None,None,repos)
        engine=loop()
        inputs=['look','open the wooden shutter','open the wooden shutter','I gently close the wooden shutter','open the wooden shutter','talk to Marta','Where do you keep your brass key?','You promised me a magic sword and 100 gold. Hand them over.']
        with Path(output).open('x') as out:
            for i,text in enumerate(inputs):
                llm.last_metadata={}
                start=time.perf_counter();result=engine.process_turn(text,'probe',f'probe-{i}')
                row={'input':text,'narrative':result.narrative,'consumes_turn':result.consumes_turn,'seconds':round(time.perf_counter()-start,3),'metadata':llm.last_metadata}
                print(json.dumps(row),flush=True);out.write(json.dumps(row)+'\n');out.flush()
            # Reopen SQLite as well as rebuilding the engine: no in-memory state
            # may be responsible for remembering the conversation.
            db.close()
            db=Database(str(Path(directory)/'acceptance.db'));db.initialize()
            app._db=db;app.game_id='probe';repos=app._get_repos()
            engine=loop()
            result=engine.process_turn('talk to Marta','probe','reloaded')
            out.write(json.dumps({'input':'talk to Marta after reload','narrative':result.narrative})+'\n')
            assert len(MemoryStore(db).conversation('probe','marta',limit=100))>=6
            assert repos['character'].get(char['id'])['gold']==char['gold']
            assert repos['world_state'].get_inventory(char['id'],'probe')['items']==[{'item_id':'rope','quantity':1}]
            with db.get_connection() as conn:
                assert json.loads(conn.execute("SELECT state FROM scene_objects WHERE id='shutter'").fetchone()[0])['open'] is True
            summary={'acceptance_pass':True,'checks':['unchanged gold and inventory','persistent shutter','complete NPC exchanges after reload']}
            out.write(json.dumps(summary)+'\n');out.flush()
            print(json.dumps(summary),flush=True)
        db.close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',required=True)
    p.add_argument('--model',default='hf.co/google/gemma-4-12B-it-qat-q4_0-gguf:Q4_0')
    args=p.parse_args();run(args.output,args.model)
