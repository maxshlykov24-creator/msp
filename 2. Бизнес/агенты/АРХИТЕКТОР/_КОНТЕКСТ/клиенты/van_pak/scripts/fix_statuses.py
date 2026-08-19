"""
Проставляет статусы (state) документов в новом аккаунте, сверяясь со старым.
Создаёт недостающие статусы в метаданных нового аккаунта (по имени).

Только расхождения: NEW ≠ OLD по имени статуса (не массовый сброс в «Новый»).

Типы: customerorder, invoiceout, move, processingorder.
Сопоставление документов через uuid_map.json.

Запуск:
    python3 fix_statuses.py --type customerorder
    python3 fix_statuses.py --type customerorder --from-date "2026-05-03 00:00:00" --to-date "2026-06-03 23:59:59"
    python3 fix_statuses.py --type customerorder --from-date "2026-05-03 00:00:00" --to-date "2026-06-03 23:59:59" --dry
"""
import argparse
import json
import time
from pathlib import Path
from typing import Optional

import requests

OLD_TOKEN = "b047463b41ff7d77010fbad1002240fb9d959ebe"
NEW_TOKEN = "619a40e860cb6ad975c3d05cae7157b29caccc0e"
BASE = "https://api.moysklad.ru/api/remap/1.2"
YEAR_START = "2026-01-01 00:00:00"

TYPES = ["customerorder", "invoiceout", "move", "processingorder"]


def make_session(token):
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {token}", "Accept-Encoding": "gzip"})
    return s


def req(s, method, url, params=None, body=None, retries=6):
    for attempt in range(retries):
        try:
            r = s.request(method, url, params=params, json=body, timeout=40)
            if r.status_code == 429:
                time.sleep(2 ** attempt + 1)
                continue
            if r.status_code == 404:
                return None
            if not r.ok:
                return {"__error__": f"{r.status_code}: {r.text[:200]}"}
            return r.json()
        except Exception:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
            else:
                raise
    return None


def uid(href):
    return href.split("/")[-1].split("?")[0] if href else ""


def moment_filter(from_date: Optional[str], to_date: Optional[str]) -> str:
    if from_date and to_date:
        return f"moment>={from_date};moment<={to_date}"
    if from_date:
        return f"moment>={from_date}"
    return f"moment>={YEAR_START}"


def get_states(s, entity_type):
    d = req(s, "GET", f"{BASE}/entity/{entity_type}/metadata")
    if not d:
        return []
    out = []
    for st in d.get("states", []):
        out.append({
            "name": st.get("name"),
            "color": st.get("color"),
            "stateType": st.get("stateType", "Regular"),
            "uuid": uid(st["meta"]["href"]),
        })
    return out


def ensure_states(new_s, entity_type, old_states):
    new_states = get_states(new_s, entity_type)
    by_name = {st["name"]: st["uuid"] for st in new_states}
    for st in old_states:
        if st["name"] in by_name:
            continue
        body = {
            "name": st["name"],
            "stateType": st["stateType"] or "Regular",
            "color": st["color"] if st["color"] is not None else 15106326,
        }
        r = req(new_s, "POST", f"{BASE}/entity/{entity_type}/metadata/states", body=body)
        if r and "__error__" not in r:
            by_name[st["name"]] = uid(r["meta"]["href"])
            print(f"    + статус '{st['name']}'")
        else:
            print(f"    ✗ статус '{st['name']}': {r.get('__error__') if r else 'нет ответа'}")
    return by_name


def fetch_doc_states(s, entity_type, filt):
    params = {"limit": 100, "offset": 0, "expand": "state", "filter": filt}
    out = {}
    while True:
        d = req(s, "GET", f"{BASE}/entity/{entity_type}", params=params)
        if not d:
            break
        rows = d.get("rows", [])
        for row in rows:
            st = row.get("state") or {}
            out[uid(row["meta"]["href"])] = st.get("name")
        if len(rows) < params["limit"]:
            break
        params = {**params, "offset": params["offset"] + params["limit"]}
        time.sleep(0.08)
    return out


def get_new_state_name(new_s, entity_type, new_uuid, cache):
    if new_uuid in cache:
        return cache[new_uuid]
    d = req(new_s, "GET", f"{BASE}/entity/{entity_type}/{new_uuid}", params={"expand": "state"})
    if not d or "__error__" in d:
        cache[new_uuid] = None
        return None
    name = (d.get("state") or {}).get("name")
    cache[new_uuid] = name
    return name


def fix_type(old_s, new_s, entity_type, umap, filt, dry=False):
    print(f"\n>>> {entity_type}  filter={filt}")
    old_states_meta = get_states(old_s, entity_type)
    if not old_states_meta:
        print("    в старом нет статусов — пропуск")
        return
    name_to_new_uuid = ensure_states(new_s, entity_type, old_states_meta)

    type_map = umap.get(entity_type, {})
    old_doc_state = fetch_doc_states(old_s, entity_type, filt)
    new_doc_state = fetch_doc_states(new_s, entity_type, filt)

    set_cnt = same_cnt = skip_cnt = err_cnt = 0
    for old_uuid, old_state_name in old_doc_state.items():
        if not old_state_name:
            continue
        new_uuid = type_map.get(old_uuid)
        if not new_uuid:
            skip_cnt += 1
            continue
        cur_state = new_doc_state.get(new_uuid)
        if cur_state is None:
            cur_state = get_new_state_name(new_s, entity_type, new_uuid, new_doc_state)
        if cur_state is None:
            skip_cnt += 1
            continue
        if cur_state == old_state_name:
            same_cnt += 1
            continue
        target = name_to_new_uuid.get(old_state_name)
        if not target:
            err_cnt += 1
            continue
        if dry:
            if set_cnt < 25:
                print(f"    [DRY] {new_uuid[:8]}: {cur_state!r} → {old_state_name!r}")
            set_cnt += 1
            continue
        r = req(new_s, "PUT", f"{BASE}/entity/{entity_type}/{new_uuid}",
                body={"state": {"meta": {
                    "href": f"{BASE}/entity/{entity_type}/metadata/states/{target}",
                    "type": "state", "mediaType": "application/json"}}})
        if r and "__error__" not in r:
            set_cnt += 1
            if set_cnt % 50 == 0:
                print(f"    проставлено {set_cnt}...", flush=True)
        else:
            err_cnt += 1
            if err_cnt <= 5:
                print(f"    ✗ {old_uuid[:8]}: {r.get('__error__') if r else 'нет ответа'}")
    label = "к_исправлению" if dry else "проставлено"
    print(f"    итог: {label}={set_cnt}, уже_совпадало={same_cnt}, "
          f"пропуск={skip_cnt}, ошибок={err_cnt}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--type", default=None)
    parser.add_argument("--from-date", default=None, help="YYYY-MM-DD HH:MM:SS")
    parser.add_argument("--to-date", default=None, help="YYYY-MM-DD HH:MM:SS")
    parser.add_argument("--dry", action="store_true")
    args = parser.parse_args()

    old_s = make_session(OLD_TOKEN)
    new_s = make_session(NEW_TOKEN)
    umap = json.loads((Path(__file__).parent / "uuid_map.json").read_text(encoding="utf-8"))

    filt = moment_filter(args.from_date, args.to_date)
    types = [args.type] if args.type else TYPES
    for t in types:
        fix_type(old_s, new_s, t, umap, filt, dry=args.dry)
    print("\nГотово.")


if __name__ == "__main__":
    main()
