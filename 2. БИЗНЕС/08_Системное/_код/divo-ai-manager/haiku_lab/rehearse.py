"""Offline delivery: real Haiku calls, no Telegram, CRM, marketplace transports."""
from __future__ import annotations
import argparse,asyncio,copy,json,os,hashlib
from datetime import datetime,timezone
from dataclasses import asdict
from .config import ROOT,config
from .engine import respond,history_contact
from .planner import Planner
from .storage import Storage

async def main():
 p=argparse.ArgumentParser();p.add_argument('--only',type=int,nargs='*');args=p.parse_args()
 cfg=config();catalog=json.loads((ROOT/'catalog.json').read_text());fixtures=json.loads((ROOT/'fixtures.json').read_text())
 runtime=ROOT/'runtime';runtime.mkdir(exist_ok=True);store=Storage(runtime/'lab.sqlite3');planner=Planner(cfg,store)
 out=runtime/'rehearsal.jsonl';rows=0
 run_id=datetime.now(timezone.utc).isoformat();revision=hashlib.sha256((ROOT/'engine.py').read_bytes()+(ROOT/'router.md').read_bytes()).hexdigest()
 try:
  with out.open('a') as f:
   for case in fixtures:
    if args.only and case['id'] not in args.only:continue
    selected=next(x for x in catalog['cases'] if x['id']==case['id'])
    for idx,m in enumerate(case['messages']):
     if m['role']!='user':continue
     history=copy.deepcopy(case['messages'][:idx]);s={'messages':history,'has_contact':history_contact(history),'contact_asked':any(x['role']=='assistant' and 'номер' in x['content'].lower() for x in history)}
     result=await respond(m['content'],s,selected,catalog['company'],planner)
     row={'run_id':run_id,'revision':revision,'case':case['id'],'turn':idx+1,'user':m['content'],'result':asdict(result)}
     f.write(json.dumps(row,ensure_ascii=False)+'\n');f.flush();rows+=1
     print(json.dumps({'case':case['id'],'turn':idx+1,'intents':result.intents,'action':result.action,'text':result.text,'cost':result.cost,'reason':result.reason},ensure_ascii=False),flush=True)
     if result.action=='error':raise RuntimeError('Live plan test failed; see safe reason above')
 finally:await planner.close()
 print('TESTED',rows,flush=True)
if __name__=='__main__':
 os.umask(0o077);asyncio.run(main())
