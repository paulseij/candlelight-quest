"""Build MLX-compatible splits from explicitly reviewed RPG examples.

Input JSONL records: family, source_ids, approved (true), messages. Each family
is a whole NPC/quest/scenario family, never an individual dialogue row. Use
engine-derived labels for mechanics and reviewed prose for dialogue. This tool
does not turn model-generated campaign history into approved training data.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path


def prepare(source,output):
    records=[json.loads(line) for line in Path(source).read_text().splitlines() if line.strip()]
    groups={'train':[],'valid':[],'test':[]}
    seen=set()
    if any(r.get('approved') is not True for r in records):
        raise ValueError('Every example must be explicitly reviewed and approved')
    families=sorted({r.get('family','') for r in records},key=lambda f:hashlib.sha256(str(f).encode()).hexdigest())
    if len(families)<3:
        raise ValueError('Need at least three independent scenario families')
    n_train=min(len(families)-2,max(1,int(len(families)*0.8)))
    n_valid=min(len(families)-n_train-1,max(1,int(len(families)*0.1)))
    split_by_family={f:('train' if i<n_train else 'valid' if i<n_train+n_valid else 'test') for i,f in enumerate(families)}
    for record in records:
        if record.get('approved') is not True:
            raise ValueError('Every example must be explicitly reviewed and approved')
        family=record.get('family')
        if not isinstance(family,str) or not family or not record.get('source_ids'):
            raise ValueError('Each example requires a scenario family and source provenance')
        messages=record.get('messages',[])
        if not messages or messages[-1].get('role')!='assistant':
            raise ValueError('Each example must end with the approved assistant target')
        if any(m.get('role') not in {'system','user','assistant'} or not isinstance(m.get('content'),str) for m in messages):
            raise ValueError('Invalid training messages')
        fingerprint=hashlib.sha256(json.dumps(messages,sort_keys=True).encode()).hexdigest()
        if fingerprint in seen:
            raise ValueError('Duplicate training example')
        seen.add(fingerprint)
        split=split_by_family[family]
        groups[split].append((record,fingerprint))
    if not all(groups.values()):
        raise ValueError('Need separate scenario families in train, valid and test; do not split neighboring turns')
    output=Path(output)
    output.mkdir(parents=True,exist_ok=False)
    manifest=[]
    for split,rows in groups.items():
        with (output/f'{split}.jsonl').open('w') as file:
            for record,fingerprint in rows:
                file.write(json.dumps({'messages':record['messages']})+'\n')
                manifest.append({'family':record['family'],'split':split,'source_ids':record['source_ids'],'sha256':fingerprint})
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return {name:len(rows) for name,rows in groups.items()}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reviewed-jsonl',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    print(json.dumps(prepare(args.reviewed_jsonl,args.output)))
