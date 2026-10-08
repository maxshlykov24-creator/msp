from __future__ import annotations
import asyncio,json
import httpx
from .config import ROOT,MODEL
from .engine import INTENTS,redact

class Planner:
    def __init__(self, cfg, store):
        self.cfg=cfg;self.store=store;self.lock=asyncio.Lock()
        self.client=httpx.AsyncClient(timeout=55,proxy=cfg.get('LAB_PROXY') or None,trust_env=False)
    async def __call__(self,history,text):
        async with self.lock:
            reserve=self.store.reserve(float(self.cfg.get('LAB_DAILY_USD','.50')))
            schema={'type':'object','properties':{'intents':{'type':'array','items':{'type':'string','enum':INTENTS},'minItems':1,'maxItems':6}},'required':['intents'],'additionalProperties':False}
            # Only user text and bounded history go to the classifier. No catalogue or secrets.
            messages=[{'role':'system','content':(ROOT/'router.md').read_text()}]
            messages.extend({'role':m['role'],'content':redact(m['content'])[:600]} for m in history[-12:])
            messages.append({'role':'user','content':redact(text)[:4000]})
            body={'model':MODEL,'messages':messages,'max_tokens':384,'reasoning':{'effort':'low','exclude':True},'response_format':{'type':'json_schema','json_schema':{'name':'intents','strict':True,'schema':schema}},'usage':{'include':True}}
            response=await self.client.post('https://openrouter.ai/api/v1/chat/completions',headers={'Authorization':'Bearer '+self.cfg['LAB_OPENROUTER_KEY']},json=body)
            if response.status_code!=200:raise RuntimeError('Haiku HTTP '+str(response.status_code))
            data=response.json();cost=(data.get('usage') or {}).get('cost');self.store.settle(reserve,cost)
            choice=(data.get('choices') or [{}])[0]
            if choice.get('finish_reason')!='stop':raise RuntimeError('Incomplete Haiku plan')
            return (choice.get('message') or {}).get('content') or '',float(cost or 0)
    async def close(self):await self.client.aclose()
