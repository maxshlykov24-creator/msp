#!/usr/bin/env python3
"""Поля карточки сделки: стек, интенсивность, следующий шаг, папка vault."""

import json
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV = ROOT / ".env"

# title сделки -> поля. Интенсивность: мало, средне, много или пусто.
DEALS = {
    "Контур продления и контроля": {
        "stack": "amoCRM, Wazzup, телефония Билайн, Точка Банк, 1С",
        "intensity": "",
        "next": "Созвон и подписание. Проект не начат.",
        "vault": "2. БИЗНЕС/04_Производство/Активные/Billboardoff/",
    },
    "Система снабжения": {
        "stack": "Своя веб-система. CRM клиент отклонил",
        "intensity": "",
        "next": "Отправить предложение. Подтвердить ЛПР.",
        "vault": "2. БИЗНЕС/04_Производство/Активные/Триумф_снабжение/",
    },
    "Аналитика, воронка и маркировка": {
        "stack": "amoCRM, МойСклад, Честный знак",
        "intensity": "",
        "next": "Подписание договора от 27.08.",
        "vault": "2. БИЗНЕС/04_Производство/Активные/2MyMoods/",
    },
    "amoCRM детейлинга": {
        "stack": "amoCRM с нуля, телефония, мессенджеры",
        "intensity": "",
        "next": "Отправить пакет в Telegram. Вариант 120 или 260 тысяч не выбран.",
        "vault": "2. БИЗНЕС/04_Производство/Лиды/DIVO_Detailing/",
    },
    "Этап ФБС": {
        "stack": "МойСклад, Google Sheets",
        "intensity": "",
        "next": "КП не подписано.",
        "vault": "2. БИЗНЕС/04_Производство/Активные/Береза_Групп/",
    },
    "Касса МойСклад": {
        "stack": "amoCRM, МойСклад, касса, Talk-me через Amojo",
        "intensity": "много",
        "next": "Закрыть хвост кассы. ТЗ созвона 20.08.",
        "vault": "2. БИЗНЕС/04_Производство/Активные/MANSBAND/",
    },
    "Перенос аккаунта МойСклад": {
        "stack": "МойСклад",
        "intensity": "мало",
        "next": "Старт переноса документов и данных за год.",
        "vault": "Папки клиента в производстве нет",
    },
    "Складской цикл": {
        "stack": "МойСклад, Google Sheets, Apps Script",
        "intensity": "",
        "next": "Ждём ответ клиента с 10.08. Оплата 70 000 ₽ не подтверждена.",
        "vault": "2. БИЗНЕС/04_Производство/Активные/Береза_Групп/",
    },
    "Бот статусов доставки": {
        "stack": "amoCRM, Telegram-бот",
        "intensity": "",
        "next": "Довести бот уведомлений о доставке.",
        "vault": "2. БИЗНЕС/04_Производство/Активные/DKAcademy/",
    },
    "Груминг и питомник": {
        "stack": "amoCRM, YCLIENTS, Tilda, боты Telegram и MAX",
        "intensity": "",
        "next": "Груминг в работе. Хвост Tilda. Дашборд не начат.",
        "vault": "2. БИЗНЕС/04_Производство/Активные/Keris_Club/",
    },
    "МойСклад и каналы продаж": {
        "stack": "МойСклад, amoCRM spineshop",
        "intensity": "",
        "next": "Этап в реестре не уточнён. Контакта в карточке нет.",
        "vault": "2. БИЗНЕС/04_Производство/Активные/Detensor/",
    },
    "Бот товароучёта": {
        "stack": "Telegram, Nexara, SQLite",
        "intensity": "",
        "next": "Бот @korman24bot ещё на временном VPS.",
        "vault": "2. БИЗНЕС/04_Производство/Активные/Комбикорм_Александр/",
    },
    "Лояльность: RetailCRM в МойСклад": {
        "stack": "МойСклад, RetailCRM, it-martache.ru",
        "intensity": "средне",
        "next": "Перенос лояльности. Цель: отказ клиента от RetailCRM.",
        "vault": "2. БИЗНЕС/04_Производство/Активные/MartaChe/",
    },
}


def load_base() -> str:
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


def call(base: str, method: str, payload: dict) -> dict:
    data = urllib.parse.urlencode(flatten(payload), doseq=True).encode()
    req = urllib.request.Request(base + "/" + method, data=data)
    with urllib.request.urlopen(req, timeout=40) as resp:
        body = json.load(resp)
    if "error" in body:
        raise SystemExit(f"{method}: {body.get('error')} {body.get('error_description')}")
    return body


def labels(ru: str) -> dict:
    return {"ru": ru, "en": ru}


def ensure_fields(base: str) -> dict:
    existing = call(base, "crm.deal.userfield.list.json", {})["result"]
    by_name = {row["FIELD_NAME"]: row for row in existing}
    specs = [
        ("UF_CRM_MSP_STACK", "string", "Стек", None),
        ("UF_CRM_MSP_NEXT", "string", "Следующий шаг", None),
        ("UF_CRM_MSP_VAULT", "string", "Папка", None),
        ("UF_CRM_MSP_INTENSITY", "enumeration", "Интенсивность поддержки", ["мало", "средне", "много"]),
    ]
    for name, kind, title, enum_values in specs:
        if name in by_name:
            print("field exists", name)
            continue
        fields = {
            "FIELD_NAME": name,
            "USER_TYPE_ID": kind,
            "EDIT_FORM_LABEL": labels(title),
            "LIST_COLUMN_LABEL": labels(title),
            "LIST_FILTER_LABEL": labels(title),
            "SHOW_FILTER": "Y",
            "SHOW_IN_LIST": "Y",
            "EDIT_IN_LIST": "Y",
            "IS_SEARCHABLE": "Y",
        }
        if enum_values:
            fields["LIST"] = [{"VALUE": value, "SORT": (i + 1) * 10} for i, value in enumerate(enum_values)]
        call(base, "crm.deal.userfield.add.json", {"fields": fields})
        print("field added", name)
    existing = call(base, "crm.deal.userfield.list.json", {})["result"]
    return {row["FIELD_NAME"]: row for row in existing}


def enum_map(field: dict) -> dict:
    out = {}
    for item in field.get("LIST") or []:
        out[item.get("VALUE")] = item.get("ID")
    return out


def support_overlay(title, company):
    table = {
        "MANSBAND": ("много", "2. БИЗНЕС/04_Производство/Активные/MANSBAND/", "amoCRM, МойСклад, касса, Talk-me", "Сопровождение. Плюс хвост кассы."),
        "МАРТАче": ("средне", "2. БИЗНЕС/04_Производство/Активные/MartaChe/", "МойСклад, RetailCRM", "Сопровождение. Плюс перенос лояльности."),
        "Ван Пак": ("мало", "Папки клиента в производстве нет", "МойСклад", "Сопровождение. Плюс перенос аккаунта."),
        "DIVO Motors": ("мало", "2. БИЗНЕС/04_Производство/Активные/DIVO_Motors/", "amoCRM, дашборд, CM.Expert", "Сопровождение прода. Интенсивность мало."),
        "Здоровый позвоночник": ("средне", "Папки клиента в производстве нет", "В реестре не указана", "Сопровождение. Система в реестре не указана."),
        "ЛУКДЕКОР": ("мало", "Папки клиента в производстве нет", "В реестре не указана", "Сопровождение. Контакт и система не указаны."),
        "LicenseBridge USA": ("", "2. БИЗНЕС/04_Производство/Активные/LicenseBridge_USA/", "Kommo, Asterisk, WhatsApp, дашборд lb.mspod24.ru", "Живой прод. Интенсивность в реестре не названа."),
        "Keris Club": ("", "2. БИЗНЕС/04_Производство/Активные/Keris_Club/", "amoCRM, YCLIENTS, Tilda, боты", "Сопровождение открытого салона."),
    }
    if title == "Сопровождение открытого салона":
        intensity, vault, stack, nxt = table["Keris Club"]
        return {"stack": stack, "intensity": intensity, "next": nxt, "vault": vault}
    if title != "Сопровождение":
        return None
    if company not in table:
        return None
    intensity, vault, stack, nxt = table[company]
    return {"stack": stack, "intensity": intensity, "next": nxt, "vault": vault}


def main() -> None:
    base = load_base()
    fields = ensure_fields(base)
    intensity_ids = enum_map(fields["UF_CRM_MSP_INTENSITY"])
    companies = {
        str(row["ID"]): row.get("TITLE") or ""
        for row in call(base, "crm.company.list.json", {"select[]": ["ID", "TITLE"]})["result"]
    }
    deals = call(base, "crm.deal.list.json", {
        "select[]": ["ID", "TITLE", "COMPANY_ID"],
        "order[ID]": "ASC",
    })["result"]
    for deal in deals:
        title = deal.get("TITLE") or ""
        company = companies.get(str(deal.get("COMPANY_ID")), "")
        spec = DEALS.get(title) or support_overlay(title, company)
        if not spec:
            print("skip", deal["ID"], title)
            continue
        payload = {
            "id": deal["ID"],
            "fields": {
                "UF_CRM_MSP_STACK": spec["stack"],
                "UF_CRM_MSP_NEXT": spec["next"],
                "UF_CRM_MSP_VAULT": spec["vault"],
            },
        }
        if spec["intensity"]:
            payload["fields"]["UF_CRM_MSP_INTENSITY"] = intensity_ids[spec["intensity"]]
        call(base, "crm.deal.update.json", payload)
        print("filled", deal["ID"], title)


if __name__ == "__main__":
    main()
