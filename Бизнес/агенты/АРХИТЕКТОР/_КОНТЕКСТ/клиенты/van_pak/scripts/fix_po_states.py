"""
Точечный фикс статусов processingorder, которые валятся из-за обязательного processingPlan.
Подтягивает processingPlan из старого документа (через uuid_map), ставит state.
"""
import json
import time
from pathlib import Path

import requests

OLD_TOKEN = "b047463b41ff7d77010fbad1002240fb9d959ebe"
NEW_TOKEN = "619a40e860cb6ad975c3d05cae7157b29caccc0e"
BASE = "https://api.moysklad.ru/api/remap/1.2"
YEAR_START = "2026-01-01 00:00:00"
ET = "processingorder"


def sess(tok):
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {tok}", "Accept-Encoding": "gzip"})
    return s


def req(s, method, url, params=None, body=None, retries=6):
    for a in range(retries):
        try:
            r = s.request(method, url, params=params, json=body, timeout=40)
            if r.status_code == 429:
                time.sleep(2 ** a + 1)
                continue
            if not r.ok:
                return {"__error__": f"{r.status_code}: {r.text[:160]}"}
            return r.json()
        except Exception:
            if a < retries - 1:
                time.sleep(2 ** a)
            else:
                raise
    return None


def uid(h):
    return h.split("/")[-1].split("?")[0] if h else ""


def main():
    old_s, new_s = sess(OLD_TOKEN), sess(NEW_TOKEN)
    umap = json.loads((Path(__file__).parent / "uuid_map.json").read_text(encoding="utf-8"))
    tmap = umap.get(ET, {})
    plan_map = umap.get("processingplan", {})

    # Имя статуса -> uuid в новом
    meta = req(new_s, "GET", f"{BASE}/entity/{ET}/metadata")
    name2uuid = {st["name"]: uid(st["meta"]["href"]) for st in meta.get("states", [])}

    # Старые статусы 2026
    params = {"limit": 100, "offset": 0, "expand": "state,processingPlan",
              "filter": f"moment>={YEAR_START}"}
    old_docs = {}
    while True:
        d = req(old_s, "GET", f"{BASE}/entity/{ET}", params=params)
        rows = d.get("rows", [])
        for row in rows:
            old_docs[uid(row["meta"]["href"])] = {
                "state": (row.get("state") or {}).get("name"),
                "plan": uid((row.get("processingPlan") or {}).get("meta", {}).get("href", "")),
            }
        if len(rows) < params["limit"]:
            break
        params["offset"] += params["limit"]
        time.sleep(0.1)

    fixed = err = skip = 0
    for old_uuid, od in old_docs.items():
        if not od["state"]:
            continue
        new_uuid = tmap.get(old_uuid)
        if not new_uuid:
            continue
        # текущий статус нового
        nd = req(new_s, "GET", f"{BASE}/entity/{ET}/{new_uuid}", params={"expand": "state"})
        if not nd or "__error__" in nd:
            err += 1
            continue
        if (nd.get("state") or {}).get("name") == od["state"]:
            continue  # уже стоит
        target = name2uuid.get(od["state"])
        if not target:
            skip += 1
            continue
        body = {"state": {"meta": {
            "href": f"{BASE}/entity/{ET}/metadata/states/{target}",
            "type": "state", "mediaType": "application/json"}}}
        # processingPlan обязателен — берём из нового дока, иначе мапим из старого
        new_plan_href = (nd.get("processingPlan") or {}).get("meta", {}).get("href")
        if new_plan_href:
            body["processingPlan"] = {"meta": {
                "href": new_plan_href, "type": "processingplan",
                "mediaType": "application/json"}}
        elif od["plan"] and plan_map.get(od["plan"]):
            body["processingPlan"] = {"meta": {
                "href": f"{BASE}/entity/processingplan/{plan_map[od['plan']]}",
                "type": "processingplan", "mediaType": "application/json"}}
        r = req(new_s, "PUT", f"{BASE}/entity/{ET}/{new_uuid}", body=body)
        if r and "__error__" not in r:
            fixed += 1
        else:
            err += 1
            if err <= 5:
                print(f"  ✗ {old_uuid}: {r.get('__error__') if r else 'нет ответа'}")

    print(f"\nИтог: проставлено={fixed}, пропущено(нет плана)={skip}, ошибок={err}")


if __name__ == "__main__":
    main()
