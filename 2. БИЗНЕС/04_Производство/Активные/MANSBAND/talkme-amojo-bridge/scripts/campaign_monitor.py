"""Independent host watchdog. Private config/state are never committed."""
import json
import sys
import time
from pathlib import Path
from urllib.request import Request,urlopen

root=Path('/root/mansband-campaign-monitor')

def telegram(config,body):
    req=Request('https://api.telegram.org/bot'+config['token']+'/sendMessage',data=json.dumps({'chat_id':config['chat_id'],'text':body}).encode(),headers={'Content-Type':'application/json'})
    try:
        with urlopen(req,timeout=15) as r:return json.load(r).get('ok') is True
    except Exception:return False

def main():
    config=json.loads((root/'config.json').read_text())
    state_path=root/'state.json'
    state=json.loads(state_path.read_text()) if state_path.exists() else {}
    if '--test' in sys.argv:
        ok=telegram(config,'MANSBAND · Проверка внешнего мониторинга\nНаблюдение работает с отдельного сервера.\nПри недоступности обработчика рассылки придёт сигнал, после восстановления — подтверждение.')
        print(json.dumps({'test_alert_sent':ok}));return
    try:
        with urlopen(config['health_url'],timeout=15) as r:healthy=json.load(r).get('ok') is True
    except Exception:healthy=False
    stamp=time.time()
    if healthy:
        state['failures']=0
        if state.get('notified'):
            if telegram(config,'MANSBAND · Рассылка\nВосстановлено: обработчик снова отвечает.\nРежим отправки не изменён автоматически.'):
                state['notified']=False
    else:
        state['failures']=state.get('failures',0)+1
        if state['failures']>=3 and stamp-state.get('last_sent',0)>=1800:
            if telegram(config,'MANSBAND · Требует внимания\nОбработчик рассылки не отвечает или не обновляет состояние.\nПроверка выполнена с другого сервера.\nДействие: проверить сервер и очередь. Не запускать повторную отправку вручную до сверки журнала.'):
                state.update(notified=True,last_sent=stamp)
    state['checked_at']=stamp
    temp=state_path.with_suffix('.tmp');temp.write_text(json.dumps(state));temp.chmod(0o600);temp.replace(state_path)
    print(json.dumps({'healthy':healthy,'failures':state.get('failures',0),'incident_notified':state.get('notified',False)}))



# Read-only sales export; runs on the kassa host. Credentials remain in its API
# container. Only hashes and document IDs leave that host for the campaign ledger.
PURCHASE_EXPORT = r'''
const fs=require('fs'),crypto=require('crypto');
(async()=>{
 const rows=JSON.parse(fs.readFileSync(0,'utf8'));
 const base=process.env.MOYSKLAD_API_BASE.replace(/\/$/,'');
 const token=process.env.MOYSKLAD_API_TOKEN;
 const cache=new Map();
 async function get(path){
  const url=path.startsWith('https://')?path:base+path;
  if(!url.startsWith(base+'/'))throw Error('foreign_document_origin');
  if(cache.has(url))return cache.get(url);
  let r;
  for(let attempt=0;attempt<3;attempt++){
   await new Promise(resolve=>setTimeout(resolve,350));
   r=await fetch(url,{headers:{Authorization:'Bearer '+token,'Accept-Encoding':'gzip'},signal:AbortSignal.timeout(25000)});
   if(r.status!==429 || attempt===2)break;
   const wait=Math.max(5,Number(r.headers.get('Retry-After'))||15);
   if(wait>60)throw Error('document_rate_limit_deferred');
   await new Promise(resolve=>setTimeout(resolve,wait*1000));
  }
  if(!r.ok)throw Error('document_http_'+r.status);
  const d=await r.json();cache.set(url,d);return d;
 }
 const stamp=v=>v?String(v).replace(' ','T')+(String(v).match(/Z$|[+-]\d\d:\d\d$/)?'':'+03:00'):null;
 const records=[];
 for(const row of rows){
  const d=await get('/entity/demand/'+row.demand_id);
  const orderHref=d.customerOrder?.meta?.href;
  if(!orderHref)continue;
  const o=await get(orderHref);
  if(row.order_id && o.id!==row.order_id)throw Error('order_link_mismatch');
  if(d.applicable!==true)continue;
  if(o.applicable!==true || !(o.sum>0) || !(o.payedSum>=o.sum) || o.shippedSum>o.payedSum || d.sum>o.payedSum)continue;
  // Demand and order must have the same counterparty.
  if(!d.agent?.meta?.href || d.agent.meta.href!==o.agent?.meta?.href)throw Error('counterparty_mismatch');
  let refund=0;
  for(const ref of d.returns||[]){const x=await get(ref.meta.href);if(x.applicable===true)refund+=x.sum||0;}
  let p=String(row.phone||'').replace(/\D/g,'');if(p.length===11 && p.startsWith('8'))p='7'+p.slice(1);
  records.push({document_id:d.id,order_id:o.id,person:crypto.createHash('sha256').update(p).digest('hex'),amo_lead_id:row.amo_lead_id,
   posted:d.applicable===true,paid:true,cancelled:false,test:false,amount_kopecks:Math.round(d.sum),refund_kopecks:Math.round(refund),
   at:stamp(d.moment),order_created_at:stamp(o.created||row.created_at)});
 }
 const endpoint=process.env.MESSAGE_TEMPLATES_URL.replace(/\/$/,'')+'/internal/templates/campaign/purchases';
 const r=await fetch(endpoint,{method:'POST',headers:{'Content-Type':'application/json','X-Reminder-Key':process.env.MESSAGE_TEMPLATES_KEY},
  body:JSON.stringify({source:'kassa',checked_at:new Date().toISOString(),records}),signal:AbortSignal.timeout(25000)});
 if(!r.ok)throw Error('campaign_feed_http_'+r.status);
 console.log(JSON.stringify({source_rows:rows.length,verified_documents:records.length,result:await r.json()}));
})().catch(e=>{console.error(e.message);process.exit(1)});
'''

def purchases():
    import subprocess
    sql="""select coalesce(json_agg(x),'[]'::json) from (
        select ms_demand_id as demand_id,ms_order_id as order_id,amo_lead_id,
          data->>'clientPhone' as phone,created_at
        from deals where ms_demand_id is not null and coalesce(data->>'isTest','false')!='true'
          and coalesce(data->>'kind','') not in ('return','exchange','cert_digital','cert_plastic')
    ) x"""
    data=subprocess.run(['docker','exec','mansband-kassa-db-1','sh','-c',
        'exec psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "$1"','sh',sql],capture_output=True,check=True).stdout
    p=subprocess.run(['docker','exec','-i','mansband-kassa-api-1','node','-e',PURCHASE_EXPORT],input=data,capture_output=True)
    if p.returncode:
        config=json.loads((root/'config.json').read_text())
        state=root/'purchase-failure.json'
        old=json.loads(state.read_text()) if state.exists() else {}
        if time.time()-old.get('notified',0)>=1800:
            if telegram(config,'MANSBAND · Учёт покупок\nСверка документов временно недоступна. Статистика покупок может быть неполной.\nОтправки не повторяются. Действие: проверить подключение кассы и МойСклад.'):
                state.write_text(json.dumps({'notified':time.time()}));state.chmod(0o600)
        raise RuntimeError('purchase_sync_failed')
    state=root/'purchase-failure.json'
    old=json.loads(state.read_text()) if state.exists() else {}
    if old.get('notified'):
        config=json.loads((root/'config.json').read_text())
        if telegram(config,'MANSBAND · Учёт покупок восстановлен\nОплаченные проведённые документы кассы снова сверяются с МойСклад.\nСтатистика обновлена. Продажи вне кассы требуют отдельной сверки.'):
            state.write_text(json.dumps({'notified':0,'recovered_at':time.time()}));state.chmod(0o600)
    print(p.stdout.decode().strip())

if __name__=='__main__':
    purchases() if '--purchases' in sys.argv else main()
