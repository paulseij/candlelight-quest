import io
import json
import pytest
from pydantic import BaseModel
from text_rpg.llm.ollama_provider import OllamaProvider
from text_rpg.llm.errors import LLMMalformedOutputError


class Contract(BaseModel):
    possible: bool


def test_native_schema_roles_and_telemetry(monkeypatch):
    requests=[]
    def request(req,timeout):
        requests.append(json.loads(req.data))
        return io.BytesIO(json.dumps({'done':True,'done_reason':'stop','eval_count':7,'message':{'content':'{"possible":false}'}}).encode())
    monkeypatch.setattr('urllib.request.urlopen',request)
    provider=OllamaProvider()
    assert provider.generate_structured('Player says yes',system_prompt='Use facts',schema=Contract)=={'possible':False}
    assert requests[0]['format']==Contract.model_json_schema()
    assert requests[0]['messages'][0]['role']=='system'
    assert requests[0]['messages'][1]['role']=='user'
    assert provider.last_metadata['eval_count']==7
    assert 'message' not in provider.last_metadata


@pytest.mark.parametrize('done,reason',[(False,'stop'),(True,'length')])
def test_truncation_never_accepted_as_complete(monkeypatch,done,reason):
    monkeypatch.setattr('urllib.request.urlopen',lambda *a,**k:io.BytesIO(json.dumps({'done':done,'done_reason':reason,'message':{'content':'{"possible":true}'}}).encode()))
    with pytest.raises(LLMMalformedOutputError):
        OllamaProvider().generate_structured('x',schema=Contract)
