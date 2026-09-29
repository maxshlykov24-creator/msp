#!/usr/bin/env python3
"""Сводка по порталу ms-p.bitrix24.ru: деньги в продаже и сделки, где ждём клиента."""

import json
import os
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV = ROOT / ".env"


def load_base() -> str:
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if line.startswith("BITRIX_WEBHOOK_URL="):
            return line.split("=", 1)[1].strip().rstrip("/")
    raise SystemExit("В .env нет BITRIX_WEBHOOK_URL")


def call(base, method, payload=None):
    data = urllib.parse.urlencode(payload or {}, doseq=True).encode()
    req = urllib.request.Request(base + "/" + method, data=data)
    with urllib.request.urlopen(req, timeout=40) as resp:
        body = json.load(resp)
    if "error" in body:
        raise SystemExit(f"{method}: {body.get('error_description') or body.get('error')}")
    return body


def grouped(amount):
    return f"{int(amount):,}".replace(",", " ")


def money(value):
    try:
        amount = int(float(value or 0))
    except (TypeError, ValueError):
        return "сумма не стоит"
    if amount == 0:
        return "сумма не стоит"
    return grouped(amount) + " ₽"


def main() -> None:
    base = load_base()
    deals = call(base, "crm.deal.list.json", {
        "select[]": ["ID", "TITLE", "STAGE_ID", "CATEGORY_ID", "OPPORTUNITY", "COMPANY_ID"],
        "order[ID]": "ASC",
        "start": 0,
    })["result"]
    companies = {
        str(c["ID"]): c.get("TITLE") or ""
        for c in call(base, "crm.company.list.json", {"select[]": ["ID", "TITLE"]})["result"]
    }
    statuses = call(base, "crm.status.list.json")["result"]
    stage_name = {s["STATUS_ID"]: s["NAME"] for s in statuses}

    sales_open = {"NEW", "PREPARATION", "PREPAYMENT_INVOICE", "EXECUTING", "FINAL_INVOICE"}
    wait_client = {"C4:PREPAYMENT_INVOICE", "C6:PREPAYMENT_INVOICE"}

    sales = [d for d in deals if str(d.get("CATEGORY_ID")) == "0" and d.get("STAGE_ID") in sales_open]
    waiting = [d for d in deals if d.get("STAGE_ID") in wait_client]

    sales_sum = 0
    unpriced = 0
    for d in sales:
        raw = d.get("OPPORTUNITY") or 0
        amount = int(float(raw))
        if amount:
            sales_sum += amount
        else:
            unpriced += 1

    lines = [
        "MS Product, сводка Битрикс24",
        "",
        f"Продажа, ещё не подписано: {len(sales)} сделок, {grouped(sales_sum)} ₽",
    ]
    if unpriced:
        lines.append(f"Без одной суммы: {unpriced}. Там в файлах два варианта, в сделку не ставил.")
    lines.append("")
    for d in sales:
        company = companies.get(str(d.get("COMPANY_ID")), "")
        title = d.get("TITLE") or ""
        label = f"{company}: {title}" if company and company not in title else title
        lines.append(f"• {label} — {stage_name.get(d.get('STAGE_ID'), d.get('STAGE_ID'))}, {money(d.get('OPPORTUNITY'))}")

    lines.append("")
    if waiting:
        lines.append(f"Ждём клиента: {len(waiting)}")
        for d in waiting:
            company = companies.get(str(d.get("COMPANY_ID")), "")
            title = d.get("TITLE") or ""
            label = f"{company}: {title}" if company else title
            lines.append(f"• {label}")
    else:
        lines.append("Сделок на стадии «Ждём клиента» нет.")

    lines.append("")
    lines.append("Портал: https://ms-p.bitrix24.ru/crm/deal/")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
