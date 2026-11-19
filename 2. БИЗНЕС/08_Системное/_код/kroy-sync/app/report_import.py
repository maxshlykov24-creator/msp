"""User-reviewed sales report scoped to a particular calculation and date range."""
from collections import defaultdict
from datetime import date
import re
from .db import now
from .order_feed import index
from .service import require,create_entity


def preview(s, data):
    platform=data.get('platform')
    key=data.get('key')
    require(platform in ('ozon','wb') and key in ('barcode','sku') and (key!='sku' or platform=='ozon'),'Выбери площадку и идентификатор отчёта',422)
    start,end=date.fromisoformat(data['start']),date.fromisoformat(data['end'])
    require(0<=(end-start).days<90,'Период отчёта до 90 дней',422)
    require(re.fullmatch(r'[a-f0-9]{64}',str(data.get('sha256',''))) and isinstance(data.get('file'),str) and 0<len(data['file'])<=255,'Проверь файл',422)
    rows=data.get('rows')
    require(isinstance(rows,list) and 0<len(rows)<=20000,'Нужны строки продаж и возвратов',422)
    codes,skus,_=index(s)
    lookup=codes if key=='barcode' else skus
    totals=defaultdict(lambda:{'sales':0,'returns':0})
    missing=set()
    for row in rows:
        code=str(row.get('code','')).strip()
        require(code and all(type(row.get(k)) is int and 0<=row[k]<=10**7 for k in ('sales','returns')),'Продажи и возвраты должны быть целыми количествами',422)
        matches=lookup.get(code,set())
        if len(matches)!=1:
            missing.add(code)
            continue
        line=totals[next(iter(matches))]
        for k in line: line[k]+=row[k]
    return dict(platform=platform,start=str(start),end=str(end),file=data['file'],sha256=data['sha256'],totals=dict(totals),unmatched=sorted(missing),records=len(rows))


def store_report(s,doc,data):
    report=preview(s,data)
    require(report['totals'] and not report['unmatched'],'Есть несопоставленные строки. Проверь коды в отчёте',422)
    create_entity(s,doc,'cut_report',{**report,'at':now().isoformat()})
    return {'id':doc}
