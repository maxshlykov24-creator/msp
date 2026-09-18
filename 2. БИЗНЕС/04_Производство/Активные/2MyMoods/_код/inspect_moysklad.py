#!/usr/bin/env python3
"""2MY — снять AS-IS МойСклад. Ничего не пишет, статусы не меняет."""

from __future__ import annotations

import gzip
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / "06_Доступы" / ".env"
BASE = "https://api.moysklad.ru/api/remap/1.2"


def load_env() -> dict[str, str]:
    out: dict[str, str] = {}
    if ENV.is_file():
        for raw in ENV.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip("'").strip('"')
    return out


def req(token: str, path: str, params: dict | None = None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    r = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/json;charset=utf-8",
        "Accept-Encoding": "gzip",
        "User-Agent": "MSProduct-2MY/1.0 (max.shlykov24@gmail.com)",
    })
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"error": raw.decode(errors="replace")[:400]}


def href_id(meta: dict | None) -> str:
    if not meta:
        return ""
    href = (meta.get("href") or "").rstrip("/")
    return href.rsplit("/", 1)[-1]


def main() -> None:
    env = load_env()
    token = (env.get("MOYSKLAD_TOKEN") or "").strip()
    if not token:
        sys.exit(f"Нет MOYSKLAD_TOKEN в {ENV}")

    st, me = req(token, "/context/employee")
    if not (200 <= st < 300):
        sys.exit(f"МойСклад недоступен [{st}]: {json.dumps(me, ensure_ascii=False)[:400]}")
    print(f"Сотрудник: {me.get('name')}  uid={me.get('uid')}  [{st}]")

    st, acc = req(token, "/context/companysettings")
    print(f"Компания настроена [{st}]  валюта по умолчанию есть")

    print("\n=== Склады ===")
    st, b = req(token, "/entity/store", {"limit": 100})
    stores = b.get("rows") or []
    print(f"[{st}] {len(stores)}")
    for s in stores:
        print(f"  {s.get('name')}  id={s.get('id')}  archived={s.get('archived')}")

    print("\n=== Статусы заказа покупателя ===")
    st, b = req(token, "/entity/customerorder/metadata")
    states = (b.get("states") or [])
    print(f"[{st}] {len(states)}")
    for s in states:
        print(f"  {s.get('name')}  id={s.get('id')}  color={s.get('color')}")

    print("\n=== Атрибуты заказа ===")
    st, b = req(token, "/entity/customerorder/metadata/attributes", {"limit": 100})
    print(f"[{st}] {(b.get('meta') or {}).get('size')}")
    for a in b.get("rows") or []:
        if not isinstance(a, dict):
            print(f"  {a}")
            continue
        print(f"  {a.get('name')}  id={a.get('id')}  type={a.get('type')}")

    print("\n=== Каналы продаж ===")
    st, b = req(token, "/entity/saleschannel", {"limit": 100})
    print(f"[{st}] {b.get('meta', {}).get('size')}")
    for row in b.get("rows") or []:
        print(f"  {row.get('name')}  id={row.get('id')}")

    print("\n=== Заказы: сколько всего и по статусам ===")
    st, b = req(token, "/entity/customerorder", {"limit": 1})
    total = (b.get("meta") or {}).get("size")
    print(f"всего заказов: {total} [{st}]")
    time.sleep(0.3)
    for s in states:
        time.sleep(0.22)
        st, b = req(token, "/entity/customerorder", {
            "limit": 1,
            "filter": f"state={BASE}/entity/customerorder/metadata/states/{s['id']}",
        })
        print(f"  {s.get('name')}: {(b.get('meta') or {}).get('size')} [{st}]")

    print("\n=== Остатки: склады с количеством ===")
    st, b = req(token, "/report/stock/bystore", {"limit": 5})
    print(f"строк отчёта meta.size={(b.get('meta') or {}).get('size')} [{st}]")
    time.sleep(0.3)
    st, b = req(token, "/report/stock/all", {"limit": 1, "groupBy": "product"})
    print(f"позиций с остатком: {(b.get('meta') or {}).get('size')} [{st}]")

    print("\n=== Вебхуки ===")
    st, b = req(token, "/entity/webhook", {"limit": 100})
    rows = b.get("rows") or []
    print(f"[{st}] {len(rows)}")
    for w in rows:
        url = (w.get("url") or "").split("?")[0]
        print(f"  {w.get('action')} {w.get('entityType')} → {url}")

    print("\n=== Последние 5 заказов (номер, статус, сумма, позиций) ===")
    st, b = req(token, "/entity/customerorder", {"limit": 5, "order": "moment,desc"})
    for row in b.get("rows") or []:
        state = ((row.get("state") or {}).get("meta") or {}).get("href", "")
        print(
            f"  {row.get('name')}  {row.get('moment')}  sum={row.get('sum')}  "
            f"positions={row.get('positions', {}).get('meta', {}).get('size')}  "
            f"state={href_id(row.get('state', {}).get('meta'))}"
        )


if __name__ == "__main__":
    main()
