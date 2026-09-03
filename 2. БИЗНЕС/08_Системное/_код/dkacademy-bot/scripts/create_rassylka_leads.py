#!/usr/bin/env python3
"""
Создание сделок «Рассылка → Подготовка» из выборки «Продажи (Успех+Провал, Источник=Dkacademy_krd_bot)».

Запуск (DRY_RUN по умолчанию True):
  cat scripts/create_rassylka_leads.py | \
    ssh -i ~/.ssh/dkacademy_marta_ed25519 root@213.176.65.241 \
    'docker compose -f /root/dkacademy-bot/docker-compose.yml exec -T api python3 -'

Боевой прогон:
  DRY_RUN=false cat scripts/create_rassylka_leads.py | \
    ssh -i ~/.ssh/dkacademy_marta_ed25519 root@213.176.65.241 \
    'DRY_RUN=false docker compose -f /root/dkacademy-bot/docker-compose.yml exec -T api python3 -'
"""
from __future__ import annotations

import os
import sys
import time
from typing import Any

import httpx

# ── Настройки ──────────────────────────────────────────────────────────────────
DRY_RUN: bool = os.environ.get("DRY_RUN", "true").lower() not in ("false", "0", "no")
BATCH_SIZE: int = int(os.environ.get("BATCH_SIZE", "50"))
SLEEP_SEC: float = float(os.environ.get("SLEEP_SEC", "0.2"))

AMO_SUBDOMAIN: str = os.environ.get("AMO_SUBDOMAIN", "").strip()
AMO_BASE_DOMAIN: str = os.environ.get("AMO_BASE_DOMAIN", "amocrm.ru").strip()
AMO_TOKEN: str = os.environ.get("AMO_LONG_LIVED_TOKEN", "").strip()

# Имена для авто-дискавери (должны совпадать с названиями в amoCRM)
SRC_PIPELINE_NAME = "Продажи"
DST_PIPELINE_NAME = "Рассылка"
USPEH_STATUS_NAME = "Успешно реализовано"      # финальный Успех (может быть просто "Успех")
PROVAL_STATUS_NAME = "Закрыто и не реализовано"  # финальный Провал
DST_STATUS_NAME = "Подготовка"
SOURCE_FIELD_NAME = "Источник"
SOURCE_ENUM_VALUE = "Dkacademy_krd_bot"

# ── Помощники ──────────────────────────────────────────────────────────────────

BASE_URL = f"https://{AMO_SUBDOMAIN}.{AMO_BASE_DOMAIN}"
HEADERS = {
    "Authorization": f"Bearer {AMO_TOKEN}",
    "Content-Type": "application/json",
    "Accept": "application/hal+json",
}


def check_env() -> None:
    if not AMO_SUBDOMAIN:
        sys.exit("AMO_SUBDOMAIN не задан — убедитесь что запускаете внутри контейнера api")
    if not AMO_TOKEN:
        sys.exit("AMO_LONG_LIVED_TOKEN не задан — убедитесь что запускаете внутри контейнера api")


def amo_get(client: httpx.Client, path: str, params: list[tuple[str, Any]] | None = None) -> dict:
    url = f"{BASE_URL}{path}"
    r = client.get(url, params=params or [], headers=HEADERS, timeout=30)
    if r.status_code == 204:
        return {}
    if r.status_code >= 400:
        print(f"  [ERR] GET {path} → {r.status_code}: {r.text[:300]}", file=sys.stderr)
        return {}
    return r.json() or {}


def amo_post(client: httpx.Client, path: str, body: list) -> dict:
    url = f"{BASE_URL}{path}"
    r = client.post(url, json=body, headers=HEADERS, timeout=30)
    if r.status_code >= 400:
        print(f"  [ERR] POST {path} → {r.status_code}: {r.text[:500]}", file=sys.stderr)
        return {}
    return r.json() or {}


# ── Шаг 0: авто-дискавери ─────────────────────────────────────────────────────

def discover(client: httpx.Client) -> tuple[int, int, int, int, int, int | None]:
    """
    Возвращает:
      src_pipeline_id, uspeh_status_id, proval_status_id,
      dst_pipeline_id, podgotovka_status_id,
      source_field_id (None если не нашли)
    """
    print("=" * 60)
    print("ШАГ 0: Дискавери воронок, этапов и полей")
    print("=" * 60)

    data = amo_get(client, "/api/v4/leads/pipelines", [("limit", "250")])
    pipelines = (data.get("_embedded") or {}).get("pipelines") or []

    src_pipeline_id = dst_pipeline_id = 0
    uspeh_status_id = proval_status_id = podgotovka_status_id = 0

    for pipe in pipelines:
        pid = pipe.get("id", 0)
        name = (pipe.get("name") or "").strip()
        statuses = (pipe.get("_embedded") or {}).get("statuses") or []
        status_map = {(s.get("name") or "").strip(): s.get("id", 0) for s in statuses}

        print(f"\n  Воронка [{pid}] «{name}»")
        for sname, sid in status_map.items():
            print(f"    Этап [{sid}] «{sname}»")

        if name == SRC_PIPELINE_NAME:
            src_pipeline_id = pid
            # Ищем «Успех» — могут быть разные формулировки
            for sname, sid in status_map.items():
                if "успеш" in sname.lower() or sname == "Успех":
                    uspeh_status_id = sid
                if "закрыто" in sname.lower() and "не реализ" in sname.lower() or sname == "Провал":
                    proval_status_id = sid

        if name == DST_PIPELINE_NAME:
            dst_pipeline_id = pid
            for sname, sid in status_map.items():
                if DST_STATUS_NAME.lower() in sname.lower():
                    podgotovka_status_id = sid

    # Кастомные поля сделок
    source_field_id: int | None = None
    source_enum_id: int | None = None
    cf_data = amo_get(client, "/api/v4/leads/custom_fields", [("limit", "250")])
    fields = (cf_data.get("_embedded") or {}).get("custom_fields") or []
    for f in fields:
        fname = (f.get("name") or "").strip()
        if fname == SOURCE_FIELD_NAME:
            source_field_id = f.get("id")
            enums = f.get("enums") or []
            print(f"\n  Поле [{source_field_id}] «{fname}» (enum-значения):")
            for e in enums:
                eid = e.get("id")
                eval_ = e.get("value") or ""
                print(f"    [{eid}] «{eval_}»")
                if eval_.strip() == SOURCE_ENUM_VALUE:
                    source_enum_id = eid
            break

    print("\n" + "=" * 60)
    print("ИТОГ ДИСКАВЕРИ:")
    print(f"  Источник: «{SRC_PIPELINE_NAME}» pipeline_id={src_pipeline_id}")
    print(f"  Успех:    status_id={uspeh_status_id}")
    print(f"  Провал:   status_id={proval_status_id}")
    print(f"  Рассылка: «{DST_PIPELINE_NAME}» pipeline_id={dst_pipeline_id}")
    print(f"  Подготовка: status_id={podgotovka_status_id}")
    print(f"  Поле «{SOURCE_FIELD_NAME}»: field_id={source_field_id}, enum_id={source_enum_id} («{SOURCE_ENUM_VALUE}»)")
    print("=" * 60)

    errors = []
    if not src_pipeline_id:
        errors.append(f"Воронка «{SRC_PIPELINE_NAME}» не найдена")
    if not uspeh_status_id:
        errors.append(f"Этап «Успех» в «{SRC_PIPELINE_NAME}» не найден")
    if not proval_status_id:
        errors.append(f"Этап «Провал» в «{SRC_PIPELINE_NAME}» не найден")
    if not dst_pipeline_id:
        errors.append(f"Воронка «{DST_PIPELINE_NAME}» не найдена")
    if not podgotovka_status_id:
        errors.append(f"Этап «{DST_STATUS_NAME}» в «{DST_PIPELINE_NAME}» не найден")
    if not source_field_id:
        errors.append(f"Поле «{SOURCE_FIELD_NAME}» не найдено")
    if not source_enum_id:
        errors.append(f"Enum «{SOURCE_ENUM_VALUE}» в поле «{SOURCE_FIELD_NAME}» не найден")

    if errors:
        print("\n[ОШИБКИ ДИСКАВЕРИ]")
        for e in errors:
            print(f"  - {e}")
        print("\nПроверьте названия воронок/этапов выше и скорректируйте константы в скрипте.")
        sys.exit(1)

    return (
        src_pipeline_id, uspeh_status_id, proval_status_id,
        dst_pipeline_id, podgotovka_status_id,
        source_field_id, source_enum_id,
    )


# ── Шаг 1–2: выгрузка источника ───────────────────────────────────────────────

def fetch_source_contact_ids(
    client: httpx.Client,
    src_pipeline_id: int,
    uspeh_status_id: int,
    proval_status_id: int,
    source_field_id: int,
    source_enum_id: int,
) -> set[int]:
    """Выгружает сделки из Продажи (Успех+Провал), фильтрует по Источник=Dkacademy_krd_bot,
    возвращает set контакт-id."""
    print("\nШАГ 1-2: Выгрузка сделок из «Продажи» (Успех + Провал)...")
    contact_ids: set[int] = set()
    total_leads = 0
    matched_leads = 0
    page = 1

    while True:
        params: list[tuple[str, Any]] = [
            ("filter[statuses][0][pipeline_id]", src_pipeline_id),
            ("filter[statuses][0][status_id]", uspeh_status_id),
            ("filter[statuses][1][pipeline_id]", src_pipeline_id),
            ("filter[statuses][1][status_id]", proval_status_id),
            ("with", "contacts"),
            ("limit", "250"),
            ("page", str(page)),
        ]
        data = amo_get(client, "/api/v4/leads", params)
        leads = (data.get("_embedded") or {}).get("leads") or []
        if not leads:
            break

        for lead in leads:
            total_leads += 1
            # Фильтр по «Источник» в коде
            if not _has_source_value(lead, source_field_id, source_enum_id):
                continue
            matched_leads += 1
            # Собираем контакты
            contacts = (lead.get("_embedded") or {}).get("contacts") or []
            for c in contacts:
                cid = c.get("id")
                if isinstance(cid, int):
                    contact_ids.add(cid)

        print(f"  стр. {page}: {len(leads)} сделок, подошло {matched_leads} (всего просмотрено {total_leads})")

        if len(leads) < 250:
            break
        page += 1
        time.sleep(SLEEP_SEC)

    print(f"  Итого: просмотрено {total_leads}, подошло под фильтр {matched_leads}, уникальных контактов: {len(contact_ids)}")
    return contact_ids


def _has_source_value(lead: dict, field_id: int, enum_id: int) -> bool:
    """Проверяет поле «Источник» по enum_id (точно) и по строке (fallback)."""
    for cf in lead.get("custom_fields_values") or []:
        if cf.get("field_id") != field_id:
            continue
        for v in cf.get("values") or []:
            if v.get("enum_id") == enum_id:
                return True
            # fallback: строковое совпадение
            val_str = str(v.get("value") or "").strip()
            if val_str == SOURCE_ENUM_VALUE:
                return True
    return False


# ── Шаг 3: существующие дубли ─────────────────────────────────────────────────

def fetch_existing_contact_ids(
    client: httpx.Client,
    dst_pipeline_id: int,
    podgotovka_status_id: int,
) -> set[int]:
    """Возвращает set contact_id уже существующих в Рассылка → Подготовка."""
    print("\nШАГ 3: Выгрузка уже существующих сделок «Рассылка → Подготовка»...")
    existing: set[int] = set()
    page = 1

    while True:
        params: list[tuple[str, Any]] = [
            ("filter[statuses][0][pipeline_id]", dst_pipeline_id),
            ("filter[statuses][0][status_id]", podgotovka_status_id),
            ("with", "contacts"),
            ("limit", "250"),
            ("page", str(page)),
        ]
        data = amo_get(client, "/api/v4/leads", params)
        leads = (data.get("_embedded") or {}).get("leads") or []
        if not leads:
            break

        for lead in leads:
            contacts = (lead.get("_embedded") or {}).get("contacts") or []
            for c in contacts:
                cid = c.get("id")
                if isinstance(cid, int):
                    existing.add(cid)

        print(f"  стр. {page}: {len(leads)} сделок (existing contacts пока: {len(existing)})")
        if len(leads) < 250:
            break
        page += 1
        time.sleep(SLEEP_SEC)

    print(f"  Итого существующих контактов в «Подготовка»: {len(existing)}")
    return existing


# ── Шаг 4: создание сделок ────────────────────────────────────────────────────

def create_leads(
    client: httpx.Client,
    contact_ids: list[int],
    dst_pipeline_id: int,
    podgotovka_status_id: int,
) -> None:
    total = len(contact_ids)
    print(f"\nШАГ 4: Создание {total} сделок (DRY_RUN={DRY_RUN}, batch={BATCH_SIZE})...")

    created = 0
    for i in range(0, total, BATCH_SIZE):
        batch = contact_ids[i : i + BATCH_SIZE]
        body = [
            {
                "pipeline_id": dst_pipeline_id,
                "status_id": podgotovka_status_id,
                "created_by": 0,
                "_embedded": {
                    "contacts": [{"id": cid, "is_main": True}]
                },
            }
            for cid in batch
        ]

        if DRY_RUN:
            print(f"  [DRY] batch {i // BATCH_SIZE + 1}: создал бы {len(batch)} сделок "
                  f"(contact_ids: {batch[:5]}{'...' if len(batch) > 5 else ''})")
        else:
            resp = amo_post(client, "/api/v4/leads", body)
            new_leads = (resp.get("_embedded") or {}).get("leads") or []
            created += len(new_leads)
            print(f"  batch {i // BATCH_SIZE + 1}: создано {len(new_leads)} из {len(batch)} "
                  f"(итого: {created}/{total})")
            time.sleep(SLEEP_SEC)

    if DRY_RUN:
        print(f"\n[DRY_RUN] Создал бы {total} сделок. Для боевого запуска: DRY_RUN=false")
    else:
        print(f"\nГотово. Создано {created} сделок в «Рассылка → Подготовка».")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    check_env()
    print(f"\nDKAcademy → Создание сделок «Рассылка/Подготовка»")
    print(f"Аккаунт: {AMO_SUBDOMAIN}.{AMO_BASE_DOMAIN}")
    print(f"DRY_RUN={DRY_RUN}  BATCH_SIZE={BATCH_SIZE}  SLEEP_SEC={SLEEP_SEC}\n")

    with httpx.Client(timeout=30) as client:
        (
            src_pipeline_id, uspeh_status_id, proval_status_id,
            dst_pipeline_id, podgotovka_status_id,
            source_field_id, source_enum_id,
        ) = discover(client)

        source_contacts = fetch_source_contact_ids(
            client,
            src_pipeline_id, uspeh_status_id, proval_status_id,
            source_field_id, source_enum_id,
        )

        existing_contacts = fetch_existing_contact_ids(
            client, dst_pipeline_id, podgotovka_status_id
        )

        to_create = sorted(source_contacts - existing_contacts)

        print("\n" + "=" * 60)
        print("ИТОГОВЫЙ ОТЧЁТ:")
        print(f"  Источник (Продажи Успех+Провал, Источник={SOURCE_ENUM_VALUE}): {len(source_contacts)} контактов")
        print(f"  Уже в «Рассылка → Подготовка»:                                 {len(existing_contacts)} контактов")
        print(f"  Будет создано:                                                  {len(to_create)} сделок")
        print("=" * 60)

        if not to_create:
            print("\nНечего создавать — все контакты уже в «Подготовка».")
            return

        create_leads(client, to_create, dst_pipeline_id, podgotovka_status_id)


if __name__ == "__main__":
    main()
