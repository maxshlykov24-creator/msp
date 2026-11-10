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

if __name__=='__main__':main()
