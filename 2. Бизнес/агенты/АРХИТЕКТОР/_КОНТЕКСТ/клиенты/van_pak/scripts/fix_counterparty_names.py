"""
Восстановить имена контрагентов «?» в NEW из OLD (externalCode / uuid_map).

  python3 fix_counterparty_names.py --dry
  python3 fix_counterparty_names.py
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import requests

OLD_TOKEN = "b047463b41ff7d77010fbad1002240fb9d959ebe"
NEW_TOKEN = "619a40e860cb6ad975c3d05cae7157b29caccc0e"
BASE = "https://api.moysklad.ru/api/remap/1.2"
SCRIPT_DIR = Path(__file__).parent
MAP_FILE = SCRIPT_DIR / "uuid_map.json"
REPORT_FILE = SCRIPT_DIR / "counterparty_question_marks.txt"
LOG_FILE = SCRIPT_DIR / "fix_counterparty_names.log"
REQUEST_SLEEP = 0.07

FIELDS = [
    "name", "legalTitle", "companyType", "inn", "kpp", "ogrn", "ogrnip",
    "legalAddress", "actualAddress", "email", "phone", "description", "code",
]

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
    for attempt in range(6):
        try:
            r = s.request(method, url, timeout=60, **kwargs)
        except requests.exceptions.RequestException as exc:
            if attempt < 5:
                time.sleep(2**attempt)
                continue
            raise RuntimeError(f"{method} {url}: {exc}") from exc
        if r.status_code == 429:
            wait = int(r.headers.get("X-Lognex-Retry-After", 3000)) / 1000
            time.sleep(wait + 0.5)
            continue
        if 500 <= r.status_code < 600 and attempt < 4:
            time.sleep(2**attempt)
            continue
        time.sleep(REQUEST_SLEEP)
        return r
    raise RuntimeError(f"Провал {method} {url}")


def uid(href: str) -> str:
    return href.split("/")[-1].split("?")[0] if href else ""


def is_bad_name(name: str | None) -> bool:
    if not name or not str(name).strip():
        return True
    n = str(name).strip()
    return n == "?" or n == "??"


def get_all(s: requests.Session, path: str) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    while True:
        r = api(s, "GET", f"{BASE}/{path}", params={"limit": 1000, "offset": offset})
        if not r.ok:
            break
        chunk = r.json().get("rows", [])
        rows.extend(chunk)
        if len(chunk) < 1000:
            break
        offset += 1000
    return rows


def resolve_old_uuid(new_cp: dict, cp_map: dict[str, str]) -> str | None:
    ec = (new_cp.get("externalCode") or "").strip()
    if ec and len(ec) == 36 and ec.count("-") == 4:
        return ec
    new_id = uid(new_cp["meta"]["href"])
    for old_id, mapped in cp_map.items():
        if mapped == new_id:
            return old_id
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry", action="store_true")
    args = parser.parse_args()

    old_s = make_session(OLD_TOKEN)
    new_s = make_session(NEW_TOKEN)
    umap = json.loads(MAP_FILE.read_text(encoding="utf-8")) if MAP_FILE.exists() else {}
    cp_map: dict[str, str] = umap.get("counterparty", {})

    log.info("Загрузка контрагентов NEW...")
    all_new = get_all(new_s, "entity/counterparty")
    bad = [c for c in all_new if is_bad_name(c.get("name"))]
    log.info("NEW контрагентов: %s, с битым именем: %s", len(all_new), len(bad))

    report_lines = [
        "# counterparty name fix",
        "# new_uuid\told_uuid\told_name\taction",
    ]
    fixed = skipped = errors = no_old = 0

    for new_cp in bad:
        new_uuid = uid(new_cp["meta"]["href"])
        old_uuid = resolve_old_uuid(new_cp, cp_map)
        if not old_uuid:
            skipped += 1
            report_lines.append(f"{new_uuid}\t\t\tno_old_uuid")
            continue

        r = api(old_s, "GET", f"{BASE}/entity/counterparty/{old_uuid}")
        if not r.ok:
            no_old += 1
            report_lines.append(f"{new_uuid}\t{old_uuid}\t\told_not_found")
            continue

        old_cp = r.json()
        old_name = old_cp.get("name", "")
        if is_bad_name(old_name):
            skipped += 1
            report_lines.append(f"{new_uuid}\t{old_uuid}\t{old_name!r}\told_name_also_bad")
            continue

        payload = {f: old_cp[f] for f in FIELDS if old_cp.get(f) not in (None, "")}
        payload["name"] = old_name

        if args.dry:
            fixed += 1
            report_lines.append(f"{new_uuid}\t{old_uuid}\t{old_name}\twould_fix")
            log.info("  [DRY] %s → %r", new_uuid[:8], old_name)
            continue

        r2 = api(new_s, "PUT", f"{BASE}/entity/counterparty/{new_uuid}", json=payload)
        if r2.ok:
            fixed += 1
            report_lines.append(f"{new_uuid}\t{old_uuid}\t{old_name}\tfixed")
            if fixed % 50 == 0:
                log.info("  fixed %s", fixed)
        else:
            errors += 1
            report_lines.append(
                f"{new_uuid}\t{old_uuid}\t{old_name}\terror:{r2.status_code}"
            )
            log.warning("  ✗ PUT %s: %s %s", new_uuid[:8], r2.status_code, r2.text[:120])

    REPORT_FILE.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    log.info(
        "ИТОГО: fixed=%s skipped=%s no_old=%s errors=%s → %s",
        fixed,
        skipped,
        no_old,
        errors,
        REPORT_FILE,
    )


if __name__ == "__main__":
    main()
