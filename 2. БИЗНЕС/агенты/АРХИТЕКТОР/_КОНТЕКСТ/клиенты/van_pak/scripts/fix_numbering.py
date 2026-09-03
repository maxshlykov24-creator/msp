"""
Пересборка нумерации документов Ван Пак с 1 мая 2026.

Типы: customerorder, demand → сквозная нумерация по (орг, тип)
      invoiceout → name = name связанного customerOrder
      factureout → name = name связанной отгрузки (demands[0])

Логика:
  1. Для каждой организации определить каноничный prefix из документов ДО from-date.
  2. Якорь = последний по moment документ ДО from-date с этим prefix → (prefix, counter, width).
  3. Документы >= from-date, sorted by moment → присвоить +1, +2…
  4. Двухпроходное переименование (temp → final), чтобы избежать коллизий.
  5. invoiceout: name = name связанного customerOrder, обрабатывается после customerorder.
  6. factureout: name = name связанного demand[0], обрабатывается после demand.

Запуск:
    python3 fix_numbering.py --dry --only demand
    python3 fix_numbering.py --account old
    python3 fix_numbering.py --account new
"""

import argparse
import json
import logging
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import requests

from ms_common import OLD_TOKEN as _OLD_TOKEN, NEW_TOKEN as _NEW_TOKEN

TOKENS = {
    "old": _OLD_TOKEN,
    "new": _NEW_TOKEN,
}
BASE = "https://api.moysklad.ru/api/remap/1.2"
SCRIPT_DIR = Path(__file__).parent
REQUEST_SLEEP = 0.08

# Принудительные префиксы — переопределяют автодетект для конкретных орг.
ORG_NAME_PREFIX_OVERRIDE: dict = {
    "ИП Афанасьева Наталья Александровна": "АФ-",
}

SEQ_TYPES = ["customerorder", "demand"]
ALL_TYPES = SEQ_TYPES + ["invoiceout", "factureout"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(SCRIPT_DIR / "fix_numbering.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


# ─── Утилиты HTTP ─────────────────────────────────────────────────────────────

def make_session(token):
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {token}", "Accept-Encoding": "gzip"})
    return s


def api_req(s, method, url, **kwargs):
    for attempt in range(6):
        try:
            r = s.request(method, url, timeout=40, **kwargs)
        except requests.exceptions.RequestException as e:
            if attempt < 5:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"{method} {url}: {e}") from e
        if r.status_code == 429:
            wait = int(r.headers.get("X-Lognex-Retry-After", 3000)) / 1000
            log.warning(f"429 rate-limit, жду {wait:.1f}с")
            time.sleep(wait + 0.5)
            continue
        if 500 <= r.status_code < 600 and attempt < 5:
            time.sleep(2 ** attempt)
            continue
        time.sleep(REQUEST_SLEEP)
        return r
    raise RuntimeError(f"Провал {method} {url}")


def get_all(s, path, params=None):
    p = dict(params or {})
    p["limit"] = 1000
    rows, offset = [], 0
    while True:
        p["offset"] = offset
        r = api_req(s, "GET", f"{BASE}/{path}", params=p)
        if not r.ok:
            log.warning(f"GET {path}: {r.status_code} {r.text[:150]}")
            break
        data = r.json()
        chunk = data.get("rows", [])
        rows.extend(chunk)
        if len(chunk) < 1000:
            break
        offset += 1000
    return rows


def uid(href):
    return href.split("/")[-1].split("?")[0] if href else ""


def put_name(s, doc_type, doc_uid, name, dry):
    """PUT name документа. В dry-режиме ничего не делает."""
    if dry:
        return True
    r = api_req(s, "PUT", f"{BASE}/entity/{doc_type}/{doc_uid}", json={"name": name})
    if not r.ok:
        log.warning(f"  ✗ PUT {doc_type}/{doc_uid} name={name!r}: {r.status_code} {r.text[:200]}")
        return False
    return True


# ─── Парсинг / форматирование номеров ────────────────────────────────────────

_NAME_RE = re.compile(r"^(.*?)(\d+)$")


def parse_name(name):
    """
    Разбить name на (prefix, number, width).
    "ВА-18332" → ("ВА-", 18332, 5)
    "00006"    → ("",    6,     5)
    Если нет числа в хвосте — вернуть None.
    """
    m = _NAME_RE.match(name or "")
    if not m:
        return None
    prefix, num_str = m.group(1), m.group(2)
    return prefix, int(num_str), len(num_str)


def format_name(prefix, number, width):
    return f"{prefix}{str(number).zfill(width)}"


# ─── Определение канонического prefix организации ─────────────────────────────

def detect_org_prefix(rows):
    """
    По списку документов одной организации определить канонический prefix.
    Возвращает prefix — самый частый среди документов.
    Ширину числа НЕ возвращаем — берём из якорного документа.
    """
    counts = Counter()
    for r in rows:
        parsed = parse_name(r.get("name", ""))
        if parsed:
            prefix, _, _ = parsed
            counts[prefix] += 1
    if not counts:
        return None
    return counts.most_common(1)[0][0]


# ─── Загрузка данных ──────────────────────────────────────────────────────────

def load_orgs(s):
    """Вернуть {uid: name} всех организаций."""
    rows = get_all(s, "entity/organization")
    return {uid(r["meta"]["href"]): r.get("name", "?") for r in rows}


def load_docs(s, doc_type, org_uid=None, from_date=None, before_date=None):
    """
    Загрузить документы типа. Опционально фильтр по орг, дате from/before.
    Возвращает list[dict] с полями name, moment, meta.
    """
    filters = []
    if org_uid:
        filters.append(f"organization={BASE}/entity/organization/{org_uid}")
    if from_date:
        filters.append(f"moment>={from_date}")
    if before_date:
        filters.append(f"moment<{before_date}")

    params = {"order": "moment"}
    if filters:
        params["filter"] = ";".join(filters)

    return get_all(s, f"entity/{doc_type}", params)


# ─── Основная логика: последовательные типы ──────────────────────────────────

def fix_sequence_type(s, doc_type, orgs, from_date, dry, org_filter=None):
    """
    Пересобрать нумерацию (doc_type) с from_date по всем организациям.
    Возвращает отчёт по каждой орг.
    """
    log.info(f"\n{'='*55}\n{doc_type}\n{'='*55}")
    report = {}

    for org_uid, org_name in orgs.items():
        if org_filter and org_uid != org_filter:
            continue

        log.info(f"\n  Орг: {org_name}")

        # Документы ДО from_date — для определения prefix и якоря
        old_rows = load_docs(s, doc_type, org_uid=org_uid, before_date=from_date)
        log.info(f"    Документов до {from_date}: {len(old_rows)}")

        canon_prefix = detect_org_prefix(old_rows)
        anchor_num = 0
        width = 5

        if canon_prefix is None:
            # Нет документов до from_date (новая организация).
            # Определяем prefix из документов demand той же орг. (как эталон).
            demand_rows = load_docs(s, "demand", org_uid=org_uid)
            canon_prefix = detect_org_prefix(demand_rows) or ""
            log.warning(
                f"    ⚠ Нет документов до {from_date}. "
                f"Prefix взят из demand: {canon_prefix!r}. Anchor=0."
            )
        else:
            log.info(f"    Канонический prefix: {canon_prefix!r}")

            # Якорь: последний по moment документ с каноничным prefix до from_date
            for r in reversed(old_rows):
                parsed = parse_name(r.get("name", ""))
                if parsed and parsed[0] == canon_prefix:
                    anchor_num = parsed[1]
                    width = parsed[2]
                    log.info(f"    Якорь: {r.get('name')!r} (moment={r.get('moment')}) → num={anchor_num} width={width}")
                    break

            if anchor_num == 0:
                log.warning(f"    ⚠ Якорь не найден по prefix {canon_prefix!r}, используем 0")

        # Принудительный prefix из ORG_NAME_PREFIX_OVERRIDE — переопределяет автодетект
        if org_name in ORG_NAME_PREFIX_OVERRIDE:
            canon_prefix = ORG_NAME_PREFIX_OVERRIDE[org_name]
            anchor_num = 0
            width = 5
            anchor_rows = list(old_rows)
            if doc_type != "customerorder":
                anchor_rows.extend(
                    load_docs(s, "customerorder", org_uid=org_uid, before_date=from_date)
                )
            for r in reversed(anchor_rows):
                parsed = parse_name(r.get("name", ""))
                if parsed and parsed[0] == canon_prefix:
                    anchor_num = parsed[1]
                    width = parsed[2]
                    log.info(
                        f"    ⚡ Принудительный prefix: {canon_prefix!r}, "
                        f"якорь {r.get('name')!r} → num={anchor_num}"
                    )
                    break
            else:
                log.info(f"    ⚡ Принудительный prefix: {canon_prefix!r} (anchor=0)")

        # Документы >= from_date — цель перенумерации
        target_rows = load_docs(s, doc_type, org_uid=org_uid, from_date=from_date)
        log.info(f"    Документов с {from_date}: {len(target_rows)}")

        if not target_rows:
            report[org_name] = {"skip": "no_docs_in_range", "anchor": anchor_num}
            continue

        # Вычислить финальные номера
        assignments = []  # [(doc_uid, old_name, new_name)]
        counter = anchor_num
        for r in target_rows:
            counter += 1
            doc_uid_v = uid(r["meta"]["href"])
            old_name = r.get("name", "")
            new_name = format_name(canon_prefix, counter, width)
            assignments.append((doc_uid_v, old_name, new_name))

        # Dry: просто вывести
        if dry:
            for doc_uid_v, old_name, new_name in assignments:
                changed = "CHANGE" if old_name != new_name else "ok"
                log.info(f"    [DRY] {changed}: {old_name!r} → {new_name!r}")
            changed_count = sum(1 for _, o, n in assignments if o != n)
            report[org_name] = {
                "anchor": anchor_num,
                "prefix": canon_prefix,
                "width": width,
                "docs_in_range": len(assignments),
                "would_change": changed_count,
                "first_few": [(o, n) for _, o, n in assignments[:5]],
            }
            continue

        # Реальный прогон: двухпроходное переименование
        need_change = [(d, o, n) for d, o, n in assignments if o != n]

        if not need_change:
            log.info(f"    ✓ Все номера уже верны")
            report[org_name] = {"anchor": anchor_num, "docs_in_range": len(assignments), "changed": 0}
            continue

        # Проход 1: временные имена (освобождаем целевые номера)
        log.info(f"    Проход 1/2: временные имена ({len(need_change)} шт)...")
        pass1_ok = 0
        for i, (doc_uid_v, old_name, new_name) in enumerate(need_change):
            tmp_name = f"TMP-{i:05d}-{doc_uid_v[:6]}"
            ok = put_name(s, doc_type, doc_uid_v, tmp_name, dry=False)
            if ok:
                pass1_ok += 1
            if (i + 1) % 80 == 0:
                log.info(f"    Пауза (80 PUT)... {i+1}/{len(need_change)}")
                time.sleep(62)

        log.info(f"    Проход 1 завершён: {pass1_ok}/{len(need_change)}")

        # Проход 2: финальные имена
        log.info(f"    Проход 2/2: финальные имена...")
        pass2_ok = pass2_err = 0
        failed_puts = []  # [(doc_uid_v, target_name)]
        time.sleep(0.5)  # небольшая пауза после прохода 1
        for i, (doc_uid_v, old_name, new_name) in enumerate(need_change):
            ok = put_name(s, doc_type, doc_uid_v, new_name, dry=False)
            if ok:
                pass2_ok += 1
            else:
                pass2_err += 1
                failed_puts.append((doc_uid_v, new_name))
            if (i + 1) % 80 == 0:
                log.info(f"    Пауза (80 PUT)... {i+1}/{len(need_change)}")
                time.sleep(62)

        log.info(f"    Проход 2 завершён: ok={pass2_ok} err={pass2_err}")

        # Проход 3 (восстановление коллизий):
        # Если после прохода 2 остались ошибки — значит в базе есть блокирующий
        # документ ДО from_date с тем же именем (аномальный "прыжок" до мая).
        # Переименовываем его в COL-{uid} и повторяем PUT.
        collision_fixes = []
        if pass2_err > 0:
            log.info(f"    Проход 3/3: устранение коллизий ({pass2_err} шт)...")
            # Загрузить АКТУАЛЬНЫЕ имена документов из БД
            current_rows = load_docs(s, doc_type, org_uid=org_uid)
            current_by_uid = {uid(r["meta"]["href"]): r for r in current_rows}
            name_to_uid = {r.get("name", ""): uid(r["meta"]["href"]) for r in current_rows}

            for doc_uid_v, target_name in failed_puts:
                current_name = (current_by_uid.get(doc_uid_v) or {}).get("name", "")
                if not current_name.startswith("TMP-"):
                    # Уже исправлен другим способом
                    continue

                # Найти блокирующего: другой документ с именем = target_name
                blocker_uid = name_to_uid.get(target_name)
                if not blocker_uid or blocker_uid == doc_uid_v:
                    log.warning(f"    ⚠ Блокирующий для {target_name!r} не найден")
                    continue
                col_name = f"COL-{blocker_uid[:8]}"

                # Шаг a: блокирующего → COL-...
                r_a = api_req(s, "PUT", f"{BASE}/entity/{doc_type}/{blocker_uid}",
                              json={"name": col_name})
                if not r_a.ok:
                    log.warning(f"    ✗ COL rename {blocker_uid}: {r_a.status_code}")
                    continue

                # Шаг b: TMP-документ → target_name
                r_b = api_req(s, "PUT", f"{BASE}/entity/{doc_type}/{doc_uid_v}",
                              json={"name": target_name})
                if r_b.ok:
                    pass2_ok += 1
                    pass2_err -= 1
                    collision_fixes.append({
                        "target": target_name,
                        "blocker_uid": blocker_uid,
                        "blocker_new_name": col_name,
                    })
                    log.info(f"    ✓ Коллизия устранена: {target_name!r}, "
                             f"блокирующий → {col_name!r}")
                else:
                    log.warning(f"    ✗ PUT {doc_uid_v} → {target_name!r}: {r_b.status_code}")

        report[org_name] = {
            "anchor": anchor_num,
            "prefix": canon_prefix,
            "width": width,
            "docs_in_range": len(assignments),
            "changed": pass2_ok,
            "errors": pass2_err,
            "collision_fixes": collision_fixes,
        }

    return report


# ─── Счёт покупателю: name = name связанного заказа покупателя ───────────────

def fix_invoiceout_from_customerorder(s, from_date, dry, account, org_filter=None):
    """
    Для каждого invoiceout >= from_date: name = name связанного customerOrder.

    Старый аккаунт: используем expand=customerOrder (поле есть напрямую).
    Новый аккаунт: поле customerOrder не перенесено при миграции.
        Сопоставляем по (org_uid, moment) позиционно.
    """
    log.info(f"\n{'='*55}\ninvoiceout → customerOrder.name\n{'='*55}")

    params = {"order": "moment", "filter": f"moment>={from_date}"}
    if org_filter:
        params["filter"] += f";organization={BASE}/entity/organization/{org_filter}"

    if account == "old":
        # Старый аккаунт: у каждого invoiceout есть ссылка на customerOrder
        params["expand"] = "customerOrder"
        rows = get_all(s, "entity/invoiceout", params)
        log.info(f"  Всего invoiceout >= {from_date}: {len(rows)}")

        no_co = already_ok = 0
        need_change = []

        for r in rows:
            co = r.get("customerOrder")
            if not co:
                log.warning(f"  ⚠ invoiceout {r.get('name')!r}: нет customerOrder, пропуск")
                no_co += 1
                continue

            co_name = co.get("name", "")
            if not co_name:
                # expand не вернул name — точечный GET
                co_uid_v = uid(co["meta"]["href"])
                rc = api_req(s, "GET", f"{BASE}/entity/customerorder/{co_uid_v}")
                if not rc.ok:
                    log.warning(f"  ✗ GET customerorder/{co_uid_v}: {rc.status_code}")
                    no_co += 1
                    continue
                co_name = rc.json().get("name", "")

            inv_uid_v = uid(r["meta"]["href"])
            old_name = r.get("name", "")

            if old_name == co_name:
                already_ok += 1
                continue

            need_change.append((inv_uid_v, old_name, co_name))

    else:
        # Новый аккаунт: нет поля customerOrder — матчим позиционно по (org_uid)
        # Для каждой орг сортируем invoiceout и customerorder по moment и
        # сопоставляем i-й invoiceout с i-м customerorder.
        rows = get_all(s, "entity/invoiceout", params)
        log.info(f"  Всего invoiceout >= {from_date}: {len(rows)}")

        # Загружаем customerorder с той же фильтрацией по дате
        co_params = {"order": "moment", "filter": f"moment>={from_date}"}
        if org_filter:
            co_params["filter"] += f";organization={BASE}/entity/organization/{org_filter}"
        co_rows = get_all(s, "entity/customerorder", co_params)
        log.info(f"  Всего customerorder >= {from_date}: {len(co_rows)}")

        # Сгруппировать customerorder по org_uid (порядок moment уже верный)
        co_by_org: dict = defaultdict(list)
        for cr in co_rows:
            o_uid = uid((cr.get("organization") or {}).get("meta", {}).get("href", ""))
            co_by_org[o_uid].append(cr.get("name", ""))

        # Позиционный счётчик для каждой орг
        co_used: dict = defaultdict(int)

        no_co = already_ok = 0
        need_change = []

        for r in rows:
            o_uid = uid((r.get("organization") or {}).get("meta", {}).get("href", ""))
            queue = co_by_org.get(o_uid, [])
            idx = co_used[o_uid]
            if idx >= len(queue):
                log.warning(
                    f"  ⚠ invoiceout {r.get('name')!r} (org={o_uid[:8]}): "
                    f"нет {idx+1}-го customerOrder по орг, пропуск"
                )
                no_co += 1
                continue

            co_name = queue[idx]
            co_used[o_uid] += 1

            inv_uid_v = uid(r["meta"]["href"])
            old_name = r.get("name", "")

            if old_name == co_name:
                already_ok += 1
                continue

            need_change.append((inv_uid_v, old_name, co_name))

    log.info(f"  Нужно изменить: {len(need_change)}, уже верно: {already_ok}, без customerOrder: {no_co}")

    if dry:
        for inv_uid_v, old_name, co_name in need_change[:30]:
            log.info(f"  [DRY] {old_name!r} → {co_name!r}")
        if len(need_change) > 30:
            log.info(f"  [DRY] ...ещё {len(need_change) - 30} изменений")
        return {
            "would_change": len(need_change),
            "already_ok": already_ok,
            "no_customerorder": no_co,
        }

    # Двухпроходное: сначала temp (на случай коллизий между счетами)
    log.info(f"  Проход 1/2: временные имена...")
    for i, (inv_uid_v, old_name, co_name) in enumerate(need_change):
        tmp = f"TMP-INV-{i:05d}"
        put_name(s, "invoiceout", inv_uid_v, tmp, dry=False)
        if (i + 1) % 80 == 0:
            time.sleep(62)

    log.info(f"  Проход 2/2: финальные имена...")
    ok2 = err2 = 0
    failed_puts = []
    time.sleep(0.5)
    for i, (inv_uid_v, old_name, co_name) in enumerate(need_change):
        if put_name(s, "invoiceout", inv_uid_v, co_name, dry=False):
            ok2 += 1
        else:
            err2 += 1
            failed_puts.append((inv_uid_v, co_name))
        if (i + 1) % 80 == 0:
            time.sleep(62)

    log.info(f"  Проход 2 завершён: ok={ok2} err={err2}")

    # Проход 3: устранение коллизий с pre-from_date документами
    if failed_puts:
        log.info(f"  Проход 3/3: устранение коллизий ({len(failed_puts)} шт)...")
        all_rows = get_all(s, "entity/invoiceout", {})
        current_by_uid = {uid(r["meta"]["href"]): r for r in all_rows}
        name_to_uid_map = {r.get("name", ""): uid(r["meta"]["href"]) for r in all_rows}

        for inv_uid_v, target_name in failed_puts:
            current_name = (current_by_uid.get(inv_uid_v) or {}).get("name", "")
            if not current_name.startswith("TMP-"):
                continue

            blocker_uid = name_to_uid_map.get(target_name)
            if not blocker_uid or blocker_uid == inv_uid_v:
                log.warning(f"  ⚠ Блокирующий для {target_name!r} не найден")
                continue

            col_name = f"COL-{blocker_uid[:8]}"
            r_a = api_req(s, "PUT", f"{BASE}/entity/invoiceout/{blocker_uid}", json={"name": col_name})
            if not r_a.ok:
                log.warning(f"  ✗ COL rename {blocker_uid}: {r_a.status_code}")
                continue

            r_b = api_req(s, "PUT", f"{BASE}/entity/invoiceout/{inv_uid_v}", json={"name": target_name})
            if r_b.ok:
                ok2 += 1
                err2 -= 1
                log.info(f"  ✓ Коллизия устранена: {target_name!r}, блокирующий → {col_name!r}")
            else:
                log.warning(f"  ✗ PUT {inv_uid_v} → {target_name!r}: {r_b.status_code}")

    log.info(f"  invoiceout: ok={ok2} err={err2} no_customerorder={no_co}")
    return {"changed": ok2, "errors": err2, "already_ok": already_ok, "no_customerorder": no_co}


# ─── Счёт-фактура: name = name связанной отгрузки ─────────────────────────────

def fix_factureout(s, from_date, dry, org_filter=None):
    """
    Для каждого factureout >= from_date: name = name связанного demand (demands[0]).
    demand загружаем отдельным GET (нужен свежий name после перенумерации).
    """
    log.info(f"\n{'='*55}\nfactureout\n{'='*55}")

    params = {"order": "moment", "filter": f"moment>={from_date}"}
    if org_filter:
        params["filter"] += f";organization={BASE}/entity/organization/{org_filter}"

    # expand=demands чтобы получить demand.name напрямую (без отдельного GET).
    params["expand"] = "demands"
    rows = get_all(s, "entity/factureout", params)
    log.info(f"  Всего factureout >= {from_date}: {len(rows)}")

    already_ok = no_demand = 0
    need_change = []

    for r in rows:
        demands = r.get("demands") or []
        if not demands:
            log.warning(f"  ⚠ factureout {r.get('name')!r}: нет demands, пропуск")
            no_demand += 1
            continue

        demand_name = demands[0].get("name", "")
        if not demand_name:
            # expand не вернул name — делаем точечный GET
            demand_uid_v = uid(demands[0]["meta"]["href"])
            rd = api_req(s, "GET", f"{BASE}/entity/demand/{demand_uid_v}")
            if not rd.ok:
                log.warning(f"  ✗ GET demand/{demand_uid_v}: {rd.status_code}")
                no_demand += 1
                continue
            demand_name = rd.json().get("name", "")

        facture_uid_v = uid(r["meta"]["href"])
        old_name = r.get("name", "")

        if old_name == demand_name:
            already_ok += 1
            continue

        need_change.append((facture_uid_v, old_name, demand_name))

    log.info(f"  Нужно изменить: {len(need_change)}, уже верно: {already_ok}, нет отгрузки: {no_demand}")

    if dry:
        for facture_uid_v, old_name, demand_name in need_change:
            log.info(f"  [DRY] {old_name!r} → {demand_name!r}")
        return {
            "would_change": len(need_change),
            "already_ok": already_ok,
            "no_demand": no_demand,
        }

    # Двухпроходное: сначала temp (на случай коллизий между счетами-фактурами)
    log.info(f"  Проход 1/2: временные имена...")
    for i, (facture_uid_v, old_name, demand_name) in enumerate(need_change):
        tmp = f"TMP-F-{i:05d}"
        put_name(s, "factureout", facture_uid_v, tmp, dry=False)
        if (i + 1) % 80 == 0:
            time.sleep(62)

    log.info(f"  Проход 2/2: финальные имена...")
    ok2 = err2 = 0
    for i, (facture_uid_v, old_name, demand_name) in enumerate(need_change):
        if put_name(s, "factureout", facture_uid_v, demand_name, dry=False):
            ok2 += 1
        else:
            err2 += 1
        if (i + 1) % 80 == 0:
            time.sleep(62)

    log.info(f"  factureout: ok={ok2} err={err2} no_demand={no_demand}")
    return {"changed": ok2, "errors": err2, "already_ok": already_ok, "no_demand": no_demand}


# ─── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Пересборка нумерации документов Ван Пак")
    parser.add_argument("--account", choices=["old", "new"], default="old",
                        help="Какой аккаунт (old=старый, new=новый). По умолчанию old.")
    parser.add_argument("--dry", action="store_true", help="Показать изменения без PUT")
    parser.add_argument("--from-date", default="2026-05-01 00:00:00",
                        help="Начало диапазона. По умолчанию 2026-05-01.")
    parser.add_argument("--only", choices=ALL_TYPES + ["seq"], default=None,
                        help="Обработать только один тип (seq = customerorder+demand)")
    parser.add_argument("--org", default=None, help="UUID организации (для точечного теста)")
    args = parser.parse_args()

    token = TOKENS[args.account]
    s = make_session(token)
    from_date = args.from_date

    log.info(f"\nАккаунт: {args.account} | from-date: {from_date} | dry: {args.dry} | only: {args.only}")

    orgs = load_orgs(s)
    log.info(f"Организаций: {len(orgs)}")
    for uid_v, name in orgs.items():
        log.info(f"  {uid_v[:8]}… {name}")

    report = {"account": args.account, "from_date": from_date, "dry": args.dry}

    # Определить какие типы обрабатываем
    run_seq = []
    run_invoiceout = False
    run_factureout = False

    if args.only == "factureout":
        run_factureout = True
    elif args.only == "invoiceout":
        run_invoiceout = True
    elif args.only == "seq":
        run_seq = SEQ_TYPES
    elif args.only in SEQ_TYPES:
        run_seq = [args.only]
    else:
        # Всё
        run_seq = SEQ_TYPES
        run_invoiceout = True
        run_factureout = True

    # Последовательные типы (customerorder, demand)
    for doc_type in run_seq:
        report[doc_type] = fix_sequence_type(
            s, doc_type, orgs, from_date, args.dry, org_filter=args.org
        )

    # Счёт покупателю (invoiceout = customerOrder.name) — ПОСЛЕ customerorder
    if run_invoiceout:
        report["invoiceout"] = fix_invoiceout_from_customerorder(
            s, from_date, args.dry, args.account, org_filter=args.org
        )

    # Счёт-фактура — ПОСЛЕ demand
    if run_factureout:
        report["factureout"] = fix_factureout(s, from_date, args.dry, org_filter=args.org)

    # Сохранить отчёт
    out = SCRIPT_DIR / "fix_numbering_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(f"\nОтчёт: {out}")


if __name__ == "__main__":
    main()
