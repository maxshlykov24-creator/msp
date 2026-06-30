"""
Синхронизация «Ответственный» (атрибут + owner/Владелец) NEW ← OLD.

Пара OLD↔NEW: uuid_map; fallback moment+agent.
Исправляет расхождения, в т.ч. когда атрибут верный, а owner нет.

  python3 sync_responsible.py --dry --from-date 2026-06-01 --to-date 2026-06-30
  python3 sync_responsible.py --from-date 2026-06-01 --to-date 2026-06-30 --only customerorder
  python3 sync_responsible.py --from-date 2026-01-01 --to-date 2026-12-31
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Optional

import requests

from ms_common import OLD_TOKEN, NEW_TOKEN, old_api_available

BASE = "https://api.moysklad.ru/api/remap/1.2"
SCRIPT_DIR = Path(__file__).parent
MAP_FILE = SCRIPT_DIR / "uuid_map.json"
REPORT_FILE = SCRIPT_DIR / "sync_responsible_report.json"
MISMATCH_FILE = SCRIPT_DIR / "sync_responsible_mismatch.txt"
LOG_FILE = SCRIPT_DIR / "sync_responsible.log"

DOC_TYPES = ["customerorder", "invoiceout", "demand", "paymentin"]
RESP_NAME = "Ответственный"

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
        if r.status_code == 403:
            time.sleep(0.07)
            return r
        if r.status_code == 429:
            wait = int(r.headers.get("X-Lognex-Retry-After", 3000)) / 1000
            time.sleep(wait + 0.5)
            continue
        if 500 <= r.status_code < 600 and attempt < 5:
            time.sleep(2**attempt)
            continue
        time.sleep(0.07)
        return r
    raise RuntimeError(f"Провал {method} {url}")


def uid(href: str) -> str:
    return href.split("/")[-1].split("?")[0] if href else ""


def moment_filter(from_date: str, to_date: str) -> str:
    return f"moment>={from_date} 00:00:00;moment<={to_date} 23:59:59"


def get_all(s: requests.Session, doc_type: str, filt: str) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    while True:
        r = api(
            s,
            "GET",
            f"{BASE}/entity/{doc_type}",
            params={"filter": filt, "limit": 100, "offset": offset, "expand": "owner,agent,attributes"},
        )
        if not r.ok:
            log.warning("GET %s: %s %s", doc_type, r.status_code, r.text[:120])
            break
        chunk = r.json().get("rows", [])
        rows.extend(chunk)
        if len(chunk) < 100:
            break
        offset += 100
    return rows


def get_attr_meta(s: requests.Session, doc_type: str) -> Optional[str]:
    r = api(s, "GET", f"{BASE}/entity/{doc_type}/metadata/attributes", params={"limit": 100})
    if not r.ok:
        return None
    for a in r.json().get("rows", []):
        if a.get("name") == RESP_NAME:
            return a["meta"]["href"]
    return None


def load_employee_maps(
    old_s: requests.Session, new_s: requests.Session, umap: dict
) -> tuple[dict[str, dict], dict[str, dict], dict[str, bool], dict[str, str]]:
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

    for old_uid, new_uid in umap.get("employee", {}).items():
        emp_map[old_uid] = {
            "href": f"{BASE}/entity/employee/{new_uid}",
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

    return emp_map, by_name, login_by_href, name_by_href


def responsible_from_doc(doc: dict) -> tuple[Optional[str], Optional[str]]:
    """(employee_uuid, display_name) из атрибута или owner."""
    for a in doc.get("attributes") or []:
        if a.get("name") == RESP_NAME and a.get("value"):
            val = a["value"]
            href = (val.get("meta") or {}).get("href", "")
            if href:
                return uid(href), val.get("name") or ""
    owner = doc.get("owner") or {}
    href = owner.get("meta", {}).get("href", "")
    if href:
        return uid(href), owner.get("name") or ""
    return None, None


def owner_meta_for(
    emp_meta: dict, login_by_href: dict, by_name: dict, emp_name: str
) -> Optional[dict]:
    href = emp_meta.get("href", "")
    if login_by_href.get(href, False):
        return emp_meta
    pref = by_name.get(emp_name)
    if pref and login_by_href.get(pref.get("href", ""), False):
        return pref
    # fallback: атрибут «Ответственный» всё равно ставим; owner — meta сотрудника
    return emp_meta


def agent_key(doc: dict) -> str:
    agent = doc.get("agent") or {}
    return uid(agent.get("meta", {}).get("href", "")) or agent.get("name", "") or ""


def build_fallback_index(old_docs: list[dict]) -> dict[tuple[str, str], str]:
    """(moment[:19], agent_key) -> old_doc_uuid"""
    idx: dict[tuple[str, str], str] = {}
    for d in old_docs:
        m = (d.get("moment") or "")[:19]
        idx[(m, agent_key(d))] = uid(d["meta"]["href"])
    return idx


def sync_type(
    old_s: requests.Session,
    new_s: requests.Session,
    doc_type: str,
    filt: str,
    type_map: dict[str, str],
    cp_map: dict[str, str],
    emp_map: dict[str, dict],
    by_name: dict[str, dict],
    login_by_href: dict[str, bool],
    name_by_href: dict[str, str],
    dry: bool,
) -> dict:
    attr_href = get_attr_meta(new_s, doc_type)
    if not attr_href:
        log.warning("%s: нет атрибута Ответственный", doc_type)
        return {"skip": 1}

    old_docs = get_all(old_s, doc_type, filt)
    fallback = build_fallback_index(old_docs)
    log.info("%s: OLD=%s", doc_type, len(old_docs))

    stat = {
        "total_old": len(old_docs),
        "ok": 0,
        "fixed": 0,
        "no_new": 0,
        "no_emp_map": 0,
        "errors": 0,
        "mismatches": [],
    }

    for old_doc in old_docs:
        old_uuid = uid(old_doc["meta"]["href"])
        old_name = old_doc.get("name", "?")
        new_uuid = type_map.get(old_uuid)
        if not new_uuid:
            key = ((old_doc.get("moment") or "")[:19], agent_key(old_doc))
            fb_old = fallback.get(key)
            if fb_old == old_uuid:
                pass
            new_uuid = type_map.get(old_uuid)

        if not new_uuid:
            stat["no_new"] += 1
            continue

        r = api(
            new_s,
            "GET",
            f"{BASE}/entity/{doc_type}/{new_uuid}",
            params={"expand": "owner,attributes"},
        )
        if not r.ok:
            stat["no_new"] += 1
            continue
        new_doc = r.json()
        new_name = new_doc.get("name", "?")

        old_emp_uid, old_emp_name = responsible_from_doc(old_doc)
        if not old_emp_uid:
            stat["ok"] += 1
            continue

        new_attr_uid, new_attr_name = responsible_from_doc(new_doc)
        new_owner_uid, new_owner_name = None, None
        owner_href = (new_doc.get("owner") or {}).get("meta", {}).get("href", "")
        if owner_href:
            new_owner_uid = uid(owner_href)
            new_owner_name = (new_doc.get("owner") or {}).get("name", "")

        target_meta = emp_map.get(old_emp_uid)
        if not target_meta:
            stat["no_emp_map"] += 1
            stat["mismatches"].append(
                f"{doc_type}\t{old_name}\t{new_name}\tno_emp_map\told={old_emp_name}"
            )
            continue

        target_name = name_by_href.get(target_meta.get("href", ""), old_emp_name)
        attr_ok = new_attr_uid and (
            new_attr_uid == uid(target_meta.get("href", ""))
            or (new_attr_name and old_emp_name and new_attr_name.split()[0] == old_emp_name.split()[0])
        )
        owner_ok = new_owner_uid and (
            new_owner_uid == uid(target_meta.get("href", ""))
            or (new_owner_name and old_emp_name and new_owner_name.split()[0] == old_emp_name.split()[0])
        )

        if attr_ok and owner_ok:
            stat["ok"] += 1
            continue

        reasons = []
        if not attr_ok:
            reasons.append(f"attr:{new_attr_name or '—'}→{target_name}")
        if not owner_ok:
            reasons.append(f"owner:{new_owner_name or '—'}→{target_name}")

        stat["mismatches"].append(
            f"{doc_type}\t{old_name}\t{new_name}\t" + ";".join(reasons)
        )

        resp_entry = {
            "meta": {
                "href": attr_href,
                "type": "attributemetadata",
                "mediaType": "application/json",
            },
            "value": {"meta": target_meta},
        }
        kept = [
            a for a in (new_doc.get("attributes") or [])
            if a.get("name") != RESP_NAME
            and (a.get("meta") or {}).get("href") != attr_href
        ]
        payload: dict = {"attributes": kept + [resp_entry]}
        om = owner_meta_for(target_meta, login_by_href, by_name, target_name)
        if om:
            payload["owner"] = {"meta": om}

        if dry:
            log.info(
                "  [DRY] %s/%s → %s | %s",
                doc_type,
                new_name,
                target_name,
                "; ".join(reasons),
            )
            stat["fixed"] += 1
            continue

        pr = api(new_s, "PUT", f"{BASE}/entity/{doc_type}/{new_uuid}", json=payload)
        if pr.ok:
            stat["fixed"] += 1
            log.info("  FIX %s/%s → %s", doc_type, new_name, target_name)
        else:
            stat["errors"] += 1
            log.warning("  PUT %s/%s: %s %s", doc_type, new_name, pr.status_code, pr.text[:150])

    log.info(
        "%s: ok=%s fixed=%s no_new=%s no_emp_map=%s errors=%s",
        doc_type,
        stat["ok"],
        stat["fixed"],
        stat["no_new"],
        stat["no_emp_map"],
        stat["errors"],
    )
    return stat


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry", action="store_true")
    parser.add_argument("--from-date", default="2026-06-01")
    parser.add_argument("--to-date", default="2026-06-30")
    parser.add_argument("--only", choices=DOC_TYPES)
    args = parser.parse_args()

    filt = moment_filter(args.from_date, args.to_date)
    old_s = make_session(OLD_TOKEN)
    if not old_api_available(old_s):
        log.error("BLOCKER: OLD API 403 — sync responsible невозможен")
        return
    new_s = make_session(NEW_TOKEN)
    umap = json.loads(MAP_FILE.read_text(encoding="utf-8"))
    emp_map, by_name, login_by_href, name_by_href = load_employee_maps(old_s, new_s, umap)
    cp_map = umap.get("counterparty", {})

    log.info("DRY=%s | filter=%s | employees=%s", args.dry, filt, len(emp_map))

    types = [args.only] if args.only else DOC_TYPES
    report = {"filter": filt, "dry": args.dry, "types": {}}
    all_mismatches: list[str] = []

    for doc_type in types:
        report["types"][doc_type] = sync_type(
            old_s,
            new_s,
            doc_type,
            filt,
            umap.get(doc_type, {}),
            cp_map,
            emp_map,
            by_name,
            login_by_href,
            name_by_href,
            args.dry,
        )
        all_mismatches.extend(report["types"][doc_type].get("mismatches", []))

    MISMATCH_FILE.write_text(
        "# type\told_name\tnew_name\treason\n" + "\n".join(all_mismatches) + "\n",
        encoding="utf-8",
    )
    REPORT_FILE.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Отчёты: %s , %s", REPORT_FILE, MISMATCH_FILE)


if __name__ == "__main__":
    main()
