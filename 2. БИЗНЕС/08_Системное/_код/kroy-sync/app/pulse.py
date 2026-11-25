"""Whether the last 30 days through yesterday can be trusted. No sale bodies and no secrets."""
from datetime import datetime, time, timedelta, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session
from .db import Balance, Entity, Item
from .sales import MSK, wb_gap_text, window_refs

OZON_PHASES=('ozon-open','ozon-fbs','ozon-fbo','ozon-returns','ozon-history-fbs','ozon-history-fbo')
WB_PHASES=('wb-catalog','wb-new','wb-fbs','wb-statistics','wb-sales')
PHASE_NAMES={
    'ozon-open':'открытые отправления Ozon','ozon-fbs':'сборки Ozon','ozon-fbo':'отправления ФБО Ozon',
    'ozon-returns':'возвраты Ozon','ozon-history-fbs':'история сборок Ozon','ozon-history-fbo':'история ФБО Ozon',
    'wb-catalog':'каталог Wildberries','wb-new':'новые заказы Wildberries','wb-fbs':'сборки Wildberries',
    'wb-statistics':'статистика Wildberries','wb-sales':'продажи Wildberries',
}
ROW_LIMIT=200


def ru(iso):
    year,month,day=str(iso).split('-')
    return day+'.'+month+'.'+year


def _moment(moment):
    if moment is None:return datetime.now(MSK)
    if moment.tzinfo is None:return moment.replace(tzinfo=MSK)
    return moment.astimezone(MSK)


def _age(value,moment):
    parsed=datetime.fromisoformat(value)
    if parsed.tzinfo is None:parsed=parsed.replace(tzinfo=timezone.utc)
    return (moment.astimezone(timezone.utc)-parsed.astimezone(timezone.utc)).total_seconds()


def _stale(data,moment):
    if not data or not data.get('at') or not data.get('data_at'):return True
    limit=2*int(data.get('interval_seconds') or 3600)
    return _age(data['at'],moment)>limit or _age(data['data_at'],moment)>limit


def _hidden(key):
    return key.startswith(('limit:','report:')) or key in ('wb-identity','cycle','request','accounting-start')


def _safe(text):
    text=str(text or '')[:180]
    lowered=text.lower()
    if any(word in lowered for word in ('token','api-key','authorization','client-id','secret','bearer')):return ''
    return text


def _choose(refs,platform,start):
    rows=[row for row in refs if row.get('platform')==platform and row.get('complete') and row.get('verified') and not row.get('identity_pending') and row.get('start') and row.get('end') and row['start']<=start]
    if not rows:return None
    return max(rows,key=lambda row:(row['end'],row.get('at') or ''))


def _coverage(window,target,floor):
    if window is None:return 'failure'
    if window['end']>=target:return 'ok'
    if floor<target and window['end']>=floor:return 'waiting'
    return 'failure'


def _sales_summary(tone,window,target):
    if tone=='ok':return 'Загрузка дошла до '+ru(target)+'.'
    if tone=='waiting' and window:return 'До 03:30 покрытие по '+ru(window['end'])+' ещё не сбой. Первый запуск в 03:00.'
    if window:return 'Загрузка не дошла до '+ru(target)+'. Последнее окно по '+ru(window['end'])+'.'
    return 'Нет сверенного окна за последние 30 дней.'


def _row(item):
    return dict(item_id=item.id,name=item.name,units=None)


def _orders(phases,prefix,known):
    chosen={key:value for key,value in phases.items() if key in known or key.startswith(prefix)}
    states=[value['state'] for value in chosen.values()]
    if any(state=='error' for state in states):
        names=[PHASE_NAMES.get(key,key) for key,value in chosen.items() if value['state']=='error']
        detail='. '.join(_safe(value['error']) for value in chosen.values() if value['state']=='error' and _safe(value['error']))
        summary='Ошибка чтения: '+', '.join(names)+('.' if not detail else '. '+detail)
        return 'failure',summary
    if any(state in ('waiting','running') for state in states):
        return 'waiting','Пауза площадки. Это не сбой.'
    if not states:return 'waiting','Фазы заказов ещё не записаны.'
    return 'ok','Чтение дошло.'


def _run(data,moment):
    if not data or data.get('state') not in ('running','done','error'):
        return 'waiting','Отметка запуска ещё не записывалась.'
    if data['state']=='running' and data.get('started_at'):
        limit=15*60 if data.get('source')=='manual' else 5*60
        if _age(data['started_at'],moment)>limit:return 'failure','Сверка не завершилась.'
        return 'waiting','Сверка ещё идёт.'
    if data['state']=='error':
        detail=_safe(data.get('error'))
        return 'failure',detail or 'Сверка завершилась с ошибкой.'
    return 'ok','Последняя сверка завершилась.'


def build_pulse(engine,moment=None):
    moment=_moment(moment)
    today=moment.date()
    target=str(today-timedelta(days=1))
    start=str(today-timedelta(days=30))
    floor=str(today-timedelta(days=2)) if moment.time()<time(3,30) else target
    refs=window_refs(engine)
    ozon=_choose(refs,'ozon',start)
    wb=_choose(refs,'wb',start)
    ozon_tone=_coverage(ozon,target,floor)
    wb_tone=_coverage(wb,target,floor)
    with Session(engine) as s:
        garments=list(s.scalars(select(Item).where(Item.kind=='garment')))
        known_stock=set()
        for balance in s.scalars(select(Balance)):
            if balance.known and ':' not in (balance.location or ''):known_stock.add(balance.item_id)
        ozon_fbo=s.get(Entity,'fbo-stock:ozon')
        wb_fbo=s.get(Entity,'fbo-stock:wb')
        ozon_fbo=ozon_fbo.data if ozon_fbo else None
        wb_fbo=wb_fbo.data if wb_fbo else None
        phases={}
        for entity in s.scalars(select(Entity).where(Entity.kind=='order_sync')):
            key=entity.id.split(':',1)[1] if entity.id.startswith('order-sync:') else entity.id
            if _hidden(key):continue
            data=entity.data or {}
            phases[key]=dict(state=data.get('state') or '',error=data.get('error') if data.get('state')=='error' else '')
        run=s.get(Entity,'sales-run')
        run=run.data if run else None
    topics=[]
    if ozon:
        proof=ozon.get('catalog_proof') or {}
        gaps=ozon.get('gaps') if isinstance(ozon.get('gaps'),list) else None
        if proof.get('catalog_products') is not None and proof.get('matched_products') is not None:
            card_count=int(proof['catalog_products'])-int(proof['matched_products'])
        elif gaps is not None:card_count=len(gaps)
        else:card_count=0
        units=int(ozon.get('unmatched') or 0)
        if card_count or units:
            if card_count and units:title=str(card_count)+' карточек Ozon нет среди наших товаров. '+str(units)+' шт. не вошли в расчёт.'
            elif card_count:title=str(card_count)+' карточек Ozon нет среди наших товаров.'
            else:title=str(units)+' шт. Ozon без точного сопоставления не вошли в расчёт.'
            rows=[dict(offer_id=row.get('offer_id') or '',name=row.get('name') or '',units=row.get('units')) for row in (gaps or [])]
            topics.append(dict(id='ozon-cards',tone='review',title=title,effect='Продажи этих карточек не входят в расчёт. Ноль по нашим товарам из-за этого не подтверждён.',count=card_count or units,units=units,rows=rows[:ROW_LIMIT],more=max(0,len(rows)-ROW_LIMIT),action=None))
        if proof.get('all_products_matched') is not True:
            covered=set(ozon.get('covered_items') or [])
            missing=[item for item in garments if item.id not in covered]
            if missing:
                topics.append(dict(id='ozon-local',tone='review',title=str(len(missing))+' товаров без подтверждённой карточки Ozon',effect='Рекомендации нет. Ноль не подставлен.',count=len(missing),units=None,rows=[_row(item) for item in sorted(missing,key=lambda item:item.name)][:ROW_LIMIT],more=max(0,len(missing)-ROW_LIMIT),action='map'))
    if wb and wb_tone=='ok':
        text=wb_gap_text(wb,start,target)
        if text:
            from .sales import unmatched_between
            count=unmatched_between(wb,start,target)
            topics.append(dict(id='wb-lines',tone='review',title=text,effect='Эти строки не входят в расчёт и не заменены нулём.',count=count,units=count,rows=[],more=0,action=None))
    elif wb:
        text=wb_gap_text(wb,wb['start'],wb['end'])
        if text:
            topics.append(dict(id='wb-lines',tone='review',title=text,effect='Эти строки не входят в расчёт и не заменены нулём.',count=int(wb.get('unmatched') or 0),units=int(wb.get('unmatched') or 0),rows=[],more=0,action=None))
    bare=[item for item in garments if not (item.barcodes or [])]
    if bare:
        topics.append(dict(id='wb-barcode',tone='review',title=str(len(bare))+' товаров без штрихкода',effect='Продажи Wildberries по ним не сверяются. Ноль не подставлен.',count=len(bare),units=None,rows=[_row(item) for item in sorted(bare,key=lambda item:item.name)][:ROW_LIMIT],more=max(0,len(bare)-ROW_LIMIT),action='map'))
    ozon_quantities=(ozon_fbo or {}).get('quantities') or {}
    if ozon and not _stale(ozon_fbo,moment) and ozon_fbo:
        listed=set(ozon.get('covered_items') or [])-set(ozon.get('not_listed_items') or [])
        absent=[item for item in garments if item.id in listed and item.id not in ozon_quantities]
        if absent:
            topics.append(dict(id='ozon-fbo-partial',tone='review',title='Нет остатка ФБО Ozon у '+str(len(absent))+' позиций, где карточка уже есть',effect='Этот остаток не вычитается и не заменён нулём.',count=len(absent),units=None,rows=[_row(item) for item in sorted(absent,key=lambda item:item.name)][:ROW_LIMIT],more=max(0,len(absent)-ROW_LIMIT),action=None))
    if ozon_fbo is None:fbo_tone,fbo_summary='failure','Снимок ФБО Ozon не загружен.'
    elif _stale(ozon_fbo,moment):fbo_tone,fbo_summary='failure','Снимок ФБО Ozon старше двух своих интервалов.'
    else:fbo_tone,fbo_summary='ok','Свежий остаток у '+str(len(ozon_quantities))+' позиций.'
    order_ozon=_orders(phases,'ozon-',OZON_PHASES)
    order_wb=_orders(phases,'wb-',WB_PHASES)
    run_tone,run_summary=_run(run,moment)
    sources=[
        dict(id='ozon-sales',title='Продажи Ozon',tone=ozon_tone,summary=_sales_summary(ozon_tone,ozon,target),at=ozon.get('at') if ozon else None),
        dict(id='wb-sales',title='Продажи Wildberries',tone=wb_tone,summary=_sales_summary(wb_tone,wb,target),at=wb.get('at') if wb else None),
        dict(id='ozon-fbo',title='ФБО Ozon',tone=fbo_tone,summary=fbo_summary,at=(ozon_fbo or {}).get('at')),
        dict(id='orders-ozon',title='Заказы Ozon',tone=order_ozon[0],summary=order_ozon[1],at=None),
        dict(id='orders-wb',title='Заказы Wildberries',tone=order_wb[0],summary=order_wb[1],at=None),
        dict(id='sales-run',title='Сверка продаж',tone=run_tone,summary=run_summary,at=(run or {}).get('finished_at') or (run or {}).get('started_at')),
    ]
    limits=[
        dict(id='wb-finance',title='Финансовый отчёт Wildberries',summary='Продажи Wildberries оперативные. Итоговый финансовый отчёт с ними не сверён.'),
    ]
    if wb_fbo is None:
        limits.insert(0,dict(id='wb-fbo',title='ФБО Wildberries',summary='Остатки ФБО Wildberries не загружаются. Крой их не вычитает и не заменяет нулём.'))
    elif _stale(wb_fbo,moment):
        sources.append(dict(id='wb-fbo',title='ФБО Wildberries',tone='failure',summary='Снимок ФБО Wildberries старше двух своих интервалов.',at=wb_fbo.get('at')))
    unknown=[item for item in garments if item.id not in known_stock]
    if unknown:
        limits.append(dict(id='own-stock',title='Свой остаток',summary='Свой остаток не внесён у '+str(len(unknown))+' позиций. Для рекомендации он принят за 0. Складской учёт не изменён.',count=len(unknown)))
    badge=sum(1 for source in sources if source['tone']=='failure')+len(topics)
    return dict(period=dict(start=start,end=target,text=ru(start)+' по '+ru(target)),question='Можно ли верить продажам за последние 30 дней по вчера.',badge=badge,sources=sources,topics=topics,limits=limits)
