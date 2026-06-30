"""
Заполнить «Ответственный» в NEW из OLD (если в NEW пусто).
Источник в OLD: доп. поле «Ответственный», иначе owner.

После заполнения атрибута — owner (если у сотрудника есть логин).

  python fill_responsible_from_old.py --dry
  python fill_responsible_from_old.py
  python fill_responsible_from_old.py --only invoiceout
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import requests

from ms_common import OLD_TOKEN, NEW_TOKEN

BASE = "https://api.moysklad.ru/api/remap/1.2"
SCRIPT_DIR = Path(__file__).parent
MAP_FILE = SCRIPT_DIR / "uuid_map.json"
LOG_FILE = SCRIPT_DIR / "fill_responsible_from_old.log"
STILL_EMPTY_FILE = SCRIPT_DIR / "responsible_still_empty.txt"

DOC_TYPES = ["customerorder", "invoiceout", "demand", "paymentin"]
RESP_NAME = "Ответственный"
REQUEST_SLEEP = 0.07

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
                time.sleep(min(30, 2**attempt))
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
        r = api(s, "GET", f"{BASE}/entity/{doc_type}", params={"limit": 1000, "offset": offset})
        if not r.ok:
            log.warning("GET %s: %s", doc_type, r.status_code)
            break
        chunk = r.json().get("rows", [])
        rows.extend(chunk)
        if len(chunk) < 1000:
            break
        offset += 1000
    return rows


def get_attr_meta(s: requests.Session, doc_type: str) -> str | None:
    r = api(s, "GET", f"{BASE}/entity/{doc_type}/metadata/attributes", params={"limit": 100})
    if not r.ok:
        return None
    for a in r.json().get("rows", []):
        if a.get("name") == RESP_NAME:
            return a["meta"]["href"]
    return None


def load_employee_maps(
    old_s: requests.Session, new_s: requests.Session, umap: dict
) -> tuple[dict[str, dict], dict[str, dict], dict[str, bool], dict[str, str], dict[str, dict]]:
    """old_uid -> new meta; name -> new meta; href -> has_login; href -> name; login_prefix -> meta."""
    emp_map: dict[str, dict] = {}
    by_name: dict[str, dict] = {}
    login_by_href: dict[str, bool] = {}
    name_by_href: dict[str, str] = {}
    uid_prefix_to_meta: dict[str, dict] = {}

    old_emps: list[dict] = []
    new_emps: list[dict] = []
    for s, bucket in ((old_s, old_emps), (new_s, new_emps)):
        r = api(s, "GET", f"{BASE}/entity/employee", params={"limit": 100})
        r.raise_for_status()
        bucket.extend(r.json().get("rows", []))

    for e in new_emps:
        href = e["meta"]["href"]
        login_by_href[href] = bool(e.get("uid"))
        name_by_href[href] = e.get("name", "")
        name = e.get("name", "")
        if name not in by_name or e.get("uid"):
            by_name[name] = e["meta"]
        if e.get("uid"):
            uid_prefix_to_meta[e["uid"].split("@")[0]] = e["meta"]

    uuid_emp = umap.get("employee", {})
    for old_uid, new_uid in uuid_emp.items():
        href = f"{BASE}/entity/employee/{new_uid}"
        emp_map[old_uid] = {
            "href": href,
            "type": "employee",
            "mediaType": "application/json",
        }

    for e in old_emps:
        old_id = uid(e["meta"]["href"])
        if old_id in emp_map:
            continue
        name = e.get("name", "")
        if name in by_name:
            emp_map[old_id] = by_name[name]
            continue
        old_login = (e.get("uid") or "").split("@")[0]
        if old_login and old_login in uid_prefix_to_meta:
            emp_map[old_id] = uid_prefix_to_meta[old_login]

    return emp_map, by_name, login_by_href, name_by_href, uid_prefix_to_meta


def old_responsible_uid(doc: dict) -> str | None:
    for a in doc.get("attributes", []):
        if a.get("name") == RESP_NAME and a.get("value"):
            href = (a["value"].get("meta") or {}).get("href", "")
            if href:
                return uid(href)
    owner = doc.get("owner") or {}
    href = owner.get("meta", {}).get("href", "")
    return uid(href) if href else None


def has_resp_attr(doc: dict) -> bool:
    return any(
        a.get("name") == RESP_NAME and a.get("value")
        for a in doc.get("attributes", [])
    )


def owner_meta_for(emp_meta: dict, login_by_href: dict, by_name: dict, emp_name: str) -> dict | None:
    href = emp_meta.get("href", "")
    if login_by_href.get(href, False):
        return emp_meta
    pref = by_name.get(emp_name)
    if pref and login_by_href.get(pref.get("href", ""), False):
        return pref
    return None


def process_type(
    old_s: requests.Session,
    new_s: requests.Session,
    doc_type: str,
    emp_map: dict[str, dict],
    by_name: dict[str, dict],
    login_by_href: dict[str, bool],
    name_by_href: dict[str, str],
    dry: bool,
    still_empty: list[str],
) -> dict:
    attr_href = get_attr_meta(new_s, doc_type)
    if not attr_href:
        log.warning("%s: нет атрибута Ответственный", doc_type)
        return {"skip": 1}

    log.info("%s: загрузка OLD...", doc_type)
    old_by_name: dict[str, str | None] = {}
    for doc in get_all(old_s, doc_type):
        name = doc.get("name", "")
        if name:
            old_by_name[name] = old_responsible_uid(doc)
    log.info("%s: OLD=%s", doc_type, len(old_by_name))

    log.info("%s: загрузка NEW...", doc_type)
    new_docs = get_all(new_s, doc_type)
    stat = {
        "total": len(new_docs),
        "empty_new": 0,
        "filled": 0,
        "no_old": 0,
        "no_emp_old": 0,
        "no_emp_map": 0,
        "errors": 0,
    }

    for doc in new_docs:
        if has_resp_attr(doc):
            continue
        stat["empty_new"] += 1
        name = doc.get("name", "?")
        moment = (doc.get("moment") or "")[:16]
        new_uid = uid(doc["meta"]["href"])

        if name not in old_by_name:
            stat["no_old"] += 1
            still_empty.append(f"{doc_type}\t{name}\t{moment}\tнет в OLD")
            continue

        old_emp_uid = old_by_name[name]
        if not old_emp_uid:
            stat["no_emp_old"] += 1
            still_empty.append(f"{doc_type}\t{name}\t{moment}\tнет ответственного в OLD")
            continue

        new_emp_meta = emp_map.get(old_emp_uid)
        if not new_emp_meta:
            stat["no_emp_map"] += 1
            still_empty.append(f"{doc_type}\t{name}\t{moment}\tсотрудник не смаплен")
            continue

        emp_name = name_by_href.get(new_emp_meta.get("href", ""), "")

        payload: dict = {
            "attributes": [
                {
                    "meta": {
                        "href": attr_href,
                        "type": "attributemetadata",
                        "mediaType": "application/json",
                    },
                    "value": {"meta": new_emp_meta},
                }
            ]
        }
        om = owner_meta_for(new_emp_meta, login_by_href, by_name, emp_name)
        if om:
            payload["owner"] = {"meta": om}

        if dry:
            stat["filled"] += 1
            continue

        r = api(new_s, "PUT", f"{BASE}/entity/{doc_type}/{new_uid}", json=payload)
        if r.ok:
            stat["filled"] += 1
            if stat["filled"] % 200 == 0:
                log.info("  %s: заполнено %s", doc_type, stat["filled"])
        else:
            stat["errors"] += 1
            log.warning("  PUT %s/%s: %s %s", doc_type, name, r.status_code, r.text[:120])

    log.info("%s: %s", doc_type, stat)
    return stat


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry", action="store_true")
    parser.add_argument("--only", choices=DOC_TYPES)
    args = parser.parse_args()

    old_s = make_session(OLD_TOKEN)
    new_s = make_session(NEW_TOKEN)
    umap = json.loads(MAP_FILE.read_text(encoding="utf-8")) if MAP_FILE.exists() else {}
    emp_map, by_name, login_by_href, name_by_href, _ = load_employee_maps(old_s, new_s, umap)

    types = [args.only] if args.only else DOC_TYPES
    still_empty: list[str] = []
    totals = {"filled": 0, "no_old": 0, "no_emp_old": 0, "no_emp_map": 0, "errors": 0}

    log.info("DRY=%s | employee map: %s", args.dry, len(emp_map))
    for doc_type in types:
        stat = process_type(
            old_s, new_s, doc_type, emp_map, by_name, login_by_href, name_by_href, args.dry, still_empty
        )
        for k in totals:
            totals[k] += stat.get(k, 0)

    still_empty.sort()
    STILL_EMPTY_FILE.write_text(
        "# still empty after fill from OLD\n# type\tname\tmoment\treason\n"
        + "\n".join(still_empty)
        + ("\n" if still_empty else ""),
        encoding="utf-8",
    )
    log.info(
        "ИТОГО: filled=%s no_old=%s no_emp_old=%s no_map=%s errors=%s still=%s → %s",
        totals["filled"],
        totals["no_old"],
        totals["no_emp_old"],
        totals["no_emp_map"],
        totals["errors"],
        len(still_empty),
        STILL_EMPTY_FILE,
    )


if __name__ == "__main__":
    main()
