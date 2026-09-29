#!/usr/bin/env python3
"""Робот на стадии «Запрос в работе» воронки Сопровождение: задача с SLA."""

import json
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV = ROOT / ".env"
STAGE = "C6:PREPARATION"


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
    try:
        with urllib.request.urlopen(req, timeout=40) as resp:
            body = json.load(resp)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()[:800]
        return {"error": "HTTP", "error_description": raw}
    return body


def main():
    base = load_base()
    listed = call(base, "bizproc.workflow.template.list.json", {
        "select": ["ID", "NAME", "DOCUMENT_STATUS", "AUTO_EXECUTE"],
        "filter": {"MODULE_ID": "crm"},
    })
    print("list", json.dumps(listed, ensure_ascii=False)[:800])
    if listed.get("error"):
        return
    for row in listed.get("result") or []:
        if row.get("NAME") == "SLA сопровождение" and row.get("DOCUMENT_STATUS") == STAGE:
            print("already", row.get("ID"))
            return

    description = (
        "SLA техподдержки MS Product. "
        "Реакция: подтвердить и оценить в течение 2 рабочих часов. "
        "Старт работ: не позже следующего рабочего дня. "
        "Квота: 1 оплаченный час в день на клиента. "
        "Ошибка на нашей стороне: без списания часов, до 1 рабочего дня."
    )
    template = {
        "Type": "SequentialWorkflowActivity",
        "Name": "Template",
        "Activated": "Y",
        "Properties": {"Title": "SLA сопровождение"},
        "Children": [
            {
                "Type": "Task2Activity",
                "Name": "sla_task",
                "Activated": "Y",
                "Properties": {
                    "Fields": {
                        "TITLE": "SLA: {=Document:TITLE}",
                        "DESCRIPTION": description,
                        "RESPONSIBLE_ID": "1",
                        "CREATED_BY": "1",
                        "DEADLINE": '{=dateadd({=System:Now}, "2h")}',
                        "ALLOW_CHANGE_DEADLINE": "Y",
                    },
                    "HoldToClose": "N",
                    "AUTO_LINK_TO_CRM_ENTITY": "Y",
                    "Title": "Поставить задачу SLA",
                },
                "Children": [],
            }
        ],
    }
    doc_types = [
        ["crm", "CCrmDocumentDeal", "DEAL"],
        ["crm", "Bitrix\\Crm\\Integration\\BizProc\\Document\\Deal", "DEAL"],
    ]
    for doc in doc_types:
        for auto in (0, 8):
            payload = {
                "DOCUMENT_TYPE": doc,
                "NAME": "SLA сопровождение",
                "DESCRIPTION": description,
                "AUTO_EXECUTE": auto,
                "DOCUMENT_STATUS": STAGE,
                "TEMPLATE": template,
            }
            result = call(base, "bizproc.workflow.template.add.json", payload)
            print("try", doc[1], "auto", auto, json.dumps(result, ensure_ascii=False)[:500])
            if not result.get("error"):
                return


if __name__ == "__main__":
    main()
