"""
Keris Club — создание ТЕСТОВЫХ сделок/контактов для наглядности.
Все названия с префиксом [ТЕСТ] — легко найти и удалить потом.
Показывает, как заполняются поля во всех 4 воронках по нашей логике.
"""

import time
import json
import urllib.request
import urllib.error
from datetime import datetime, timedelta

TOKEN = (
    "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiIsImp0aSI6Ijk0NzNjN2UzYzczNzdjYzYwOTZlNzM0MzhlNGUwOTgzYTVmNjNjM2E2MGQ1MWI1MmRlNTM0YmJjMzRkYjU4YWQ1ZGY0NWEzNzVlZTJhYjNmIn0"
    ".eyJhdWQiOiJhYjU3ZjJhYy05M2Y3LTRhYzEtOTI0Mi0yY2VmMDZmZjhjZmEiLCJqdGkiOiI5NDczYzdlM2M3Mzc3Y2M2MDk2ZTczNDM4ZTRlMDk4M2E1ZjYzYzNhNjBkNTFiNTJkZTUzNGJiYzM0ZGI1OGFkNWRmNDVhMzc1ZWUyYWIzZiIsImlhdCI6MTc4MjI0NjIzNCwibmJmIjoxNzgyMjQ2MjM0LCJleHAiOjE4NjQ1MTIwMDAsInN1YiI6Ijk0OTA1MzAiLCJncmFudF90eXBlIjoiIiwiYWNjb3VudF9pZCI6MzMxMTcxNTAsImJhc2VfZG9tYWluIjoiYW1vY3JtLnJ1IiwidmVyc2lvbiI6Miwic2NvcGVzIjpbInB1c2hfbm90aWZpY2F0aW9ucyIsImZpbGVzIiwiY3JtIiwiZmlsZXNfZGVsZXRlIiwibm90aWZpY2F0aW9ucyJdLCJoYXNoX3V1aWQiOiJkNzhjNjlkOC1jMjA0LTQ1MmEtOWMxYS03MTRlNzQwMmM3NDciLCJhcGlfZG9tYWluIjoiYXBpLWIuYW1vY3JtLnJ1In0"
    ".KOFMQ9Qy7elwGPClf8PILvWx3YoIACUQROxE2u0ptABiZnL70Nqvmasa5mHIRnFxkemec2I4AWsBjBpAG-wWBkDWOFVYedLfFV-hXR27MOgadeLaTFC0XoSVSFcOyXyL7sNdxBQlJxuj4egGEEHcy7ruKiMp60Oij_5QTnnfUxalSSYr_f4GOzKYc2Q85D-k9mokPZjZ6F70oHrgipaKTq-fOvAYxfCiHfZK74_K9L2wHq_abom-C8_Hh4wZXvzIcR0dHF5TTLO8Z1Q7cdnrndsrbyvBM2sZPGUB-PQxXXQiZQo7W67ih7OJdBqtlE1PDNiqqkMiZiToKdmE4wgwEQ"
)
BASE = "https://kerisclub.amocrm.ru"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

# ── ID воронок/этапов (факт 24.06.2026) ──
PIPE_PRODAZHI = 11036674
PIPE_SHCHENKI = 11036834
PIPE_RASSROCHKA = 11036838
PIPE_CLIENTS = 11036874

ST_SH_SVOBODEN = 86718366
ST_SH_BRON = 86718370
ST_SH_UEHAL = 142
ST_PR_BRON = 86718354       # «Щенок забронирован» в Продажах
ST_PR_UEHAL = 142
ST_RASS_AKTIVNA = 86718382
ST_CL_1MONTH = 86718686

# ── ID кастомных полей ──
# Контакт
F_PHONE = 1820533
C_SRC = 1820795
C_CONSENT = 1820799
C_CONSENT_DATE = 1820801
C_TG = 1820899
C_CLIENT_PIT = 1820903
# Сделка — щенок
L_NAME = 1820803
L_BIRTH = 1820807
L_SEX = 1820809
L_COLOR = 1820811
L_PARENTS = 1820813
L_VACC = 1820819
L_VACC_TILL = 1820821
L_MEDCARD = 1820823
L_READY = 1820825
L_BRON_DATE = 1820827
L_BRON_TILL = 1820829
L_PREPAY = 1820831
L_PREPAY_STATUS = 1820833
L_PHOTO = 1820925
# Сделка — продажи/рассрочка
L_SRC = 1820835
L_COMMENT = 1820837
L_PAYTYPE = 1820839
L_RASS_TERM = 1820869
L_RASS_SUM = 1820845
L_NEXT_PAY = 1820847
L_DEBT = 1820849
L_LINK_ID = 1820851
L_CONTACT_WAY = 1820923


def req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(f"{BASE}{path}", data=data, headers=HEADERS, method=method)
    try:
        with urllib.request.urlopen(r) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"error": raw.decode(errors="replace")}


def ts(days_offset=0):
    return int((datetime.now() + timedelta(days=days_offset)).timestamp())


def cf(field_id, value):
    return {"field_id": field_id, "values": [{"value": value}]}


def first_id(body, key):
    items = body.get("_embedded", {}).get(key, [])
    return items[0]["id"] if items else None


# ─────────────────────────────────────────────
# 1. Контакты-владельцы
# ─────────────────────────────────────────────
print("── Контакты ──")

s, b = req("POST", "/api/v4/contacts", [{
    "name": "[ТЕСТ] Анна Котова",
    "custom_fields_values": [
        {"field_id": F_PHONE, "values": [{"value": "+79991234567", "enum_code": "MOB"}]},
        cf(C_SRC, "Телеграм-канал"),
        {"field_id": C_CONSENT, "values": [{"value": True}]},
        {"field_id": C_CONSENT_DATE, "values": [{"value": ts(0)}]},
        {"field_id": C_TG, "values": [{"value": True}]},
    ],
}])
anna_id = first_id(b, "contacts")
print(f"  Анна Котова: {anna_id} [{s}]")
time.sleep(0.4)

s, b = req("POST", "/api/v4/contacts", [{
    "name": "[ТЕСТ] Мария Лебедева",
    "custom_fields_values": [
        {"field_id": F_PHONE, "values": [{"value": "+79997654321", "enum_code": "MOB"}]},
        cf(C_SRC, "Рекомендации"),
        {"field_id": C_CONSENT, "values": [{"value": True}]},
        {"field_id": C_CONSENT_DATE, "values": [{"value": ts(-40)}]},
        {"field_id": C_TG, "values": [{"value": True}]},
        {"field_id": C_CLIENT_PIT, "values": [{"value": True}]},
    ],
}])
maria_id = first_id(b, "contacts")
print(f"  Мария Лебедева: {maria_id} [{s}]")
time.sleep(0.4)

# ─────────────────────────────────────────────
# 2. Щенки (витрина)
# ─────────────────────────────────────────────
print("\n── Щенки (воронка Щенки) ──")


def make_puppy(name, status, fields, price):
    body = [{
        "name": name,
        "price": price,
        "pipeline_id": PIPE_SHCHENKI,
        "status_id": status,
        "custom_fields_values": fields,
    }]
    s, b = req("POST", "/api/v4/leads", body)
    pid = first_id(b, "leads")
    print(f"  {name}: {pid} [{s}]")
    if not (200 <= s < 300):
        print("    ", json.dumps(b, ensure_ascii=False)[:300])
    time.sleep(0.4)
    return pid


jam_id = make_puppy("[ТЕСТ] Джем 🍓", ST_SH_SVOBODEN, [
    cf(L_NAME, "Джем"),
    {"field_id": L_BIRTH, "values": [{"value": ts(-65)}]},
    cf(L_SEX, "Кобель"),
    cf(L_COLOR, "ice gold"),
    cf(L_PARENTS, "отец Ch. Korea Line / мать China Line"),
    cf(L_VACC, "1-я сделана"),
    {"field_id": L_VACC_TILL, "values": [{"value": ts(-5)}]},
    cf(L_MEDCARD, "вет.паспорт №KC-018"),
    {"field_id": L_READY, "values": [{"value": ts(-5)}]},
    cf(L_PHOTO, "https://t.me/keris_chat"),
], 150000)

floks_id = make_puppy("[ТЕСТ] Флокс 🧸", ST_SH_SVOBODEN, [
    cf(L_NAME, "Флокс"),
    {"field_id": L_BIRTH, "values": [{"value": ts(-65)}]},
    cf(L_SEX, "Кобель"),
    cf(L_COLOR, "кремовый"),
    cf(L_PARENTS, "отец Ch. Korea Line / мать China Line"),
    cf(L_VACC, "1-я сделана"),
    {"field_id": L_READY, "values": [{"value": ts(-5)}]},
    cf(L_PHOTO, "https://t.me/keris_chat"),
], 140000)

moroshka_id = make_puppy("[ТЕСТ] Морошка 💘", ST_SH_BRON, [
    cf(L_NAME, "Морошка"),
    {"field_id": L_BIRTH, "values": [{"value": ts(-65)}]},
    cf(L_SEX, "Сука"),
    cf(L_COLOR, "abricot"),
    cf(L_PARENTS, "отец Ch. Korea Line / мать China Line"),
    cf(L_VACC, "2-я сделана"),
    {"field_id": L_VACC_TILL, "values": [{"value": ts(2)}]},
    cf(L_MEDCARD, "вет.паспорт №KC-021"),
    {"field_id": L_READY, "values": [{"value": ts(-3)}]},
    {"field_id": L_BRON_DATE, "values": [{"value": ts(0)}]},
    {"field_id": L_BRON_TILL, "values": [{"value": ts(7)}]},
    {"field_id": L_PREPAY, "values": [{"value": 48000}]},
    cf(L_PREPAY_STATUS, "Оплачена"),
    cf(L_PHOTO, "https://t.me/keris_chat"),
], 160000)

yagodka_id = make_puppy("[ТЕСТ] Ягодка ✨", ST_SH_UEHAL, [
    cf(L_NAME, "Ягодка"),
    {"field_id": L_BIRTH, "values": [{"value": ts(-130)}]},
    cf(L_SEX, "Сука"),
    cf(L_COLOR, "ice gold"),
    cf(L_VACC, "Комплекс готов"),
    cf(L_PREPAY_STATUS, "Оплачена"),
], 155000)

# ─────────────────────────────────────────────
# 3. Продажи
# ─────────────────────────────────────────────
print("\n── Продажи ──")


def make_lead(name, pipeline, status, contact_id, fields, price):
    body = [{
        "name": name,
        "price": price,
        "pipeline_id": pipeline,
        "status_id": status,
        "custom_fields_values": fields,
        "_embedded": {"contacts": [{"id": contact_id}]} if contact_id else {},
    }]
    s, b = req("POST", "/api/v4/leads", body)
    lid = first_id(b, "leads")
    print(f"  {name}: {lid} [{s}]")
    if not (200 <= s < 300):
        print("    ", json.dumps(b, ensure_ascii=False)[:300])
    time.sleep(0.4)
    return lid


make_lead("[ТЕСТ] Анна — бронь Морошки", PIPE_PRODAZHI, ST_PR_BRON, anna_id, [
    cf(L_SRC, "Телеграм-канал"),
    cf(L_CONTACT_WAY, "TG-бот"),
    cf(L_PAYTYPE, "Рассрочка"),
    {"field_id": L_COMMENT, "values": [{"value": "Девочка, ice gold/abricot, микро. Пишет из бота."}]},
    {"field_id": L_LINK_ID, "values": [{"value": moroshka_id}]},
], 160000)

make_lead("[ТЕСТ] Мария — Ягодка (продано)", PIPE_PRODAZHI, ST_PR_UEHAL, maria_id, [
    cf(L_SRC, "Рекомендации"),
    cf(L_PAYTYPE, "Полная оплата"),
    {"field_id": L_COMMENT, "values": [{"value": "Полная оплата, щенок уехал в семью."}]},
    {"field_id": L_LINK_ID, "values": [{"value": yagodka_id}]},
], 155000)

# ─────────────────────────────────────────────
# 4. Рассрочка
# ─────────────────────────────────────────────
print("\n── Рассрочка ──")
make_lead("[ТЕСТ] Рассрочка — Анна (Морошка)", PIPE_RASSROCHKA, ST_RASS_AKTIVNA, anna_id, [
    cf(L_RASS_TERM, "6"),
    {"field_id": L_RASS_SUM, "values": [{"value": 112000}]},
    {"field_id": L_NEXT_PAY, "values": [{"value": ts(30)}]},
    {"field_id": L_DEBT, "values": [{"value": 112000}]},
], 112000)

# ─────────────────────────────────────────────
# 5. Клиенты питомника (лояльность)
# ─────────────────────────────────────────────
print("\n── Клиенты питомника ──")
make_lead("[ТЕСТ] Мария — сопровождение", PIPE_CLIENTS, ST_CL_1MONTH, maria_id, [], 0)

print("\nDONE")
