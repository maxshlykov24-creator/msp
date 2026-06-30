"""
ШАГ 1: Заменить позиции ~563 документов (6 типов) на актуальные из old аккаунта.
Попутно починить повреждённый externalCode (содержит ?expand=...).

Типы (порядок важен): supply, move, invoiceout, customerorder, demand, loss

Запуск:
    python3 fix_doc_positions.py --only supply   # проба на 1 типе
    python3 fix_doc_positions.py                 # полный прогон всех типов
"""

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
YEAR_START = "2026-01-01 00:00:00"
SCRIPT_DIR = Path(__file__).parent
MAP_FILE = SCRIPT_DIR / "uuid_map.json"
REQUEST_SLEEP = 0.07

DOC_TYPES = ["supply", "move", "invoiceout", "customerorder", "demand", "loss"]

# Типы с позициями (все 6 имеют positions)
HAS_POSITIONS = {"supply", "move", "invoiceout", "customerorder", "demand", "loss"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(SCRIPT_DIR / "fix_doc_positions.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


# ── HTTP ──────────────────────────────────────────────────────────────────────

def make_session(token: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {token}", "Accept-Encoding": "gzip"})
    return s


def api_req(s: requests.Session, method: str, url: str, **kwargs):
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


def get_all(s: requests.Session, path: str, params: dict = None) -> list:
    p = dict(params or {})
    limit = 100 if p.get("expand") else 1000
    p["limit"] = limit
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
        total = data.get("meta", {}).get("size", 0)
        if offset == 0:
            log.info(f"  {path}: 0/{total}")
        if len(chunk) < limit:
            break
        offset += limit
        if offset % 500 == 0:
            log.info(f"  {path}: {offset}/{total}")
    return rows


def uid(href: str) -> str:
    """Извлечь UUID из href, отрезав ?... (ОБЯЗАТЕЛЬНО — externalCode повреждён)."""
    return href.split("/")[-1].split("?")[0] if href else ""


def meta_obj(entity_type: str, uuid: str) -> dict:
    return {"meta": {
        "href": f"{BASE}/entity/{entity_type}/{uuid}",
        "type": entity_type,
        "mediaType": "application/json",
    }}


# ── Маппинг позиции ──────────────────────────────────────────────────────────

def map_position(p: dict, umap: dict):
    a_href = (p.get("assortment") or {}).get("meta", {}).get("href", "")
    old_a = uid(a_href)
    new_uuid = umap["product"].get(old_a)
    new_type = "product"
    if not new_uuid:
        new_uuid = umap["variant"].get(old_a)
        new_type = "variant"
    if not new_uuid:
        new_uuid = umap["service"].get(old_a)
        new_type = "service"
    if not new_uuid:
        return None  # не смаппился — логировать снаружи

    np = {
        "assortment": meta_obj(new_type, new_uuid),
        "quantity": p.get("quantity", 1),
        "price": p.get("price", 0),
        "discount": p.get("discount", 0),
        "vat": p.get("vat", 0),
    }
    uom_href = (p.get("uom") or {}).get("meta", {}).get("href", "")
    uom_new = umap["uom"].get(uid(uom_href))
    if uom_new:
        np["uom"] = meta_obj("uom", uom_new)
    return np


# ── Обработка одного типа документа ──────────────────────────────────────────

def fix_doc_type(doc_type: str, old_s, new_s, umap: dict) -> dict:
    log.info(f"\n{'='*60}\n{doc_type}\n{'='*60}")
    stat = {
        "fixed": 0, "already_ok": 0, "no_map": 0,
        "skipped_no_positions": 0, "unmapped_pos": 0, "errors": 0,
    }
    dmap = umap.get(doc_type, {})

    old_rows = get_all(old_s, f"entity/{doc_type}", {"filter": f"moment>={YEAR_START}"})
    new_rows = get_all(new_s, f"entity/{doc_type}", {"filter": f"moment>={YEAR_START}"})
    new_by_id = {uid(d["meta"]["href"]): d for d in new_rows}

    put_count = 0

    for old in old_rows:
        old_uuid = uid(old["meta"]["href"])
        new_uuid = dmap.get(old_uuid)
        if not new_uuid:
            stat["no_map"] += 1
            continue

        n = new_by_id.get(new_uuid)
        if not n:
            stat["no_map"] += 1
            continue

        old_sum = old.get("sum", 0) or 0
        new_sum = n.get("sum", 0) or 0
        ext_code = n.get("externalCode", "") or ""
        ext_ok = "?" not in ext_code

        if abs(old_sum - new_sum) <= 100 and ext_ok:
            stat["already_ok"] += 1
            continue

        # Загрузить полный старый документ с позициями
        r = api_req(old_s, "GET", f"{BASE}/entity/{doc_type}/{old_uuid}",
                    params={"expand": "positions"})
        if not r.ok:
            log.warning(f"  ✗ GET old {doc_type}/{old_uuid}: {r.status_code}")
            stat["errors"] += 1
            continue

        full = r.json()
        pos_data = full.get("positions", {})
        old_pos_rows = pos_data.get("rows", []) if isinstance(pos_data, dict) else (pos_data or [])

        # Для invoiceout допустимо 0 позиций
        if not old_pos_rows and doc_type != "invoiceout":
            log.warning(f"  ✗ {doc_type}/{old.get('name','?')}: 0 позиций в old, пропуск")
            stat["skipped_no_positions"] += 1
            continue

        # Смапить позиции
        mapped = []
        skipped_pos = 0
        for p in old_pos_rows:
            mp = map_position(p, umap)
            if mp is None:
                skipped_pos += 1
                log.warning(f"  ! {doc_type}/{old.get('name','?')}: позиция не смаплена "
                            f"({uid((p.get('assortment') or {}).get('meta',{}).get('href',''))})")
            else:
                mapped.append(mp)

        stat["unmapped_pos"] += skipped_pos

        if not mapped and old_pos_rows:
            log.warning(f"  ✗ {doc_type}/{old.get('name','?')}: 0 позиций смаппилось, пропуск")
            stat["skipped_no_positions"] += 1
            continue

        # PUT: заменить позиции + починить externalCode
        body = {
            "positions": mapped,
            "externalCode": old_uuid,  # чистый UUID (без ?...)
        }

        r2 = api_req(new_s, "PUT", f"{BASE}/entity/{doc_type}/{new_uuid}", json=body)
        put_count += 1
        if put_count % 80 == 0:
            log.info(f"  Пауза (лимит PUT)... исправлено {stat['fixed']}")
            time.sleep(62)

        if r2.ok:
            new_s2 = r2.json().get("sum", 0) or 0
            stat["fixed"] += 1
            if abs((old_sum - new_s2)) > 100:
                log.warning(f"  ! {doc_type}/{old.get('name','?')}: после PUT sum={new_s2/100:.2f} vs old={old_sum/100:.2f}")
        else:
            log.warning(f"  ✗ PUT {doc_type}/{new_uuid}: {r2.status_code} {r2.text[:200]}")
            stat["errors"] += 1

    log.info(f"  {doc_type}: fixed={stat['fixed']} already_ok={stat['already_ok']} "
             f"no_map={stat['no_map']} skipped={stat['skipped_no_positions']} "
             f"unmapped_pos={stat['unmapped_pos']} errors={stat['errors']}")
    return stat


# ── Финальная проверка сумм ───────────────────────────────────────────────────

def verify_sums(doc_type: str, old_s, new_s, umap: dict) -> dict:
    dmap = umap.get(doc_type, {})
    old_rows = get_all(old_s, f"entity/{doc_type}", {"filter": f"moment>={YEAR_START}"})
    new_rows = get_all(new_s, f"entity/{doc_type}", {"filter": f"moment>={YEAR_START}"})
    new_by_id = {uid(d["meta"]["href"]): d for d in new_rows}
    mism = 0
    for old in old_rows:
        nid = dmap.get(uid(old["meta"]["href"]))
        if not nid:
            continue
        n = new_by_id.get(nid)
        if not n:
            continue
        if abs((old.get("sum", 0) or 0) - (n.get("sum", 0) or 0)) > 100:
            mism += 1
    return {"type": doc_type, "old": len(old_rows), "new": len(new_rows), "mismatch": mism}


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", default=None, help="Запустить только для одного типа (напр. supply)")
    args = parser.parse_args()

    old_s = make_session(OLD_TOKEN)
    new_s = make_session(NEW_TOKEN)
    umap = json.loads(MAP_FILE.read_text(encoding="utf-8"))

    types = [args.only] if args.only else DOC_TYPES

    report = {}
    for doc_type in types:
        if doc_type not in DOC_TYPES:
            log.warning(f"Неизвестный тип: {doc_type}, пропуск")
            continue
        stat = fix_doc_type(doc_type, old_s, new_s, umap)
        report[doc_type] = stat

    # Финальная проверка
    log.info("\n=== ФИНАЛЬНАЯ ПРОВЕРКА СУММ ===")
    verify = {}
    for doc_type in types:
        v = verify_sums(doc_type, old_s, new_s, umap)
        verify[doc_type] = v
        status = "OK" if v["mismatch"] == 0 else f"DIFF {v['mismatch']}"
        log.info(f"  {doc_type}: old={v['old']} new={v['new']} расхождений={v['mismatch']} [{status}]")

    out = SCRIPT_DIR / "fix_doc_positions_report.json"
    out.write_text(json.dumps({"fixes": report, "verify": verify}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    log.info(f"\nОтчёт: {out}")


if __name__ == "__main__":
    main()
