from __future__ import annotations
import asyncio,fcntl,json,logging,os,time
from dataclasses import asdict
from pathlib import Path
import httpx
from .config import ROOT,MODEL,config
from .engine import respond,redact
from .planner import Planner
from .storage import Storage

logging.basicConfig(level=logging.WARNING,format='%(levelname)s %(message)s')
logging.getLogger('httpx').setLevel(logging.ERROR)
HELP='''Тестовый агент Haiku 5.5. Сообщения клиентам и задачи менеджерам отсюда не уходят.
/cars — выбрать машину из снимка стока
/cases — сценарии из проверенных переписок
/car НОМЕР — начать по машине
/case НОМЕР — начать по объявлению сценария
/reset — новый диалог по той же машине
/resume — продолжить после тестовой передачи человеку
/status — модель, машина и дата снимка
/debug — показать или скрыть разбор ответа
/export — скачать свою тестовую переписку
/results — скачать автоматические прогоны
/feedback ТЕКСТ — сохранить замечание к последнему ответу

После выбора машины пиши как клиент. Для проверки контакта используй вымышленный номер.'''

class Lab:
    def __init__(self,cfg):
        self.cfg=cfg;self.token=cfg['LAB_TELEGRAM_TOKEN'];self.allowed={int(v) for v in cfg['LAB_ALLOWED_USERS'].split(',') if v.strip()}
        if self.token.split(':')[0] in cfg.get('LAB_FORBIDDEN_BOT_IDS','').split(','):
            raise RuntimeError('Production bot credential forbidden')
        if not self.allowed or any(x<=0 for x in self.allowed):raise RuntimeError('Private user allowlist required')
        self.runtime=ROOT/'runtime';self.runtime.mkdir(exist_ok=True)
        self.store=Storage(self.runtime/'lab.sqlite3');self.planner=Planner(cfg,self.store)
        self.tg=httpx.AsyncClient(timeout=40,proxy=cfg.get('LAB_PROXY') or None,trust_env=False)
        self.catalog=json.loads((ROOT/'catalog.json').read_text())
    async def api(self,method,**payload):
        r=await self.tg.post('https://api.telegram.org/bot'+self.token+'/'+method,json=payload)
        d=r.json()
        if not d.get('ok'):raise RuntimeError('Telegram '+method+' HTTP '+str(r.status_code))
        return d['result']
    async def send(self,chat,text):
        if chat not in self.allowed:raise RuntimeError('Chat not allowed')
        for start in range(0,len(text),3800):
            await self.api('sendMessage',chat_id=chat,text=text[start:start+3800],link_preview_options={'is_disabled':True})
    def selected(self,s):
        group=s.get('group','cars');key=str(s.get('selected',''))
        return next((x for x in self.catalog.get(group,[]) if str(x['id'])==key),None)
    async def command(self,chat,text):
        cmd,_,arg=text.partition(' ');cmd=cmd.split('@')[0].lower();s=self.store.session(chat)
        if cmd in ('/start','/help'):await self.send(chat,HELP)
        elif cmd in ('/cars','/cases'):
            group='cars' if cmd=='/cars' else 'cases';selector='/car' if group=='cars' else '/case'
            query=arg.strip().lower();rows=self.catalog[group]
            if query:rows=[r for r in rows if query in r['listing']['title'].lower()]
            lines=[selector+' '+str(r['id'])+' · '+r['listing']['title']+' · '+str(r['listing'].get('price','')) for r in rows]
            await self.send(chat,'\n'.join(lines) if lines else 'Совпадений нет. Можно искать: /cars Geely')
        elif cmd in ('/car','/case'):
            group='cars' if cmd=='/car' else 'cases';row=next((x for x in self.catalog[group] if str(x['id'])==arg.strip()),None)
            if not row:await self.send(chat,'Не нашёл номер. Открой /cars или /cases');return
            self.store.event(chat,'reset',{'previous':s.get('selected'),'group':group,'selected':row['id']})
            self.store.save(chat,{'messages':[],'group':group,'selected':row['id'],'debug':s.get('debug',False)})
            await self.send(chat,'Тестовый диалог: '+row['listing']['title']+'\nЦена: '+str(row['listing'].get('price','неизвестна'))+'\nСнимок: '+self.catalog['created_at']+'\nПиши вопрос клиента.')
        elif cmd=='/reset':
            self.store.event(chat,'reset',{'selected':s.get('selected')})
            self.store.save(chat,{k:v for k,v in s.items() if k in ('selected','group','debug')});await self.send(chat,'Начинаем новый тестовый диалог. Машина сохранена.')
        elif cmd=='/resume':
            s['paused']=False;self.store.save(chat,s);self.store.event(chat,'resume',{});await self.send(chat,'Тест продолжен. В клиентском контуре этот диалог оставался бы у человека.')
        elif cmd=='/debug':
            s['debug']=not s.get('debug');self.store.save(chat,s);await self.send(chat,'Разбор '+('включён' if s['debug'] else 'выключен'))
        elif cmd=='/status':
            row=self.selected(s);await self.send(chat,'Модель: '+MODEL+'\nРежим: отдельная лаборатория\nМашина: '+(row['listing']['title'] if row else 'не выбрана')+'\nПауза: '+str(bool(s.get('paused')))+'\nСнимок: '+self.catalog['created_at']+'\nРасход лаборатории: $%.5f'%self.store.spent())
        elif cmd=='/feedback':
            if not arg.strip():await self.send(chat,'Напиши /feedback и замечание к ответу.');return
            self.store.event(chat,'feedback',{'text':redact(arg),'selected':s.get('selected')});await self.send(chat,'Замечание сохранено вместе с тестовой перепиской.')
        elif cmd in ('/export','/results'):
            if cmd=='/results':
                path=self.runtime/'rehearsal.jsonl'
                if not path.exists():await self.send(chat,'Автоматических прогонов пока нет.');return
                content=json.dumps([json.loads(line) for line in path.read_text().splitlines() if line.strip()],ensure_ascii=False,indent=2).encode()
            else:content=json.dumps(self.store.transcript(chat),ensure_ascii=False,indent=2).encode()
            r=await self.tg.post('https://api.telegram.org/bot'+self.token+'/sendDocument',data={'chat_id':str(chat),'caption':'Полный журнал тестов: вопросы, ответы, решения Haiku и замечания.'},files={'document':('haiku-tests.json',content,'application/json')})
            if not r.json().get('ok'):raise RuntimeError('Export failed')
        else:await self.send(chat,'Неизвестная команда. /help')
    async def handle(self,update):
        m=update.get('message') or {};chat=(m.get('chat') or {}).get('id');sender=(m.get('from') or {}).get('id')
        if chat not in self.allowed or sender!=chat or m.get('chat',{}).get('type')!='private':return
        text=m.get('text') or ''
        if text.startswith('/'):
            await self.command(chat,text);return
        if not text:
            await self.send(chat,'В этой версии тестируем текст. Фото и голос не анализируются.');return
        if len(text)>4000:await self.send(chat,'Для теста отправь сообщение до 4000 символов.');return
        s=self.store.session(chat)
        if not self.selected(s):await self.send(chat,'Сначала выбери объявление: /cars или /cases');return
        await self.api('sendChatAction',chat_id=chat,action='typing')
        result=await respond(text,s,self.selected(s),self.catalog['company'],self.planner)
        self.store.save(chat,s)
        self.store.event(chat,'turn',{'user':redact(text),'selected':s.get('selected'),'group':s.get('group'),'result':asdict(result),'delivery':'pending'})
        if result.text:await self.send(chat,result.text)
        if result.action=='handoff':await self.send(chat,'🧪 Тест: здесь подключился бы менеджер. Реальная задача не создавалась. Для нового диалога /reset, для условного продолжения /resume.')
        elif result.action=='silence':await self.send(chat,'🧪 Тест: агент промолчал бы на эту реплику.')
        elif result.action=='error':await self.send(chat,'🧪 Не удалось получить ответ Haiku. Ошибка или лимит тестового контура; клиентские сервисы не затронуты.')
        if s.get('debug'):await self.send(chat,'🧪 Разбор\nНамерения: '+', '.join(result.intents)+'\nФакты: '+', '.join(result.facts)+'\nУточнить: '+', '.join(result.unknown)+'\nДействие: '+result.action+'\nСтоимость: $%.6f'%result.cost)
        self.store.event(chat,'delivery',{'update_id':update['update_id'],'status':'sent'})
    async def run(self):
        me=await self.api('getMe')
        if str(me['id'])!=self.cfg.get('LAB_EXPECTED_BOT_ID'):raise RuntimeError('Wrong Telegram bot')
        if str(me['id']) in self.cfg.get('LAB_FORBIDDEN_BOT_IDS','').split(','):raise RuntimeError('Production bot is forbidden')
        webhook=await self.api('getWebhookInfo')
        if webhook.get('url'):raise RuntimeError('Existing webhook detected; refusing to alter it')
        await self.api('setMyCommands',commands=[{'command':cmd,'description':desc} for cmd,desc in [('cars','Выбрать машину'),('cases','Сценарии из переписок'),('reset','Новый диалог'),('status','Проверить тестовый режим'),('debug','Разбор ответа'),('export','Выгрузить свою переписку'),('results','Автоматические прогоны'),('feedback','Оставить замечание'),('help','Как тестировать')]])
        print('LAB_READY @'+me['username'],flush=True)
        while True:
            try:
                offset=int(self.store.meta('offset') or 0)
                updates=await self.api('getUpdates',offset=offset,timeout=25,allowed_updates=['message'])
                for u in updates:
                    uid=u['update_id']
                    if self.store.claim(uid):
                        try:await self.handle(u);self.store.complete(uid)
                        except Exception as exc:
                            self.store.complete(uid,'failed');print('HANDLER_ERROR '+type(exc).__name__,flush=True)
                    self.store.meta('offset',uid+1)
            except Exception as exc:
                print('POLL_ERROR '+type(exc).__name__,flush=True);await asyncio.sleep(5)
    async def close(self):await self.planner.close();await self.tg.aclose()

async def main():
    os.umask(0o077);cfg=config()
    for key in ('LAB_TELEGRAM_TOKEN','LAB_OPENROUTER_KEY','LAB_ALLOWED_USERS','LAB_EXPECTED_BOT_ID','LAB_FORBIDDEN_BOT_IDS'):
        if not cfg.get(key):raise RuntimeError('Missing '+key)
    (ROOT/'runtime').mkdir(exist_ok=True)
    with (ROOT/'runtime'/'poller.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        lab=Lab(cfg)
        try:await lab.run()
        finally:await lab.close()

if __name__=='__main__':
    try:asyncio.run(main())
    except KeyboardInterrupt:pass
    except Exception as exc:
        print('LAB_STOPPED '+type(exc).__name__,flush=True)
        raise SystemExit(1)
