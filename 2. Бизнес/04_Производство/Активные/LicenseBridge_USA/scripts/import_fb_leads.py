#!/usr/bin/env python3
"""Импорт лидов Facebook Lead Ads (CSV-выгрузка) в Kommo.

Создаёт контакт + сделку на этапе «Новая заявка» воронки Pipeline.
Проверяет дубли по телефону: если контакт уже есть — не дублирует,
сделку привязывает к существующему контакту.

Запуск:
    python3 import_fb_leads.py "<путь_к_csv>" [--dry-run]

Токен берётся из переменной окружения KOMMO_TOKEN.
"""
import csv
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
import urllib.parse

BASE = "https://licensebridgeusa.kommo.com/api/v4"

PIPELINE_ID = 11274779
STATUS_NEW = 108112044          # этап «Новая заявка»
RESPONSIBLE_USER = 13291175     # Pavel

PHONE_FIELD = 142532
PHONE_ENUM_WORK = 120012
CHANNEL_FIELD = 143104
UTM_SOURCE_FIELD = 142546
UTM_CAMPAIGN_FIELD = 142544
RUSSIAN_FIELD = 1417881
CALIFORNIA_FIELD = 1417883


def get_token() -> str:
    tok = os.environ.get("KOMMO_TOKEN", "").strip()
    if not tok:
        sys.exit("ERROR: переменная окружения KOMMO_TOKEN не задана")
    return tok


def api(method: str, path: str, token: str, payload=None):
    url = BASE + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            code = r.getcode()
            body = r.read().decode()
            return code, (json.loads(body) if body else None)
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        return e.code, (json.loads(body) if body else None)


def clean_phone(raw: str) -> str:
    p = (raw or "").strip()
    if p.startswith("p:"):
        p = p[2:]
    p = re.sub(r"[^\d+]", "", p)
    return p


def norm_yesno(raw: str) -> str:
    v = (raw or "").lower()
    if "да" in v or "yes" in v:
        return "Да"
    if "нет" in v or "no" in v:
        return "Нет"
    return ""


def norm_name(raw: str) -> str:
    return re.sub(r"\s+", " ", (raw or "").strip())


def campaign_short(raw: str) -> str:
    """Из campaign_name делаем короткое California / New York."""
    v = (raw or "").strip()
    low = v.lower()
    if "california" in low:
        return "California"
    if "new york" in low:
        return "New York"
    return v


def find_contact_by_phone(phone: str, token: str):
    """Возвращает id существующего контакта или None."""
    q = urllib.parse.quote(phone)
    code, data = api("GET", f"/contacts?query={q}&limit=10", token)
    if code == 204 or not data:
        return None
    for c in data.get("_embedded", {}).get("contacts", []):
        for cf in c.get("custom_fields_values") or []:
            if cf.get("field_code") == "PHONE" or cf.get("field_id") == PHONE_FIELD:
                for val in cf.get("values", []):
                    if clean_phone(val.get("value", "")) == phone:
                        return c["id"]
    # совпадение по query, но телефон не точный — вернём первый кандидат как мягкий дубль
    contacts = data.get("_embedded", {}).get("contacts", [])
    return contacts[0]["id"] if contacts else None


def create_contact(name: str, phone: str, token: str):
    payload = [{
        "name": name or phone,
        "responsible_user_id": RESPONSIBLE_USER,
        "custom_fields_values": [{
            "field_id": PHONE_FIELD,
            "values": [{"value": phone, "enum_id": PHONE_ENUM_WORK}],
        }],
    }]
    code, data = api("POST", "/contacts", token, payload)
    if code in (200, 201):
        return data["_embedded"]["contacts"][0]["id"]
    raise RuntimeError(f"create_contact failed {code}: {json.dumps(data, ensure_ascii=False)}")


def create_lead(lead_name, contact_id, channel, utm_source, utm_campaign,
                russian, california, token):
    cfv = []

    def add(field_id, value):
        if value:
            cfv.append({"field_id": field_id, "values": [{"value": value}]})

    add(CHANNEL_FIELD, channel)
    add(UTM_SOURCE_FIELD, utm_source)
    add(UTM_CAMPAIGN_FIELD, utm_campaign)
    add(RUSSIAN_FIELD, russian)
    add(CALIFORNIA_FIELD, california)

    payload = [{
        "name": lead_name,
        "pipeline_id": PIPELINE_ID,
        "status_id": STATUS_NEW,
        "responsible_user_id": RESPONSIBLE_USER,
        "custom_fields_values": cfv,
        "_embedded": {"contacts": [{"id": contact_id}]},
    }]
    code, data = api("POST", "/leads", token, payload)
    if code in (200, 201):
        return data["_embedded"]["leads"][0]["id"]
    raise RuntimeError(f"create_lead failed {code}: {json.dumps(data, ensure_ascii=False)}")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry-run" in sys.argv
    if not args:
        sys.exit("usage: import_fb_leads.py <csv> [--dry-run]")
    csv_path = args[0]
    token = get_token()

    rows = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append(r)

    print(f"Лидов в файле: {len(rows)} | dry-run={dry}\n")
    created, dupes, errors = 0, 0, 0
    report = []

    for r in rows:
        name = norm_name(r.get("full_name", ""))
        phone = clean_phone(r.get("phone", ""))
        russian = norm_yesno(r.get("вы_русскоговорящий?_/_do_you_speak_russian?", ""))
        california = norm_yesno(r.get("вы_работаете_в_калифорнии?_/_do_you_work_in_california?", ""))
        utm_source = (r.get("platform", "") or "").strip()
        camp = campaign_short(r.get("campaign_name", ""))
        lead_name = f"FB {camp} — {name}".strip(" —")

        if not phone:
            errors += 1
            report.append(f"SKIP (нет телефона): {name}")
            continue

        if dry:
            report.append(f"DRY  {lead_name} | {phone} | RU={russian} CA={california} | src={utm_source} camp={camp}")
            continue

        try:
            existing = find_contact_by_phone(phone, token)
            if existing:
                dupes += 1
                contact_id = existing
                tag = "ДУБЛЬ→привязка"
            else:
                contact_id = create_contact(name, phone, token)
                created += 1
                tag = "новый"
                time.sleep(0.3)
            lead_id = create_lead(lead_name, contact_id, "Facebook", utm_source,
                                  camp, russian, california, token)
            report.append(f"OK [{tag}] lead={lead_id} contact={contact_id} | {lead_name} | {phone}")
            time.sleep(0.3)
        except Exception as e:  # noqa: BLE001
            errors += 1
            report.append(f"ERR {lead_name} | {phone} | {e}")

    print("\n".join(report))
    print("\n--- ИТОГ ---")
    print(f"Создано новых контактов: {created}")
    print(f"Дублей (сделка привязана к существующему контакту): {dupes}")
    print(f"Ошибок/пропусков: {errors}")


if __name__ == "__main__":
    main()
