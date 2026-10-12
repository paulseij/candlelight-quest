import importlib.util
import json
from pathlib import Path
import pytest

spec=importlib.util.spec_from_file_location('prepare',Path(__file__).parents[2]/'tools/prepare_rpg_training.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def test_unreviewed_prose_cannot_become_training_data(tmp_path):
    source=tmp_path/'source.jsonl';source.write_text(json.dumps({'approved':False}))
    with pytest.raises(ValueError,match='reviewed'):
        module.prepare(source,tmp_path/'output')


def test_entire_scenario_families_stay_in_one_split(tmp_path):
    source=tmp_path/'source.jsonl'
    records=[{'approved':True,'family':f'quest-{family}','source_ids':[f'receipt-{family}-{turn}'],'messages':[{'role':'user','content':f'Quest {family} turn {turn}'},{'role':'assistant','content':'The recorded reward is five gold.'}]} for family in range(30) for turn in range(2)]
    source.write_text('\n'.join(json.dumps(r) for r in records))
    counts=module.prepare(source,tmp_path/'output')
    assert sum(counts.values())==60
    manifest=json.loads((tmp_path/'output'/'manifest.json').read_text())
    for family in {r['family'] for r in manifest}:
        assert len({r['split'] for r in manifest if r['family']==family})==1
