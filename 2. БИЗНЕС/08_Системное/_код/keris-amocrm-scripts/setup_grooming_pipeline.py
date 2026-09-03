"""Keris Club — воронка «Груминг» и поля под полную историю клиента.

Идемпотентно: воронка, этапы, поля сделки-визита, поля питомца на Компании и
метрики на Контакте создаются только если их ещё нет. Повторный прогон ничего
не дублирует и печатает фактические ID для `ДОСТУПЫ.md` и `.env`.

Запуск:
    AMOCRM_TOKEN=<долгосрочный токен> python3 setup_grooming_pipeline.py

Токен в файле не хранится (в отличие от старых `setup_amocrm.py` / `inspect_amocrm.py`) —
он лежит в `Keris_Club/06_Доступы/ДОСТУПЫ.md`.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

TOKEN = os.environ.get("AMOCRM_TOKEN", "").strip()
BASE = os.environ.get("AMOCRM_BASE_URL", "https://kerisclub.amocrm.ru").rstrip("/")

if not TOKEN:
    sys.exit("AMOCRM_TOKEN не задан. Токен — в Keris_Club/06_Доступы/ДОСТУПЫ.md")

HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

# Палитру amoCRM не документирует и произвольный hex отклоняет (400
# NotSupportedChoice — в том числе на цвета из старого setup_amocrm.py).
# Поэтому берём только те значения, которые живут в этом аккаунте.
C_GREY = "#e6e8ea"
C_BLUE = "#c1e0ff"
C_ORANGE = "#ffce5a"
C_YELLOW = "#fffd7f"
C_MINT = "#87f2c0"
# #D5D8DB и #c1c1c1 заняты системными Провалом и Неразобранным — на обычный
# этап amoCRM их не отдаёт.
C_ROSE = "#ffdbdb"
C_PINK = "#ffc8c8"

PIPELINE_NAME = "Груминг"

# Этап «Отменена» и «Не пришёл» — обычные, не системный Провал: сейлсбот
# отрабатывает их разными сценариями, закрытие владелец делает автоматизацией.
STAGES = [
    ("Запись создана", 10, C_GREY),
    ("Ожидает визита", 20, C_BLUE),
    ("Завтра запись", 30, C_ORANGE),
    ("Сегодня запись", 40, C_YELLOW),
    ("Клиент пришёл", 50, C_MINT),
    ("Отменена", 60, C_ROSE),
    ("Не пришёл", 70, C_PINK),
]

# Системные 142/143 API переименовывать не даёт (400 NotSupportedChoice на `id`),
# хотя в воронке «Продажи» они переименованы — это делалось руками в интерфейсе.
# Сервер целится в 142 по номеру, а не по названию, поэтому переименование —
# косметика на усмотрение владельца.
SUCCESS_STATUS_HINT = "Визит завершён"

# entity -> [(имя, тип, [значения select])]
FIELDS: dict[str, list[tuple[str, str, list[str]]]] = {
    "leads": [
        ("Номер записи", "text", []),
        ("Дата и время визита", "date_time", []),
        ("Мастер", "text", []),
        ("Услуга", "text", []),
        ("Допуслуги", "textarea", []),
        ("Питомец", "text", []),
        ("Размер", "text", []),
        ("Источник записи", "select", [
            "Mini App", "Сайт", "Яндекс.Карты / 2ГИС", "Журнал YCLIENTS", "Админ-бот",
        ]),
        ("Оплата", "text", []),
        ("Журнал YCLIENTS", "url", []),
        ("Фото-отчёт", "url", []),
        ("Что уточнить", "textarea", []),
    ],
    # Компания = питомец клиента (АРХИТЕКТУРА_ЭКОСИСТЕМЫ.md, Принцип №2).
    # «Имя собаки», «Дата рождения», «Пол», «Окрас», «Медкарта», «Родители» уже есть.
    "companies": [
        ("Порода", "text", []),
        # Вес текстом, а не numeric: amoCRM numeric — целое, а вес бывает 3.5 кг.
        ("Вес, кг", "text", []),
        ("Размер по прайсу", "text", []),
        ("Особенности / аллергии", "textarea", []),
        ("Куплен у Keris", "checkbox", []),
        ("Визитов всего", "numeric", []),
        ("Последний визит", "date", []),
    ],
    "contacts": [
        ("Груминг: визитов всего", "numeric", []),
        ("Груминг: сумма всего", "numeric", []),
        ("Груминг: средний чек", "numeric", []),
        ("Груминг: первый визит", "date", []),
        ("Груминг: последний визит", "date", []),
        ("Груминг: следующий визит", "date", []),
        ("Груминг: статус клиента", "select", [
            "Новый", "Активный", "Спящий 60+", "Потерянный 120+",
        ]),
        ("Груминг: отмен", "numeric", []),
        ("Груминг: неявок", "numeric", []),
        ("Абонемент: план", "text", []),
        ("Абонемент: остаток визитов", "text", []),
        ("Абонемент: действует до", "date", []),
    ],
}


def req(method: str, path: str, body=None):
    url = f"{BASE}{path}"
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
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


def ok(label: str, status: int, body=None) -> bool:
    if 200 <= status < 300:
        print(f"  + {label} [{status}]")
        return True
    detail = json.dumps(body, ensure_ascii=False)[:400] if body else ""
    print(f"  ! {label} [{status}]: {detail}")
    return False


def enums(values: list[str]) -> list[dict]:
    return [{"value": v, "sort": (i + 1) * 10} for i, v in enumerate(values)]


# ---------------------------------------------------------------------------
# Воронка и этапы
# ---------------------------------------------------------------------------

def find_pipeline(name: str) -> dict | None:
    s, b = req("GET", "/api/v4/leads/pipelines")
    if not (200 <= s < 300):
        sys.exit(f"не удалось прочитать воронки [{s}]: {b}")
    for p in (b.get("_embedded") or {}).get("pipelines") or []:
        if (p.get("name") or "").strip().lower() == name.lower():
            return p
    return None


def create_pipeline() -> dict:
    print(f"\n-- Создаём воронку «{PIPELINE_NAME}» --")
    s, b = req("POST", "/api/v4/leads/pipelines", [{
        "name": PIPELINE_NAME,
        "sort": 30,
        "is_main": False,
        # Неразобранное не нужно: сделки создаёт сервер, сразу на этапе.
        "is_unsorted_on": False,
        "_embedded": {"statuses": [
            {"name": n, "sort": srt, "color": c} for n, srt, c in STAGES
        ]},
    }])
    if not ok(f"воронка «{PIPELINE_NAME}»", s, b):
        sys.exit("воронка не создана")
    return ((b.get("_embedded") or {}).get("pipelines") or [{}])[0]


def ensure_stages(pipeline: dict) -> dict[str, int]:
    pid = pipeline["id"]
    existing = {
        (st.get("name") or "").strip().lower(): st
        for st in (pipeline.get("_embedded") or {}).get("statuses") or []
    }
    missing = [(n, srt, c) for n, srt, c in STAGES if n.lower() not in existing]
    if missing:
        print(f"\n-- Добавляем этапы ({len(missing)}) --")
        s, b = req("POST", f"/api/v4/leads/pipelines/{pid}/statuses",
                   [{"name": n, "sort": srt, "color": c} for n, srt, c in missing])
        ok(", ".join(n for n, _, _ in missing), s, b)
        time.sleep(0.4)

    success = existing.get("успешно реализовано")
    if success:
        print(f"  i этап Успех (142) — переименовать в «{SUCCESS_STATUS_HINT}» "
               "можно только руками в интерфейсе, API это не отдаёт")

    s, b = req("GET", f"/api/v4/leads/pipelines/{pid}")
    if not (200 <= s < 300):
        sys.exit(f"не удалось перечитать воронку [{s}]: {b}")
    result: dict[str, int] = {}
    for st in (b.get("_embedded") or {}).get("statuses") or []:
        result[(st.get("name") or "").strip()] = st["id"]
    return result


# ---------------------------------------------------------------------------
# Кастомные поля
# ---------------------------------------------------------------------------

def existing_fields(entity: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    page = 1
    while True:
        s, b = req("GET", f"/api/v4/{entity}/custom_fields?page={page}&limit=250")
        if not (200 <= s < 300):
            sys.exit(f"не удалось прочитать поля {entity} [{s}]: {b}")
        fields = (b.get("_embedded") or {}).get("custom_fields") or []
        if not fields:
            break
        for f in fields:
            out[(f.get("name") or "").strip().lower()] = f
        if len(fields) < 250:
            break
        page += 1
    return out


def ensure_fields(entity: str, wanted: list[tuple[str, str, list[str]]]) -> dict[str, int]:
    print(f"\n-- Поля {entity} --")
    have = existing_fields(entity)
    to_create = []
    for name, ftype, values in wanted:
        if name.lower() in have:
            print(f"  = {name} (ID {have[name.lower()]['id']})")
            continue
        item: dict = {"name": name, "type": ftype}
        if values:
            item["enums"] = enums(values)
        to_create.append(item)

    if to_create:
        s, b = req("POST", f"/api/v4/{entity}/custom_fields", to_create)
        ok(", ".join(i["name"] for i in to_create), s, b)
        time.sleep(0.5)
        have = existing_fields(entity)

    result: dict[str, int] = {}
    for name, _ftype, _values in wanted:
        field = have.get(name.lower())
        if field is None:
            print(f"  ! {name} — не создалось, проверить вручную")
            continue
        result[name] = field["id"]
    return result


def main() -> None:
    s, b = req("GET", "/api/v4/account")
    if not (200 <= s < 300):
        sys.exit(f"amoCRM недоступен [{s}]: {b}\nПроверь AMOCRM_TOKEN и AMOCRM_BASE_URL={BASE}")
    print(f"Аккаунт: {b.get('name')} ({b.get('id')}), {BASE}")

    pipeline = find_pipeline(PIPELINE_NAME)
    if pipeline is None:
        pipeline = create_pipeline()
    else:
        print(f"\n= Воронка «{PIPELINE_NAME}» уже есть (ID {pipeline['id']})")
    stages = ensure_stages(pipeline)

    field_ids = {entity: ensure_fields(entity, wanted) for entity, wanted in FIELDS.items()}

    print("\n" + "=" * 62)
    print(f"Воронка «{PIPELINE_NAME}»: ID {pipeline['id']}")
    print("\nЭтапы:")
    for name, sid in stages.items():
        print(f"  {name:24} {sid}")
    for entity, ids in field_ids.items():
        print(f"\nПоля {entity}:")
        for name, fid in ids.items():
            print(f"  {name:30} {fid}")
    print("\nДля .env keris-server:")
    print(f"  AMOCRM_BASE_URL={BASE}")
    print(f"  AMOCRM_PIPELINE_GROOMING_ID={pipeline['id']}")
    print("\nЭтапы и поля сервер резолвит по имени — ID в .env прописывать не нужно.")


if __name__ == "__main__":
    main()
