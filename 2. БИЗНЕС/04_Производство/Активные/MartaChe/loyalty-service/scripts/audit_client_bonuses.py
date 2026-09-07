#!/usr/bin/env python3
"""Аудит бонусов клиента MartaChe: три независимых числа и их расхождение.

  1. КАРТОЧКА МС  — доп. поля контрагента (что видит менеджер).
  2. ТРАНЗАКЦИИ МС — факт по bonustransaction (что реально проведено в МойСклад).
  3. ПЕРЕСЧЁТ      — реплей заказов клиента по правилам ПЛ (что должно быть).

Только GET-запросы, ничего не пишет в МойСклад.

Запуск:  python3 scripts/audit_client_bonuses.py "Хайкичева"
"""
from __future__ import annotations

import math
import os
import re
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import get_settings
from app.moysklad_client import MoySkladClient
from app.tier_manager import TIER_RATES, tier_index_from_annual_sum, tier_name_ru

NOW = datetime.now(timezone.utc)
# Remap 1.2 отдаёт даты без смещения, в часовом поясе аккаунта (у MartaChe — Москва).
MSK = timezone(timedelta(hours=3))
SEARCH = sys.argv[1] if len(sys.argv) > 1 else "Хайкичева"

# Из spend_engine: заказы-исключения из лимита 30%
SPEND_LIMIT_EXEMPT = {"site_1871304105", "38051df5-a1e3-11f1-0a80-05b0001dbad7"}


def parse_dt(v) -> datetime | None:
    if not v:
        return None
    s = str(v).replace(" ", "T")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=MSK)
    return dt


def fmt_dt(dt: datetime | None) -> str:
    if not dt:
        return "—"
    return dt.astimezone(MSK).strftime("%Y-%m-%d %H:%M")


def attr_value(cp: dict, attr_id: str):
    for a in cp.get("attributes") or []:
        if a.get("id") == attr_id:
            v = a.get("value")
            if isinstance(v, dict):
                return v.get("name") or v.get("value") or str(v)
            return v
    return None


def get_all(client: MoySkladClient, path: str, params: dict) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    while True:
        p = dict(params, limit=100, offset=offset)
        data = client.get(path, params=p)
        batch = data.get("rows") or []
        rows.extend(batch)
        if len(batch) < 100:
            break
        offset += 100
    return rows


def line_rub(price_kop: float, qty: float, disc_pct: float) -> int:
    kop = int(round(float(price_kop) * float(qty) * (1.0 - max(0.0, min(100.0, disc_pct)) / 100.0)))
    return kop // 100


def order_lines(order: dict) -> list[dict]:
    """Позиции заказа: id, assortment_id, gross_kop, price, qty, discount_total."""
    out = []
    pos_block = order.get("positions") or {}
    for pos in pos_block.get("rows") or []:
        a = pos.get("assortment") or {}
        a_meta = (a.get("meta") or {}).get("href", "")
        out.append({
            "id": str(pos.get("id") or ""),
            "assortment_id": a_meta.rstrip("/").split("/")[-1],
            "price": float(pos.get("price") or 0),
            "qty": float(pos.get("quantity") or 0),
            "discount": float(pos.get("discount") or 0.0),
        })
    return out


def main() -> None:
    settings = get_settings()
    client = MoySkladClient()

    # --- 0. Клиент -----------------------------------------------------------
    found = client.search_counterparties(SEARCH, limit=20)
    if not found:
        print(f"Контрагент «{SEARCH}» не найден")
        return
    cp = None
    for c in found:
        if SEARCH.lower() in (c.get("name") or "").lower():
            cp = c
            break
    cp = cp or found[0]
    cp_id = cp["id"]
    cp = client.fetch_counterparty(cp_id)
    cp_href = cp["meta"]["href"]
    print(f"Клиент: {cp.get('name')}  (id {cp_id})")
    print(f"  телефон: {(cp.get('phone') or '—')}  email: {(cp.get('email') or '—')}")

    card_tier = attr_value(cp, settings.attr_loyalty_tier)
    card_status = attr_value(cp, settings.attr_loyalty_status)
    card_active = attr_value(cp, settings.attr_active_bonuses)
    card_pending = attr_value(cp, settings.attr_pending_bonuses)
    print("\n=== 1. КАРТОЧКА МС (доп. поля, пишет сервис) ===")
    print(f"  Уровень:            {card_tier}")
    print(f"  Статус:             {card_status}")
    print(f"  Активные бонусы:    {card_active}")
    print(f"  Ожидают активации:  {card_pending}")
    # исторические поля ПЛ по имени
    meta = client.get("/entity/counterparty/metadata")
    attrs_meta = meta.get("attributes") or []
    if isinstance(attrs_meta, dict):
        attrs_meta = attrs_meta.get("rows") or []
    for a in attrs_meta:
        if not isinstance(a, dict):
            continue
        if (a.get("name") or "") in ("Дата регистрации ПЛ", "Сумма покупок ПЛ"):
            print(f"  {a['name']}: {attr_value(cp, a['id'])}")

    # --- 1. Транзакции МС ----------------------------------------------------
    txs = get_all(client, "/entity/bonustransaction", {"filter": f"agent={cp_href}"})
    earn_active = earn_pending = spend_total = 0
    tx_rows = []
    for t in txs:
        ttype = t.get("transactionType")
        val = int(t.get("bonusValue") or 0)
        exec_dt = parse_dt(t.get("executionDate"))
        created = parse_dt(t.get("created")) or parse_dt(t.get("moment"))
        ext = t.get("externalCode") or ""
        parent = ((t.get("parentDocument") or {}).get("meta") or {}).get("href", "")
        parent_id = parent.rstrip("/").split("/")[-1] if parent else ""
        # сервис не ставит parentDocument — вытаскиваем имя заказа из названия операции
        m = re.search(r"заказ[а]?\s+([A-Za-z0-9_\-]+)", t.get("name") or "")
        order_name = m.group(1) if m else ""
        is_active = exec_dt is None or exec_dt <= NOW
        if ttype == "EARNING":
            if is_active:
                earn_active += val
            else:
                earn_pending += val
        elif ttype == "SPENDING":
            spend_total += val
        tx_rows.append({
            "created": created, "type": ttype, "val": val, "exec": exec_dt,
            "ext": ext, "parent_id": parent_id, "order_name": order_name,
            "name": t.get("name") or "",
        })
    tx_rows.sort(key=lambda r: (r["created"] or NOW))
    balance_ms = earn_active - spend_total

    print("\n=== 2. ТРАНЗАКЦИИ МС (bonustransaction, факт) ===")
    print(f"  Начислено активных:   {earn_active}")
    print(f"  Начислено отложенных: {earn_pending}")
    print(f"  Списано всего:        {spend_total}")
    print(f"  => Баланс активных по МС: {balance_ms}   (+{earn_pending} в ожидании)")
    print("\n  Журнал транзакций:")
    for r in tx_rows:
        src = "сервис" if r["ext"] == settings.loyalty_external_code else ("РУЧНАЯ/МИГРАЦИЯ" if not r["ext"] else r["ext"])
        pend = "" if (r["exec"] is None or r["exec"] <= NOW) else f" [актив. {fmt_dt(r['exec'])}]"
        print(f"    {fmt_dt(r['created'])}  {r['type']:<8} {r['val']:>6}  {src:<16} заказ:{r['parent_id'][:8] or '—'}{pend}  {r['name'][:60]}")

    # --- 2. Заказы + пересчёт -------------------------------------------------
    orders = get_all(client, "/entity/customerorder", {
        "filter": f"agent={cp_href}",
        "expand": "state,positions,positions.assortment,positions.assortment.productFolder",
        "order": "moment",
    })
    print(f"\n=== 3. ПЕРЕСЧЁТ ПО ЗАКАЗАМ ({len(orders)} шт.) ===")

    # возвраты по заказам
    returns_by_order: dict[str, list[dict]] = {}
    for o in orders:
        st = ((o.get("state") or {}).get("name") or "")
        if st in (settings.status_return_full, settings.status_return_partial):
            rets = client.fetch_salesreturns_for_order(o["meta"]["href"])
            full_rets = []
            for r in rets:
                full_rets.append(client.fetch_salesreturn(r["id"]))
            returns_by_order[o["id"]] = full_rets

    annual = 0
    replay_earn = replay_spend = 0
    delay = timedelta(days=int(settings.bonus_delay_days))
    limit_pct = max(0, min(100, int(settings.loyalty_spend_percent_limit)))

    # Приветственный бонус берём по факту транзакции, а не по догадке о регистрации.
    welcome_fact = sum(r["val"] for r in tx_rows
                       if r["type"] == "EARNING" and "приветственные баллы" in r["name"].lower())
    earn_tx_by_order: dict[str, int] = {}
    for r in tx_rows:
        if r["type"] == "EARNING" and r["order_name"] and "возврат списания" not in r["name"].lower():
            earn_tx_by_order[r["order_name"]] = earn_tx_by_order.get(r["order_name"], 0) + r["val"]

    print(f"  {'заказ':<22} {'дата':<11} {'статус':<18} {'сумма':>8} {'уровень':>10} {'расчёт':>7} {'фактМС':>7} {'списан':>7}")
    for o in orders:
        oid = o["id"]
        name = o.get("name") or ""
        moment = parse_dt(o.get("moment")) or NOW
        state = ((o.get("state") or {}).get("name") or "")
        lines = order_lines(o)
        # intent списания из доп. поля заказа
        intent = 0
        for a in o.get("attributes") or []:
            if a.get("id") == settings.attr_order_spend_bonuses:
                try:
                    intent = int(float(a.get("value") or 0))
                except (TypeError, ValueError):
                    intent = 0

        # восстановление «исходных» скидок: если было списание, сервис размазал
        # его по discount позиций. total_orig = total_after + spend_cap (точно, если
        # ни одна строка не упёрлась в потолок).
        total_after = sum(line_rub(l["price"], l["qty"], l["discount"]) for l in lines)
        # реально списанное по этому заказу из транзакций МС (по имени заказа в названии)
        spent_tx = sum(r["val"] for r in tx_rows if r["type"] == "SPENDING" and (r["parent_id"] == oid or (r["order_name"] and r["order_name"] == name)))
        refunded_tx = sum(r["val"] for r in tx_rows if r["type"] == "EARNING" and "возврат списания" in r["name"].lower() and (r["parent_id"] == oid or (r["order_name"] and r["order_name"] == name)))
        spent_order = max(spent_tx - refunded_tx, intent if spent_tx == 0 else 0)

        earn = 0
        tier_idx = tier_index_from_annual_sum(annual)
        if state == settings.status_delivered:
            # вычесть частичные возвраты из количества строк
            ret_qty: dict[str, float] = {}
            for r in returns_by_order.get(oid, []):
                for pos in ((r.get("positions") or {}).get("rows") or []):
                    a_href = (((pos.get("assortment") or {}).get("meta") or {}).get("href", ""))
                    aid = a_href.rstrip("/").split("/")[-1]
                    ret_qty[aid] = ret_qty.get(aid, 0.0) + float(pos.get("quantity") or 0)
            total_orig = total_after + spent_order
            factor = (total_orig / (total_orig - spent_order)) if spent_order and total_orig > spent_order else 1.0
            full_pct, disc_pct = TIER_RATES[tier_index_from_annual_sum(annual)]  # временно, ниже пересчёт
            # база под кэшбэк: восстановленные исходные строки минус оплаченное бонусами
            elig = []
            for l in lines:
                qty = max(0.0, l["qty"] - ret_qty.get(l["assortment_id"], 0.0))
                if qty <= 0:
                    continue
                after = line_rub(l["price"], qty, l["discount"])
                orig_line = int(round(after * factor))
                gross = int(l["price"] * qty) // 100
                orig_dp = (1.0 - orig_line / gross) * 100.0 if gross > 0 else 0.0
                discounted = orig_dp > 0.5  # исходная скидка строки (без нашей надбавки)
                elig.append((orig_line, discounted))
            elig_total = sum(x[0] for x in elig)
            scale = 1.0
            if elig_total > 0 and spent_order > 0:
                scale = max(0.0, (elig_total - min(spent_order, elig_total)) / elig_total)
            cashable = int(math.floor(elig_total * scale + 1e-9))
            tier_idx = tier_index_from_annual_sum(annual + cashable)
            full_pct, disc_pct = TIER_RATES[tier_idx]
            for orig_line, discounted in elig:
                c = int(math.floor(orig_line * scale + 1e-9))
                if c <= 0:
                    continue
                earn += math.ceil(c * (disc_pct if discounted else full_pct) / 100)
            annual += cashable

        # списание: cap = min(intent, баланс, 30% от суммы до бонусной скидки)
        cap = 0
        if intent > 0:
            total_for_limit = total_after + spent_order
            cap = intent
            if name not in SPEND_LIMIT_EXEMPT and oid not in SPEND_LIMIT_EXEMPT:
                cap = min(cap, (total_for_limit * limit_pct) // 100)
        # факт списания берём из транзакций (это то, что реально ушло)
        spent_fact = spent_tx - refunded_tx
        replay_spend += spent_fact
        replay_earn += earn

        flag = ""
        if intent > 0 and spent_fact != cap:
            flag = f"  ⚠ intent={intent} cap={cap} факт={spent_fact}"
        earn_fact = earn_tx_by_order.get(name, 0)
        diff_mark = " ⚠" if earn_fact != earn and state == settings.status_delivered else ""
        print(f"  {name:<22} {fmt_dt(moment)[:10]:<11} {state:<18} {total_after:>8} {tier_name_ru(tier_idx):>10} {earn:>7} {earn_fact:>7} {spent_fact:>7}{diff_mark}{flag}")

    welcome = int(welcome_fact)
    if welcome:
        print(f"  + приветственный бонус (факт по МС): {welcome}")
    else:
        print("  приветственный бонус не начислялся")

    replay_total = replay_earn + welcome
    replay_balance = replay_total - replay_spend
    print("\n=== ИТОГ ПЕРЕСЧЁТА ===")
    print(f"  Начислено по заказам: {replay_earn}  (+{welcome} приветствие) = {replay_total}")
    print(f"  Списано по факту МС:  {replay_spend}")
    print(f"  => Должно быть активных: ≈{replay_balance}  (минус ещё не активированные 15 дн.)")
    print(f"  Годовой оборот (накопл.): {annual} ₽ → уровень {tier_name_ru(tier_index_from_annual_sum(annual))}")

    print("\n=== СВЕРКА ===")
    print(f"  Карточка МС:   активные={card_active}  ожидают={card_pending}")
    print(f"  Транзакции МС: активные={balance_ms}  ожидают={earn_pending}")
    print(f"  Пересчёт:      всего начислено={replay_total}, баланс≈{replay_balance}")
    try:
        diff = int(float(card_active)) - replay_balance
        print(f"  Расхождение карточка vs пересчёт: {diff:+d}")
    except (TypeError, ValueError):
        pass
    diff2 = balance_ms - replay_balance
    print(f"  Расхождение транзакции vs пересчёт: {diff2:+d}")


if __name__ == "__main__":
    main()
