"""
Проставить в новом аккаунте МойСклад:
  1. owner (Владелец) ← сотрудник из доп. поля «Ответственный» (если заполнено)
  2. group (Отдел) = «Головной офис» — у всех документов

Запуск:
    python set_owner_and_group.py --dry      # только статистика и отчёт
    python set_owner_and_group.py            # боевой прогон
    python set_owner_and_group.py --only customerorder
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

import requests

NEW_TOKEN = "619a40e860cb6ad975c3d05cae7157b29caccc0e"
BASE = "https://api.moysklad.ru/api/remap/1.2"
SCRIPT_DIR = Path(__file__).parent
SKIPPED_FILE = SCRIPT_DIR / "owner_skipped.txt"
UNRESOLVED_FILE = SCRIPT_DIR / "owner_unresolved.txt"
LOG_FILE = SCRIPT_DIR / "set_owner_and_group.log"

GROUP_NAME = "Головной офис"
RESP_ATTR_NAME = "Ответственный"
BATCH_SIZE = 50
REQUEST_SLEEP = 0.07

TYPES_WITH_RESPONSIBLE = ["customerorder", "invoiceout", "demand", "paymentin"]
TYPES_GROUP_ONLY = [
    "factureout",
    "purchaseorder",
    "supply",
    "processingorder",
    "processing",
    "move",
    "enter",
    "loss",
    "purchasereturn",
]
ALL_TYPES = TYPES_WITH_RESPONSIBLE + TYPES_GROUP_ONLY

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


def make_session(token: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {token}", "Accept-Encoding": "gzip"})
    return s


def api(s: requests.Session, method: str, url: str, **kwargs) -> requests.Response:
    timeout = kwargs.pop("timeout", 120 if method == "POST" else 60)
    for attempt in range(8):
        try:
            r = s.request(method, url, timeout=timeout, **kwargs)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            if attempt < 7:
                wait = min(30, 2 ** attempt)
                log.warning("%s %s timeout/conn (attempt %s): %s", method, url, attempt + 1, exc)
                time.sleep(wait)
                continue
            raise RuntimeError(f"{method} {url}: {exc}") from exc
        except requests.exceptions.RequestException as exc:
            if attempt < 5:
                time.sleep(2**attempt)
                continue
            raise RuntimeError(f"{method} {url}: {exc}") from exc
        if r.status_code == 429:
            wait = int(r.headers.get("X-Lognex-Retry-After", 3000)) / 1000
            time.sleep(wait + 0.5)
            continue
        if 500 <= r.status_code < 600 and attempt < 5:
            time.sleep(2**attempt)
            continue
        time.sleep(REQUEST_SLEEP)
        return r
    raise RuntimeError(f"Провал {method} {url}")


def uid(href: str) -> str:
    return href.split("/")[-1].split("?")[0] if href else ""


def get_all(s: requests.Session, doc_type: str) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    while True:
        r = api(
            s,
            "GET",
            f"{BASE}/entity/{doc_type}",
            params={"limit": 1000, "offset": offset, "order": "moment"},
        )
        if not r.ok:
            log.warning("GET %s: %s %s", doc_type, r.status_code, r.text[:200])
            break
        chunk = r.json().get("rows", [])
        rows.extend(chunk)
        if len(chunk) < 1000:
            break
        offset += 1000
    return rows


def resolve_group_meta(s: requests.Session) -> dict:
    r = api(s, "GET", f"{BASE}/entity/group", params={"limit": 100})
    r.raise_for_status()
    for g in r.json().get("rows", []):
        if g.get("name") == GROUP_NAME:
            return g["meta"]
    raise RuntimeError(f"Группа {GROUP_NAME!r} не найдена в новом аккаунте")


def load_preferred_owners(s: requests.Session) -> tuple[dict[str, dict], dict[str, bool]]:
    """Краткое имя → meta; второй dict: href → has_login."""
    r = api(s, "GET", f"{BASE}/entity/employee", params={"limit": 100})
    r.raise_for_status()
    preferred: dict[str, tuple[dict, bool]] = {}
    login_by_href: dict[str, bool] = {}
    for emp in r.json().get("rows", []):
        href = emp["meta"]["href"]
        has_login = bool(emp.get("uid"))
        login_by_href[href] = has_login
        name = emp.get("name", "")
        if name not in preferred or (has_login and not preferred[name][1]):
            preferred[name] = (emp["meta"], has_login)
    return {name: meta for name, (meta, _) in preferred.items()}, login_by_href


def resolve_owner_meta(
    s: requests.Session,
    responsible: dict,
    preferred_by_name: dict[str, dict],
    login_by_href: dict[str, bool],
    emp_cache: dict[str, dict | None],
) -> dict | None:
    """Meta employee для owner — только с логином в системе."""
    href = responsible.get("meta", {}).get("href", "")
    if not href:
        return None
    if href not in emp_cache:
        r = api(s, "GET", href)
        if not r.ok:
            meta = responsible.get("meta") or {}
            emp_cache[href] = meta if login_by_href.get(meta.get("href", ""), False) else None
        else:
            emp = r.json()
            if emp.get("uid"):
                emp_cache[href] = emp["meta"]
            elif emp.get("name") in preferred_by_name:
                pref = preferred_by_name[emp.get("name", "")]
                emp_cache[href] = pref if login_by_href.get(pref.get("href", ""), False) else None
            else:
                emp_cache[href] = None
    return emp_cache.get(href)


def get_responsible_employee(doc: dict) -> dict | None:
    for attr in doc.get("attributes", []):
        if attr.get("name") != RESP_ATTR_NAME:
            continue
        value = attr.get("value")
        if not value or not isinstance(value, dict):
            return None
        meta = value.get("meta") or {}
        if meta.get("href"):
            return value
    return None


def group_href(doc: dict) -> str:
    return (doc.get("group") or {}).get("meta", {}).get("href", "")


def owner_href(doc: dict) -> str:
    return (doc.get("owner") or {}).get("meta", {}).get("href", "")


def build_update(
    doc: dict,
    group_meta: dict,
    with_owner: bool,
    owner_meta: dict | None = None,
) -> dict | None:
    target_group = group_meta["href"]
    current_group = group_href(doc)
    group_needs = current_group != target_group

    payload: dict = {"meta": doc["meta"]}
    owner_needs = False

    if with_owner and owner_meta:
        target_owner = owner_meta["href"]
        owner_needs = owner_href(doc) != target_owner
        if owner_needs:
            payload["owner"] = {"meta": owner_meta}

    if group_needs:
        payload["group"] = {"meta": group_meta}

    if group_needs or owner_needs:
        return payload
    return None


def put_fallback(s: requests.Session, doc_type: str, item: dict) -> bool:
    doc_uid = uid(item["meta"]["href"])
    r_get = api(s, "GET", f"{BASE}/entity/{doc_type}/{doc_uid}")
    if not r_get.ok:
        log.warning("  GET %s/%s: %s", doc_type, doc_uid[:8], r_get.status_code)
        return False

    existing = r_get.json()
    body: dict = {"meta": existing["meta"]}
    for key in ("group", "owner"):
        if key in item:
            body[key] = item[key]

    # Типы с обязательными полями при PUT
    if doc_type == "processingorder":
        for req in ("organization", "processingPlan"):
            if req in existing:
                body[req] = existing[req]
    elif doc_type == "processing":
        for req in ("organization", "processingPlan", "materials", "products"):
            if req in existing:
                body[req] = existing[req]

    r1 = api(s, "PUT", f"{BASE}/entity/{doc_type}/{doc_uid}", json=body)
    if r1.ok:
        return True
    log.warning("  PUT %s/%s: %s %s", doc_type, doc_uid[:8], r1.status_code, r1.text[:120])
    return False


def bulk_update(s: requests.Session, doc_type: str, batch: list[dict], dry: bool) -> tuple[int, int]:
    if dry or not batch:
        return len(batch), 0

    r = api(s, "POST", f"{BASE}/entity/{doc_type}", json=batch)
    if r.ok:
        return len(batch), 0

    log.warning("%s bulk %s: %s — fallback по одному", doc_type, len(batch), r.status_code)
    ok, err = 0, 0
    for item in batch:
        if put_fallback(s, doc_type, item):
            ok += 1
        else:
            err += 1
    return ok, err


def process_type(
    s: requests.Session,
    doc_type: str,
    group_meta: dict,
    dry: bool,
    skipped_lines: list[str],
    unresolved_lines: list[str],
    preferred_by_name: dict[str, dict],
    login_by_href: dict[str, bool],
    emp_cache: dict[str, dict | None],
) -> dict:
    with_owner = doc_type in TYPES_WITH_RESPONSIBLE
    docs = get_all(s, doc_type)
    log.info("%s: загружено %s", doc_type, len(docs))

    stat = {
        "total": len(docs),
        "updated": 0,
        "owner_set": 0,
        "group_set": 0,
        "skipped_no_responsible": 0,
        "owner_unresolved": 0,
        "already_ok": 0,
        "errors": 0,
    }

    batch: list[dict] = []
    for doc in docs:
        name = doc.get("name", "?")
        moment = (doc.get("moment") or "")[:16]
        responsible = get_responsible_employee(doc) if with_owner else None
        owner_meta = None

        if with_owner and not responsible:
            stat["skipped_no_responsible"] += 1
            skipped_lines.append(f"{doc_type}\t{name}\t{moment}\tнет Ответственного")
        elif with_owner and responsible:
            owner_meta = resolve_owner_meta(
                s, responsible, preferred_by_name, login_by_href, emp_cache
            )
            if not owner_meta:
                stat["owner_unresolved"] += 1
                resp_name = responsible.get("name", "?")
                unresolved_lines.append(
                    f"{doc_type}\t{name}\t{moment}\t{resp_name}\tнет логина у сотрудника"
                )

        update = build_update(doc, group_meta, with_owner, owner_meta)
        if not update:
            stat["already_ok"] += 1
            continue

        if "owner" in update:
            stat["owner_set"] += 1
        if "group" in update:
            stat["group_set"] += 1

        batch.append(update)
        if len(batch) >= BATCH_SIZE:
            ok, err = bulk_update(s, doc_type, batch, dry)
            stat["updated"] += ok
            stat["errors"] += err
            batch = []

    if batch:
        ok, err = bulk_update(s, doc_type, batch, dry)
        stat["updated"] += ok
        stat["errors"] += err

    log.info(
        "%s: updated=%s owner=%s group=%s skipped=%s unresolved=%s already_ok=%s errors=%s",
        doc_type,
        stat["updated"],
        stat["owner_set"],
        stat["group_set"],
        stat["skipped_no_responsible"],
        stat["owner_unresolved"],
        stat["already_ok"],
        stat["errors"],
    )
    return stat


def main() -> None:
    parser = argparse.ArgumentParser(description="Проставить owner и group в новом аккаунте")
    parser.add_argument("--dry", action="store_true", help="Только статистика, без записи")
    parser.add_argument("--only", choices=ALL_TYPES, help="Обработать один тип документа")
    parser.add_argument(
        "--from-type",
        choices=ALL_TYPES,
        help="Начать с указанного типа (и все последующие)",
    )
    args = parser.parse_args()

    s = make_session(NEW_TOKEN)
    group_meta = resolve_group_meta(s)
    preferred_by_name, login_by_href = load_preferred_owners(s)
    log.info("DRY=%s | group=%s (%s)", args.dry, GROUP_NAME, uid(group_meta["href"]))

    if args.only:
        types = [args.only]
    elif args.from_type:
        idx = ALL_TYPES.index(args.from_type)
        types = ALL_TYPES[idx:]
    else:
        types = ALL_TYPES
    skipped_lines: list[str] = []
    unresolved_lines: list[str] = []
    totals = {
        "updated": 0,
        "owner_set": 0,
        "group_set": 0,
        "skipped_no_responsible": 0,
        "owner_unresolved": 0,
        "already_ok": 0,
        "errors": 0,
        "total": 0,
    }
    emp_cache: dict[str, dict | None] = {}
    report: dict[str, dict] = {}

    for doc_type in types:
        stat = process_type(
            s,
            doc_type,
            group_meta,
            args.dry,
            skipped_lines,
            unresolved_lines,
            preferred_by_name,
            login_by_href,
            emp_cache,
        )
        report[doc_type] = stat
        for key in totals:
            if key in stat:
                totals[key] += stat[key]

    skipped_lines.sort()
    unresolved_lines.sort()
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    SKIPPED_FILE.write_text(
        f"# owner skipped — {ts} dry={args.dry}\n# type\tname\tmoment\treason\n"
        + "\n".join(skipped_lines)
        + ("\n" if skipped_lines else ""),
        encoding="utf-8",
    )
    UNRESOLVED_FILE.write_text(
        f"# owner unresolved — {ts} dry={args.dry}\n# type\tname\tmoment\temployee\treason\n"
        + "\n".join(unresolved_lines)
        + ("\n" if unresolved_lines else ""),
        encoding="utf-8",
    )

    log.info("=" * 60)
    log.info(
        "ИТОГО: docs=%s updated=%s owner=%s group=%s skipped=%s unresolved=%s already_ok=%s errors=%s",
        totals["total"],
        totals["updated"],
        totals["owner_set"],
        totals["group_set"],
        totals["skipped_no_responsible"],
        totals["owner_unresolved"],
        totals["already_ok"],
        totals["errors"],
    )
    log.info("Пропущено (нет Ответственного): %s → %s", totals["skipped_no_responsible"], SKIPPED_FILE)
    log.info("Owner не проставлен (API): %s → %s", totals["owner_unresolved"], UNRESOLVED_FILE)
    log.info("=" * 60)


if __name__ == "__main__":
    main()
