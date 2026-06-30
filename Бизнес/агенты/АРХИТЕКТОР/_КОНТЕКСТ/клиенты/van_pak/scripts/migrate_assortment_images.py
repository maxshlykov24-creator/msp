"""
Van Pak: перенос фото product/variant OLD -> NEW.

ЖЁСТКИЕ ОГРАНИЧЕНИЯ:
  - НЕ создаём и НЕ изменяем товары/модификации (никаких POST/PUT/PATCH entity/product|variant).
  - Единственная запись в NEW: POST entity/{type}/{id}/images (добавление фото).
  - При любом сомнении в сопоставлении — skip + запись в mismatch-отчёт.
"""
import argparse
import base64
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Optional, Tuple

import requests

OLD_TOKEN = os.environ.get("OLD_TOKEN", "")
NEW_TOKEN = os.environ.get("NEW_TOKEN", "")
BASE = "https://api.moysklad.ru/api/remap/1.2"
SLEEP = 0.07
MAX_BYTES = 3_200_000  # API принимает ~3.1 МБ (проверено 2026-06-04)

SCRIPT_DIR = Path(__file__).parent
MAP_FILE = SCRIPT_DIR / "uuid_map.json"
REPORT_FILE = SCRIPT_DIR / "migrate_assortment_images_report.json"
MISMATCH_FILE = SCRIPT_DIR / "assortment_image_mismatch.txt"
LOG_FILE = SCRIPT_DIR / "migrate_assortment_images.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-6s %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler()],
)
log = logging.getLogger("img")


def load_tokens():
    global OLD_TOKEN, NEW_TOKEN
    if OLD_TOKEN and NEW_TOKEN:
        return
    mig = (SCRIPT_DIR / "migrate.py").read_text(encoding="utf-8")
    if not OLD_TOKEN:
        m = re.search(r'get\("OLD_TOKEN", "([0-9a-f]{40})"\)', mig)
        OLD_TOKEN = m.group(1) if m else ""
    if not NEW_TOKEN:
        m = re.search(r'get\("NEW_TOKEN", "([0-9a-f]{40})"\)', mig)
        NEW_TOKEN = m.group(1) if m else ""
    if not OLD_TOKEN or not NEW_TOKEN:
        raise RuntimeError("Не удалось получить токены из migrate.py или env")


def _norm(s) -> str:
    return (s or "").strip()


def _uid(href: str) -> str:
    return href.split("/")[-1].split("?")[0] if href else ""


class SafeNewSession(requests.Session):
    """NEW-аккаунт: разрешены только GET и POST .../images."""

    def request(self, method, url, **kwargs):
        m = method.upper()
        if m in ("PUT", "PATCH", "DELETE"):
            raise RuntimeError(f"ЗАПРЕЩЕНО: {m} {url}")
        if m == "POST" and "/images" not in url:
            raise RuntimeError(f"ЗАПРЕЩЕНО: POST не на /images: {url}")
        return super().request(method, url, **kwargs)


def session(token, safe_write=False):
    cls = SafeNewSession if safe_write else requests.Session
    s = cls()
    s.headers.update({"Authorization": f"Bearer {token}", "Accept-Encoding": "gzip"})
    return s


def req(s, method, url, **kw):
    for attempt in range(5):
        r = s.request(method, url, timeout=60, **kw)
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(1.0 + attempt)
            continue
        return r
    return r


def get_images(s, etype, uid):
    r = req(s, "GET", f"{BASE}/entity/{etype}/{uid}/images")
    if r.status_code != 200:
        return []
    return r.json().get("rows", [])


def verify_product(oj: dict, nj: dict) -> Optional[str]:
    """None = ok. uuid_map — источник пары; code в NEW перенумерован, не сверяем."""
    on, nn = _norm(oj.get("name")), _norm(nj.get("name"))
    if not on or not nn:
        return "empty_name"
    if on != nn:
        return f"name: '{on}' != '{nn}'"
    oa, na = _norm(oj.get("article")), _norm(nj.get("article"))
    if oa and na and oa != na:
        return f"article: '{oa}' != '{na}'"
    return None


def verify_variant(oj: dict, nj: dict, old_uid: str, prod_map: dict) -> Optional[str]:
    oc, nc = _norm(oj.get("code")), _norm(nj.get("code"))
    if not oc or not nc:
        return "empty_code"
    if oc != nc:
        return f"code: '{oc}' != '{nc}'"
    ec = _norm(nj.get("externalCode"))
    if ec and ec != old_uid:
        return f"externalCode: '{ec}' != old_uuid '{old_uid}'"
    old_parent = _uid((oj.get("product") or {}).get("meta", {}).get("href", ""))
    new_parent = _uid((nj.get("product") or {}).get("meta", {}).get("href", ""))
    if not old_parent or not new_parent:
        return "missing_parent_product"
    expected_new_parent = prod_map.get(old_parent) or prod_map.get(old_parent + "?expand=attributes")
    if expected_new_parent and expected_new_parent.split("?")[0] != new_parent:
        return f"parent_mismatch: map gives {expected_new_parent[:8]}... != {new_parent[:8]}..."
    return None


def transfer_images(old_s, new_s, etype, old_uid, new_uid, dry):
    old_imgs = get_images(old_s, etype, old_uid)
    time.sleep(SLEEP)
    if not old_imgs:
        return 0, "no_images_old"
    new_imgs = get_images(new_s, etype, new_uid)
    time.sleep(SLEEP)
    if new_imgs:
        return 0, "already_has_images"

    moved = 0
    for img in old_imgs[:10]:
        dl = img.get("meta", {}).get("downloadHref")
        fn = img.get("filename") or img.get("title") or f"{old_uid}.jpg"
        if not dl:
            continue
        rb = req(old_s, "GET", dl)
        time.sleep(SLEEP)
        if rb.status_code != 200:
            continue
        content = rb.content
        if len(content) > MAX_BYTES:
            log.warning(f"  skip large {fn} ({len(content)} B) {etype}/{old_uid}")
            continue
        if dry:
            moved += 1
            continue
        body = {"filename": fn, "content": base64.b64encode(content).decode("ascii")}
        url = f"{BASE}/entity/{etype}/{new_uid}/images"
        rp = req(new_s, "POST", url, json=body)
        time.sleep(SLEEP)
        if rp.status_code in (200, 201):
            moved += 1
        else:
            log.warning(f"  POST fail {url}: {rp.status_code} {rp.text[:150]}")
    if moved == 0:
        return 0, "transfer_failed"
    return moved, "transferred"


def run_product(old_s, new_s, umap, dry, limit, mismatch_lines):
    pairs = umap.get("product", {})
    stats = {
        "total_map": len(pairs), "scanned": 0, "ok": 0, "files": 0,
        "no_images_old": 0, "already_has_images": 0, "mismatch": 0,
        "no_new": 0, "errors": 0, "transfer_failed": 0,
    }
    transferred = []

    for old_uid, new_uid in pairs.items():
        if limit and stats["ok"] >= limit:
            break
        old_uid = old_uid.split("?")[0]
        new_uid = new_uid.split("?")[0]
        stats["scanned"] += 1

        ro = req(old_s, "GET", f"{BASE}/entity/product/{old_uid}")
        time.sleep(SLEEP)
        if ro.status_code != 200:
            stats["errors"] += 1
            continue
        oj = ro.json()
        rn = req(new_s, "GET", f"{BASE}/entity/product/{new_uid}")
        time.sleep(SLEEP)
        if rn.status_code != 200:
            stats["no_new"] += 1
            continue
        nj = rn.json()

        reason = verify_product(oj, nj)
        if reason:
            stats["mismatch"] += 1
            mismatch_lines.append(
                f"product\t{old_uid}\t{new_uid}\t{reason}\told_name={oj.get('name','')[:60]}"
            )
            log.warning(f"  SKIP mismatch product {old_uid}: {reason}")
            continue

        moved, action = transfer_images(old_s, new_s, "product", old_uid, new_uid, dry)
        stats[action] = stats.get(action, 0) + 1
        if action == "transferred":
            stats["ok"] += 1
            stats["files"] += moved
            item = {
                "old_uid": old_uid, "new_uid": new_uid,
                "name": oj.get("name"), "article": oj.get("article"),
                "code": oj.get("code"), "files": moved,
            }
            transferred.append(item)
            log.info(
                f"  {'[DRY] ' if dry else ''}OK product | {oj.get('name','')[:50]} | "
                f"article={oj.get('article','')} | {moved} file(s)"
            )

    stats["transferred_items"] = transferred
    return stats


def run_variant(old_s, new_s, umap, dry, limit, mismatch_lines):
    pairs = umap.get("variant", {})
    prod_map = umap.get("product", {})
    stats = {
        "total_map": len(pairs), "scanned": 0, "ok": 0, "files": 0,
        "no_images_old": 0, "already_has_images": 0, "mismatch": 0,
        "no_new": 0, "errors": 0, "transfer_failed": 0,
    }
    transferred = []

    for old_uid, new_uid in pairs.items():
        if limit and stats["ok"] >= limit:
            break
        old_uid = old_uid.split("?")[0]
        new_uid = new_uid.split("?")[0]
        stats["scanned"] += 1

        ro = req(old_s, "GET", f"{BASE}/entity/variant/{old_uid}")
        time.sleep(SLEEP)
        if ro.status_code != 200:
            stats["errors"] += 1
            continue
        oj = ro.json()
        rn = req(new_s, "GET", f"{BASE}/entity/variant/{new_uid}")
        time.sleep(SLEEP)
        if rn.status_code != 200:
            stats["no_new"] += 1
            continue
        nj = rn.json()

        reason = verify_variant(oj, nj, old_uid, prod_map)
        if reason:
            stats["mismatch"] += 1
            mismatch_lines.append(
                f"variant\t{old_uid}\t{new_uid}\t{reason}\tcode={oj.get('code','')}"
            )
            log.warning(f"  SKIP mismatch variant {oj.get('code')}: {reason}")
            continue

        moved, action = transfer_images(old_s, new_s, "variant", old_uid, new_uid, dry)
        stats[action] = stats.get(action, 0) + 1
        if action == "transferred":
            stats["ok"] += 1
            stats["files"] += moved
            transferred.append({
                "old_uid": old_uid, "new_uid": new_uid,
                "code": oj.get("code"), "name": oj.get("name"), "files": moved,
            })
            log.info(f"  {'[DRY] ' if dry else ''}OK variant | code={oj.get('code')} | {moved} file(s)")

    stats["transferred_items"] = transferred
    return stats


def main():
    ap = argparse.ArgumentParser(
        description="Van Pak: только перенос фото (без изменения товаров)"
    )
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="макс. успешных переносов с фото")
    ap.add_argument("--only", choices=["product", "variant"], default=None)
    args = ap.parse_args()

    load_tokens()
    old_s = session(OLD_TOKEN)
    new_s = session(NEW_TOKEN, safe_write=not args.dry)
    umap = json.loads(MAP_FILE.read_text(encoding="utf-8"))

    mismatch_lines = []
    report = {
        "dry": args.dry,
        "limit": args.limit,
        "policy": "images_only_no_entity_modify",
        "types": {},
    }

    if not args.only or args.only == "product":
        log.info("=== product (verify: name + article if both set; uuid_map = пара) ===")
        report["types"]["product"] = run_product(
            old_s, new_s, umap, args.dry, args.limit if args.only == "product" else 0,
            mismatch_lines,
        )
    if not args.only or args.only == "variant":
        lim = args.limit if args.only == "variant" else 0
        log.info("=== variant (verify: code + externalCode + parent product) ===")
        report["types"]["variant"] = run_variant(
            old_s, new_s, umap, args.dry, lim, mismatch_lines,
        )

    for etype, st in report["types"].items():
        brief = {k: v for k, v in st.items() if k != "transferred_items"}
        log.info(f"  {etype}: {json.dumps(brief, ensure_ascii=False)}")

    REPORT_FILE.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    MISMATCH_FILE.write_text("\n".join(mismatch_lines), encoding="utf-8")
    log.info(f"Report -> {REPORT_FILE}")
    if mismatch_lines:
        log.warning(f"Mismatch {len(mismatch_lines)} -> {MISMATCH_FILE}")


if __name__ == "__main__":
    main()
