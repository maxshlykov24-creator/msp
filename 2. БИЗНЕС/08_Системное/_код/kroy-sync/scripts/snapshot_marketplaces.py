"""Run in stage folder on host; credentials read from legacy env, never copied or emitted."""
import sys
import os
import json
from datetime import datetime, timezone, timedelta
sys.path.insert(0, '/opt/kroy-sync')
import config
sys.path.insert(0, '/opt/kroy-app-stage')
from app.connectors import ReadClient, ozon_postings, ozon_stocks, wb_new_orders

now = datetime.now(timezone.utc)
since = (now - timedelta(days=14)).isoformat()
wb = ReadClient('wb', {'Authorization': os.environ['WB_TOKEN']})
ozon = ReadClient('ozon', {'Api-Key': os.environ['OZON_API_KEY'], 'Client-Id': os.environ['OZON_CLIENT_ID']})
result = {'started_at': now.isoformat(), 'since': since, 'until': now.isoformat(), 'mode': 'observation',
          'wb_scope': 'new_orders_only', 'wb_orders': wb_new_orders(wb),
          'ozon_postings': ozon_postings(ozon, since, now.isoformat()), 'ozon_stocks': ozon_stocks(ozon)}
result['finished_at'] = datetime.now(timezone.utc).isoformat()
print(json.dumps(result, ensure_ascii=False))
