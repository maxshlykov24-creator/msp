#!/usr/bin/env python3
"""Срез МойСклад для живого дашборда: подтверждённые, шоурум, медиана отправки."""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import lib

OUT = Path(__file__).resolve().parents[1] / "дашборд" / "snapshot.json"
HTML = Path(__file__).resolve().parents[1] / "дашборд" / "2MY_дашборд_живой.html"

SHOWROOM = {lib.MS_STATE_SHOWROOM}
CONFIRMED_BUY = {lib.MS_STATE_CONFIRMED, lib.MS_STATE_DONE, lib.MS_STATE_SHOWROOM, "655b21e8-c447-11eb-0a80-08be002efc0e"}


def parse_moment(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace(" ", "T")[:19])
    except ValueError:
        return None


def attr(order: dict, aid: str):
    for a in order.get("attributes") or []:
        if a.get("id") == aid:
            return a.get("value")
    return None


def state_id(order: dict) -> str:
    return lib.href_id((order.get("state") or {}).get("meta"))


def channel_id(order: dict) -> str:
    return lib.href_id((order.get("salesChannel") or {}).get("meta"))


def main() -> None:
    ms = lib.MS()
    print("читаю заказы…")
    orders = ms.rows("/entity/customerorder", {"limit": 1000, "order": "moment,desc"}, pages=20)
    print("заказов в срезе", len(orders))
    by_state: dict[str, int] = defaultdict(int)
    by_channel: dict[str, int] = defaultdict(int)
    confirmed_sum = 0
    confirmed_n = 0
    showroom_n = 0
    showroom_sum = 0
    ship_all = []
    ship_fast = []
    ship_slow = []
    for o in orders:
        sid = state_id(o)
        by_state[sid] += 1
        cid = channel_id(o) or "нет"
        by_channel[cid] += 1
        sm = int(o.get("sum") or 0)
        if sid in CONFIRMED_BUY or sid == lib.MS_STATE_CONFIRMED:
            confirmed_n += 1
            confirmed_sum += sm
        if sid in SHOWROOM or cid == lib.MS_CHANNEL_SHOWROOM:
            showroom_n += 1
            showroom_sum += sm
        t_conf = attr(o, "ed14761e-dc0d-11ef-0a80-10cd00226b08")
        t_sent = attr(o, "ed14796a-dc0d-11ef-0a80-10cd00226b0c")
        d1 = parse_moment(t_conf if isinstance(t_conf, str) else None) or parse_moment(o.get("moment"))
        d2 = parse_moment(t_sent if isinstance(t_sent, str) else None)
        if d1 and d2 and d2 >= d1:
            days = (d2 - d1).total_seconds() / 86400
            ship_all.append(days)
            if days <= 2:
                ship_fast.append(days)
            else:
                ship_slow.append(days)

    def med(xs):
        return round(statistics.median(xs), 2) if xs else None

    snap = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "orders_in_slice": len(orders),
        "note": "Покупка = статусы подтверждён / выполнен / покупка в шоуруме / отправлен, не только отгрузка. Срок отправки: атрибуты Подтвержден→Отправлен, иначе moment. Наличие ≈ ≤2 дней, предзаказ >2 — эвристика, не выдуманный остаток на дату заказа.",
        "confirmed_count": confirmed_n,
        "confirmed_sum_kopecks": confirmed_sum,
        "showroom_count": showroom_n,
        "showroom_sum_kopecks": showroom_sum,
        "median_ship_days": med(ship_all),
        "median_ship_in_stock_days": med(ship_fast),
        "median_ship_preorder_days": med(ship_slow),
        "ship_sample": {"all": len(ship_all), "in_stock": len(ship_fast), "preorder": len(ship_slow)},
        "by_state": dict(by_state),
        "by_channel": dict(by_channel),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    rub = lambda k: f"{(k or 0)/100:,.0f} ₽".replace(",", " ")
    html = f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>2MY дашборд живой</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;background:#f7f7f7;color:#020202;margin:0;padding:32px}}
h1{{font-size:28px;margin:0 0 8px}} .sub{{color:#4f5354;margin-bottom:24px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}}
.card{{background:#fff;border:1px solid #e1e3e5;border-radius:12px;padding:16px}}
.k{{font-size:12px;color:#4f5354}} .v{{font-size:22px;font-weight:700}}
table{{width:100%;border-collapse:collapse;margin-top:24px;background:#fff}}
td,th{{border-bottom:1px solid #e1e3e5;padding:8px 10px;text-align:left;font-size:14px}}
.note{{margin-top:24px;font-size:13px;color:#4f5354;max-width:720px}}
</style></head><body>
<h1>2MyMoods — живой срез</h1>
<p class="sub">Собрано {snap['generated']}. Покупки по статусу МойСклад, не только по отгрузкам.</p>
<div class="grid">
<div class="card"><div class="k">Подтверждённые покупки</div><div class="v">{confirmed_n}</div></div>
<div class="card"><div class="k">Сумма покупок</div><div class="v">{rub(confirmed_sum)}</div></div>
<div class="card"><div class="k">Покупка в шоуруме</div><div class="v">{showroom_n}</div></div>
<div class="card"><div class="k">Шоурум, сумма</div><div class="v">{rub(showroom_sum)}</div></div>
<div class="card"><div class="k">Медиана отправки, дни</div><div class="v">{snap['median_ship_days'] if snap['median_ship_days'] is not None else "нет даты"}</div></div>
<div class="card"><div class="k">Наличие ≤2 дн</div><div class="v">{snap['median_ship_in_stock_days'] if snap['median_ship_in_stock_days'] is not None else "—"}</div></div>
<div class="card"><div class="k">Предзаказ >2 дн</div><div class="v">{snap['median_ship_preorder_days'] if snap['median_ship_preorder_days'] is not None else "—"}</div></div>
</div>
<p class="note">{snap['note']} Заказов в срезе: {len(orders)}. Excel пока этот JSON: дашборд/snapshot.json.</p>
<script>window.SNAPSHOT={json.dumps(snap, ensure_ascii=False)}</script>
</body></html>"""
    HTML.write_text(html, encoding="utf-8")
    print("записал", OUT, "и", HTML)


if __name__ == "__main__":
    main()
