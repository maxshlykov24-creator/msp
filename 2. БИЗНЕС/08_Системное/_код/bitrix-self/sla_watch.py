#!/usr/bin/env python3
"""Если сделка сопровождения стоит на «Запрос в работе», ставит одну задачу SLA.

Штатный робот Битрикса этим вебхуком не создаётся: метод шаблона требует
контекст приложения, не входящий вебхук. Наблюдатель делает то же действие.
"""

import json
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV = ROOT / ".env"
STAGE = "C6:PREPARATION"
MSK = timezone(timedelta(hours=3))
SLA = (
    "SLA техподдержки MS Product.\n"
    "Реакция: подтвердить, оценить и поставить в работу в течение 2 рабочих часов.\n"
    "Старт работ: не позже следующего рабочего дня. Это не обещание закрыть задачу в тот же день.\n"
    "Квота: 1 оплаченный час в день на этого клиента. Сверх квоты только по согласованию.\n"
    "Ошибка на нашей стороне: без списания часов, срок до 1 рабочего дня."
)


def load_base():
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if line.startswith("BITRIX_WEBHOOK_URL="):
            return line.split("=", 1)[1].strip().rstrip("/")
    raise SystemExit("В .env нет BITRIX_WEBHOOK_URL")


def flatten(obj, prefix=""):
    items = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            p = f"{prefix}[{key}]" if prefix else key
            items.extend(flatten(value, p))
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            items.extend(flatten(value, f"{prefix}[{i}]"))
    else:
        items.append((prefix, "" if obj is None else str(obj)))
    return items


def call(base, method, payload):
    data = urllib.parse.urlencode(flatten(payload), doseq=True).encode()
    req = urllib.request.Request(base + "/" + method, data=data)
    with urllib.request.urlopen(req, timeout=40) as resp:
        body = json.load(resp)
    if "error" in body:
        raise SystemExit(f"{method}: {body.get('error')} {body.get('error_description')}")
    return body


def has_open_sla(base, deal_id):
    listed = call(base, "tasks.task.list.json", {
        "filter": {"UF_CRM_TASK": f"D_{deal_id}"},
        "select": ["ID", "TITLE", "STATUS"],
    })
    tasks = (listed.get("result") or {}).get("tasks") or []
    if isinstance(tasks, dict):
        tasks = list(tasks.values())
    for task in tasks:
        title = task.get("title") or task.get("TITLE") or ""
        status = str(task.get("status") or task.get("STATUS") or "")
        if title.startswith("SLA:") and status not in ("5", "7"):
            return True
    return False


def main():
    base = load_base()
    deals = call(base, "crm.deal.list.json", {
        "filter": {"CATEGORY_ID": 6, "STAGE_ID": STAGE},
        "select[]": ["ID", "TITLE", "COMPANY_ID"],
    })["result"]
    companies = {
        str(row["ID"]): row.get("TITLE") or ""
        for row in call(base, "crm.company.list.json", {"select[]": ["ID", "TITLE"]})["result"]
    }
    deadline = (datetime.now(MSK) + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S+03:00")
    created = 0
    for deal in deals:
        if has_open_sla(base, deal["ID"]):
            print("exists", deal["ID"])
            continue
        company = companies.get(str(deal.get("COMPANY_ID")), "")
        title = deal.get("TITLE") or "Сопровождение"
        label = f"{company}. {title}" if company else title
        result = call(base, "tasks.task.add.json", {"fields": {
            "TITLE": f"SLA: {label}",
            "DESCRIPTION": SLA,
            "RESPONSIBLE_ID": 1,
            "CREATED_BY": 1,
            "DEADLINE": deadline,
            "UF_CRM_TASK": [f"D_{deal['ID']}"],
        }})
        task_id = (result.get("result") or {}).get("task", {}).get("id")
        print("created", task_id, label)
        created += 1
    print("checked", len(deals), "created", created)


if __name__ == "__main__":
    main()
