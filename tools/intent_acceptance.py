"""Live local intent diagnostic on an isolated authored scene; no player saves."""
import argparse
import json
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

from text_rpg.content.loader import load_region
from text_rpg.engine.scene_actions import SceneActions
from text_rpg.llm.ollama_provider import OllamaProvider
from text_rpg.models.action import Action
from text_rpg.storage.database import Database
from text_rpg.systems.base import GameContext


class RecordingProvider(OllamaProvider):
    def generate_structured(self, *args, **kwargs):
        self.plan = super().generate_structured(*args, **kwargs)
        return self.plan


def run(output, contains=None):
    llm = RecordingProvider()
    llm.scene_max_tokens = 256
    with tempfile.TemporaryDirectory(prefix='rpg-intents-') as directory:
        db = Database(str(Path(directory) / 'test.db'))
        db.initialize()
        region = load_region('iron_coast')
        location = next(e for e in region['locations'] if e['id'] == 'saltmere_docks')
        entities = [e for e in region['npcs'] if e.get('location_id') == location['id']]
        ctx = GameContext('probe', {'id': 'hero'}, location, entities)
        engine = SceneActions({'save_game': SimpleNamespace(db=db)}, llm)
        cases = [
            ('Any friendly faces about?', {'list_people'}),
            ('I could use someone to chat with', {'list_people'}),
            ('any freindly peple round these parts', {'list_people'}),
            ('Who is working on these docks?', {'list_people'}),
            ('Could I have a word with the captain?', {'talk'}),
            ('What does the captain look like?', {'inspect_person'}),
            ('Where could I head next?', {'list_exits'}),
            ('Describe my surroundings for me', {'observe'}),
            ('Is there a wizard nearby?', {'list_people'}),
            ('What is the captains secret bank balance?', {'unsupported','clarify'}),
            ('I dig a drainage channel beside the wharf', {'creative'}),
            ('I try to redirect the stream with loose stones', {'creative', 'unsupported', 'clarify', 'unknown'}),
        ]
        if contains:
            cases = [case for case in cases if contains.lower() in case[0].lower()]
        passed = 0
        with Path(output).open('x') as file:
            for text, expected in cases:
                start = time.perf_counter()
                llm.plan = None
                result = engine.propose(Action('custom', 'hero', raw_input=text), ctx)
                plan = llm.plan or ({'operation': 'creative'} if isinstance(result, Action) and
                                    result.action_type == 'creative' else {'operation': 'unknown'})
                ok = plan['operation'] in expected
                if plan['operation'] == 'creative':
                    ok = ok and isinstance(result, Action) and result.action_type == 'creative'
                elif 'talk' in expected:
                    ok = ok and isinstance(result, Action) and result.target_id == 'captain_mora'
                else:
                    ok = ok and not isinstance(result, Action) and not result.events and not result.state_mutations
                if 'wizard' in text:
                    ok = ok and plan.get('person_reference') == 'wizard' and 'Nobody here matches' in result.outcome_description
                if 'redirect the stream' in text:
                    ok = ok and not isinstance(result, Action) and 'no stream' in result.outcome_description.lower()
                passed += bool(ok)
                row = {'input': text, 'plan': plan, 'seconds': round(time.perf_counter()-start,3), 'pass': bool(ok),
                       'outcome': result.raw_input if isinstance(result, Action) else result.outcome_description}
                file.write(json.dumps(row)+'\n');file.flush()
                print(json.dumps(row), flush=True)
            row = {'passed': passed, 'total': len(cases), 'note': 'Development diagnostics, not held-out accuracy or a guarantee for all paraphrases.'}
            file.write(json.dumps(row)+'\n');print(json.dumps(row), flush=True)
        db.close()
        return passed == len(cases)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--contains', help='Run only matching diagnostic inputs')
    args = parser.parse_args()
    raise SystemExit(0 if run(args.output, args.contains) else 1)
