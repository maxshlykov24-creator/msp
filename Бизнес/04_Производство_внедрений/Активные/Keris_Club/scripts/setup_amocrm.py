"""
Keris Club — amoCRM setup script v2
Настраивает воронки, этапы и кастомные поля по ТЗ_ЭТАП0_ПИТОМНИК.md
"""

import time
import json
import urllib.request
import urllib.error

TOKEN = (
    "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiIsImp0aSI6Ijk0NzNjN2UzYzczNzdjYzYwOTZlNzM0MzhlNGUwOTgzYTVmNjNjM2E2MGQ1MWI1MmRlNTM0YmJjMzRkYjU4YWQ1ZGY0NWEzNzVlZTJhYjNmIn0"
    ".eyJhdWQiOiJhYjU3ZjJhYy05M2Y3LTRhYzEtOTI0Mi0yY2VmMDZmZjhjZmEiLCJqdGkiOiI5NDczYzdlM2M3Mzc3Y2M2MDk2ZTczNDM4ZTRlMDk4M2E1ZjYzYzNhNjBkNTFiNTJkZTUzNGJiYzM0ZGI1OGFkNWRmNDVhMzc1ZWUyYWIzZiIsImlhdCI6MTc4MjI0NjIzNCwibmJmIjoxNzgyMjQ2MjM0LCJleHAiOjE4NjQ1MTIwMDAsInN1YiI6Ijk0OTA1MzAiLCJncmFudF90eXBlIjoiIiwiYWNjb3VudF9pZCI6MzMxMTcxNTAsImJhc2VfZG9tYWluIjoiYW1vY3JtLnJ1IiwidmVyc2lvbiI6Miwic2NvcGVzIjpbInB1c2hfbm90aWZpY2F0aW9ucyIsImZpbGVzIiwiY3JtIiwiZmlsZXNfZGVsZXRlIiwibm90aWZpY2F0aW9ucyJdLCJoYXNoX3V1aWQiOiJkNzhjNjlkOC1jMjA0LTQ1MmEtOWMxYS03MTRlNzQwMmM3NDciLCJhcGlfZG9tYWluIjoiYXBpLWIuYW1vY3JtLnJ1In0"
    ".KOFMQ9Qy7elwGPClf8PILvWx3YoIACUQROxE2u0ptABiZnL70Nqvmasa5mHIRnFxkemec2I4AWsBjBpAG-wWBkDWOFVYedLfFV-hXR27MOgadeLaTFC0XoSVSFcOyXyL7sNdxBQlJxuj4egGEEHcy7ruKiMp60Oij_5QTnnfUxalSSYr_f4GOzKYc2Q85D-k9mokPZjZ6F70oHrgipaKTq-fOvAYxfCiHfZK74_K9L2wHq_abom-C8_Hh4wZXvzIcR0dHF5TTLO8Z1Q7cdnrndsrbyvBM2sZPGUB-PQxXXQiZQo7W67ih7OJdBqtlE1PDNiqqkMiZiToKdmE4wgwEQ"
)
BASE = "https://kerisclub.amocrm.ru"
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json",
}

# Valid amoCRM status colors (из документации)
C_BLUE   = "#99ccff"
C_YELLOW = "#ffff99"
C_ORANGE = "#ffcc66"
C_PINK   = "#ffcccc"
C_LIME   = "#ccff66"
C_GREY   = "#c1c1c1"

EXISTING_PIPELINE_ID = 11036674
# Existing editable statuses to rename (no color change — just name+sort)
EXISTING_STATUSES = {
    86717266: ("Новая заявка", 10),
    86717270: ("Взято в работу", 20),
    86717274: ("Лист ожидания", 30),
    86717278: ("Щенок подобран", 40),
}


def req(method, path, body=None):
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body else None
    r = urllib.request.Request(url, data=data, headers=HEADERS, method=method)
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


def ok(label, status, body=None):
    if 200 <= status < 300:
        print(f"  ✅ {label} [{status}]")
    else:
        detail = json.dumps(body, ensure_ascii=False)[:300] if body else ""
        print(f"  ❌ {label} [{status}]: {detail}")


def enums(*values):
    return [{"value": v, "sort": (i + 1) * 10} for i, v in enumerate(values)]


# ─────────────────────────────────────────────────────────
# 1. Rename default pipeline → «Продажи»
# ─────────────────────────────────────────────────────────
print("\n── 1. Переименовываем «Воронка» → «Продажи» ──")
s, b = req("PATCH", f"/api/v4/leads/pipelines/{EXISTING_PIPELINE_ID}",
           {"name": "Продажи"})
ok("Rename pipeline", s, b)
time.sleep(0.4)

# ─────────────────────────────────────────────────────────
# 2. Rename existing statuses in «Продажи» (name + sort only)
# ─────────────────────────────────────────────────────────
print("\n── 2. Переименовываем этапы «Продажи» ──")
for sid, (name, sort) in EXISTING_STATUSES.items():
    s, b = req("PATCH",
               f"/api/v4/leads/pipelines/{EXISTING_PIPELINE_ID}/statuses/{sid}",
               {"name": name, "sort": sort})
    ok(f"→ {name}", s, b)
    time.sleep(0.3)

# ─────────────────────────────────────────────────────────
# 3. Add new statuses to «Продажи»
# ─────────────────────────────────────────────────────────
print("\n── 3. Добавляем этапы «Предоплата получена» и «Документы оформлены» ──")
s, b = req("POST",
           f"/api/v4/leads/pipelines/{EXISTING_PIPELINE_ID}/statuses",
           [
               {"name": "Предоплата получена", "sort": 50, "color": C_LIME},
               {"name": "Документы оформлены", "sort": 60, "color": C_ORANGE},
           ])
ok("Add 2 new statuses", s, b)
time.sleep(0.5)

# ─────────────────────────────────────────────────────────
# 4. Create pipeline «Щенки»
# ─────────────────────────────────────────────────────────
print("\n── 4. Создаём воронку «Щенки» ──")
s, b = req("POST", "/api/v4/leads/pipelines", [{
    "name": "Щенки",
    "sort": 10,
    "is_main": False,
    "is_unsorted_on": False,
    "_embedded": {
        "statuses": [
            {"name": "Свободен",              "sort": 10, "color": C_BLUE},
            {"name": "Щенок забронирован",    "sort": 20, "color": C_ORANGE},
            {"name": "В процессе оформления", "sort": 30, "color": C_YELLOW},
        ]
    }
}])
ok("Create Щенки", s, b)
shchenki_id = None
if 200 <= s < 300:
    pipes = b.get("_embedded", {}).get("pipelines", [])
    if pipes:
        shchenki_id = pipes[0]["id"]
        print(f"    Щенки pipeline ID: {shchenki_id}")
time.sleep(0.5)

# ─────────────────────────────────────────────────────────
# 5. Create pipeline «Рассрочка»
# ─────────────────────────────────────────────────────────
print("\n── 5. Создаём воронку «Рассрочка» ──")
s, b = req("POST", "/api/v4/leads/pipelines", [{
    "name": "Рассрочка",
    "sort": 20,
    "is_main": False,
    "is_unsorted_on": False,
    "_embedded": {
        "statuses": [
            {"name": "Активна",    "sort": 10, "color": C_BLUE},
            {"name": "Просрочена", "sort": 20, "color": C_PINK},
        ]
    }
}])
ok("Create Рассрочка", s, b)
rassrochka_id = None
if 200 <= s < 300:
    pipes = b.get("_embedded", {}).get("pipelines", [])
    if pipes:
        rassrochka_id = pipes[0]["id"]
        print(f"    Рассрочка pipeline ID: {rassrochka_id}")
time.sleep(0.5)

# ─────────────────────────────────────────────────────────
# 6. Contact custom fields
# ─────────────────────────────────────────────────────────
print("\n── 6. Поля Контактов ──")
s, b = req("POST", "/api/v4/contacts/custom_fields", [
    {
        "name": "Источник",
        "type": "select",
        "enums": enums("Тильда", "Яндекс.Реклама", "Телеграм-канал",
                       "Рекомендации", "Яндекс.Карты", "2ГИС", "MAX"),
    },
    {
        "name": "Теги направлений",
        "type": "multiselect",
        "enums": enums("питомник", "груминг", "передержка"),
    },
    {
        "name": "Согласие ПДн",
        "type": "checkbox",
    },
    {
        "name": "Дата согласия ПДн",
        "type": "date",
    },
])
ok("Contact fields ×4", s, b)
time.sleep(0.5)

# ─────────────────────────────────────────────────────────
# 7. Lead fields — Щенки
# ─────────────────────────────────────────────────────────
print("\n── 7. Поля Сделок — воронка «Щенки» ──")
s, b = req("POST", "/api/v4/leads/custom_fields", [
    {"name": "Кличка",          "type": "text"},
    {"name": "Помёт",           "type": "text"},
    {"name": "Дата рождения",   "type": "date"},
    {
        "name": "Пол",
        "type": "select",
        "enums": enums("Сука", "Кобель"),
    },
    {"name": "Окрас",           "type": "text"},
    {"name": "Родители",        "type": "text"},
    {"name": "Цена",            "type": "numeric"},
    {"name": "Фото / Видео",    "type": "url"},
    {
        "name": "Статус прививки",
        "type": "select",
        "enums": enums("Без прививки", "1-я сделана", "2-я сделана", "Комплекс готов"),
    },
    {"name": "Дата прививки",              "type": "date"},
    {"name": "Медкарта",                   "type": "text"},
    {"name": "Готов к переезду с",         "type": "date"},
    {"name": "Дата брони",                 "type": "date"},
    {"name": "Срок брони",                 "type": "date"},
    {"name": "Сумма предоплаты",           "type": "numeric"},
    {
        "name": "Статус оплаты предоплаты",
        "type": "select",
        "enums": enums("Ожидается", "Частично", "Оплачена"),
    },
])
ok("Lead fields ×16 (Щенки)", s, b)
time.sleep(0.5)

# ─────────────────────────────────────────────────────────
# 8. Lead fields — Продажи + Рассрочка
# ─────────────────────────────────────────────────────────
print("\n── 8. Поля Сделок — воронка «Продажи» + рассрочка ──")
s, b = req("POST", "/api/v4/leads/custom_fields", [
    {
        "name": "Источник сделки",
        "type": "select",
        "enums": enums("Тильда", "Яндекс.Реклама", "Телеграм-канал",
                       "Рекомендации", "Яндекс.Карты", "2ГИС", "MAX"),
    },
    {"name": "Запрос / предпочтения",     "type": "textarea"},
    {
        "name": "Условие сделки",
        "type": "select",
        "enums": enums("Полная оплата", "Рассрочка"),
    },
    {"name": "Ожидаемая дата помёта",     "type": "date"},
    {"name": "Рассрочка: срок (мес)",     "type": "select", "enums": enums("3", "6", "10")},
    {"name": "Сумма к рассрочке",         "type": "numeric"},
    {"name": "Дата ближайшего платежа",   "type": "date"},
    {"name": "Остаток долга",             "type": "numeric"},
    {"name": "Связанный щенок (ID сделки)", "type": "numeric"},
])
ok("Lead fields ×9 (Продажи + рассрочка)", s, b)
time.sleep(0.5)

# ─────────────────────────────────────────────────────────
# Done
# ─────────────────────────────────────────────────────────
print("\n" + "─" * 50)
print("Итог:")
print(f"  Воронка «Продажи»:   ID {EXISTING_PIPELINE_ID}")
print(f"  Воронка «Щенки»:     ID {shchenki_id}")
print(f"  Воронка «Рассрочка»: ID {rassrochka_id}")
print("  Открыть: https://kerisclub.amocrm.ru")
