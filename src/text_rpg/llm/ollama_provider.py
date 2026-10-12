"""Native Ollama provider with schema enforcement and completion telemetry."""
from __future__ import annotations
import json
import threading
import time
import urllib.request

from text_rpg.llm.errors import LLMUnavailableError, LLMMalformedOutputError
from text_rpg.llm.provider import LLMProvider

DEFAULT_LOCAL_MODEL = 'hf.co/google/gemma-4-12B-it-qat-q4_0-gguf:Q4_0'


class OllamaProvider(LLMProvider):
    _generation_lock = threading.Lock()

    def __init__(self, model=DEFAULT_LOCAL_MODEL, base_url='http://localhost:11434', num_ctx=8192, think=False, timeout=90):
        self._ollama_model = model
        self.base_url = base_url.rstrip('/')
        self.num_ctx = num_ctx
        self.think = think
        self.timeout = timeout
        self.last_metadata = {}

    def _chat(self, prompt, system_prompt, temperature, max_tokens, schema=None):
        messages = []
        if system_prompt:
            messages.append({'role':'system','content':system_prompt})
        messages.append({'role':'user','content':prompt})
        payload = {'model':self._ollama_model,'messages':messages,'stream':False,'keep_alive':'30m',
                   'options':{'num_ctx':self.num_ctx,'num_predict':max_tokens,'temperature':temperature}}
        if 'qwen' in self._ollama_model.lower() or 'gemma-4' in self._ollama_model.lower():
            payload['think'] = self.think
        if schema is not None:
            payload['format'] = schema.model_json_schema()
        elif system_prompt and 'valid JSON only' in system_prompt:
            payload['format'] = 'json'
        req = urllib.request.Request(self.base_url+'/api/chat', data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
        try:
            with self._generation_lock:
                start = time.perf_counter()
                with urllib.request.urlopen(req,timeout=self.timeout) as response:
                    result = json.load(response)
                self.last_metadata = {k:v for k,v in result.items() if k != 'message'}
                self.last_metadata['wall_seconds'] = time.perf_counter()-start
        except Exception as exc:
            raise LLMUnavailableError(f'Local model request failed: {exc}') from exc
        if result.get('error'):
            raise LLMUnavailableError(result['error'])
        if result.get('done') is not True or result.get('done_reason') != 'stop':
            raise LLMMalformedOutputError('Local model response was incomplete or truncated')
        text = result.get('message',{}).get('content','')
        if not text.strip():
            raise LLMMalformedOutputError('Local model returned no visible response')
        return text

    def generate(self,prompt,system_prompt=None,temperature=0.7,max_tokens=200):
        return self._chat(prompt,system_prompt,temperature,max_tokens)

    def _generate_for_structured(self,prompt,system_prompt,temperature,max_tokens,schema):
        return self._chat(prompt,system_prompt,temperature,max_tokens,schema)

    def warmup(self):
        """Load once during game startup; exact commands remain usable."""
        payload={'model':self._ollama_model,'messages':[],'stream':False,'keep_alive':'30m','options':{'num_ctx':self.num_ctx}}
        req=urllib.request.Request(self.base_url+'/api/chat',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
        try:
            with self._generation_lock:
                with urllib.request.urlopen(req,timeout=self.timeout) as response:
                    json.load(response)
        except Exception:
            # Foreground requests report actionable failures through the UI.
            return False
        return True

    def is_available(self):
        try:
            with urllib.request.urlopen(self.base_url+'/api/tags',timeout=5) as response:
                names = [m['name'] for m in json.load(response).get('models',[])]
            return self._ollama_model in names or (':' not in self._ollama_model and self._ollama_model+':latest' in names)
        except Exception:
            return False

    @property
    def model_name(self):
        return self._ollama_model
