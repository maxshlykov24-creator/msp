#!/usr/bin/env python3
"""2MY: трек в сделке → LiveInform → этап Получен (142).

Только новая воронка «Продажи 2MY». Старую не трогает.
МойСклад не нужен: читаем поле «Трек-номер» в amo.

По умолчанию сухой прогон. Списание за отслеживание только с --apply
(LiveInform add). Доставлен = track_status 2, как в DKAcademy.

Запуск:
    python3 sync_liveinform.py
    python3 sync_liveinform.py --apply
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / "06_Доступы" / ".env"

PIPELINE_SALES_NEW = 11318374
STATUS_SENT = 88727602
STATUS_RECEIVED = 142
FIELD_TRACK = 928963
FIELD_TRACK_MS = 929039
FIELD_CDEK = 929209

TRACK_URL = "https://www.liveinform.ru/api/v2/track/"
ADD_URL = "https://www.liveinform.ru/api/v2/add/"


def load_env() -> dict[str, str]:
    out: dict[str, str] = {}
    if ENV.is_file():
        for raw in ENV.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip("'").strip('"')
    return out


def field_value(lead: dict, field_id: int) -> str:
    for grp in lead.get("custom_fields_values") or []:
        if grp.get("field_id") != field_id:
            continue
        for v in grp.get("values") or []:
            val = str(v.get("value") or "").strip()
            if val:
                return val
    return ""


def track_of(lead: dict) -> str:
    return field_value(lead, FIELD_TRACK) or field_value(lead, FIELD_CDEK) or field_value(lead, FIELD_TRACK_MS)


def amo_opener(env: dict[str, str]):
    proxy = (os.environ.get("HTTPS_PROXY") or env.get("AMOCRM_HTTPS_PROXY") or "").strip()
    if proxy:
        return urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    return urllib.request.build_opener()


def amo_req(opener, base: str, token: str, method: str, path: str, body=None):
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    r = urllib.request.Request(
        base.rstrip("/") + path,
        data=data,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method=method,
    )
    try:
        with opener.open(r, timeout=30) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"error": raw.decode(errors="replace")}


def li_get(url: str, params: dict) -> dict:
    full = url + "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(full, timeout=20) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return json.loads(raw)
        except Exception:
            return {"status": "ERROR", "http": e.code, "raw": raw.decode(errors="replace")[:300]}


def li_errors(body: dict) -> list[str]:
    err = body.get("error") or body.get("errors") or []
    if isinstance(err, dict):
        err = [err]
    out = []
    for item in err:
        if isinstance(item, dict):
            out.append(f"{item.get('code')}: {item.get('text')}")
        else:
            out.append(str(item))
    return out


def ping_liveinform(api_id: str) -> str:
    body = li_get(TRACK_URL, {"api_id": api_id})
    codes = {str(e.get("code")) for e in (body.get("error") or []) if isinstance(e, dict)}
    if "200" in codes:
        return "неверный api_id"
    if "208" in codes:
        return "ок"
    if body.get("status") and body.get("status") != "ERROR":
        return "ок"
    return "неизвестно: " + ", ".join(li_errors(body)[:3] or [json.dumps(body, ensure_ascii=False)[:200]])


def delivered(body: dict) -> bool:
    ts = body.get("track_status")
    if ts is not None and str(ts).strip() == "2":
        return True
    if str(body.get("status") or "").strip() == "2":
        return True
    return False


def list_sent(opener, base: str, token: str) -> list[dict]:
    items: list[dict] = []
    page = 1
    while page <= 20:
        path = (
            f"/api/v4/leads?limit=250&page={page}"
            f"&filter[statuses][0][pipeline_id]={PIPELINE_SALES_NEW}"
            f"&filter[statuses][0][status_id]={STATUS_SENT}"
        )
        st, b = amo_req(opener, base, token, "GET", path)
        if st == 204 or not (200 <= st < 300):
            break
        chunk = (b.get("_embedded") or {}).get("leads") or []
        items.extend(chunk)
        if len(chunk) < 250:
            break
        page += 1
    return items


def main() -> None:
    parser = argparse.ArgumentParser(description="LiveInform → Получен, только Продажи 2MY")
    parser.add_argument("--apply", action="store_true", help="add в LiveInform и перевод в 142")
    args = parser.parse_args()

    env = load_env()
    api_id = (env.get("LIVEINFORM_API_ID") or "").strip()
    token = (env.get("AMOCRM_LONG_LIVED_TOKEN") or os.environ.get("AMOCRM_TOKEN") or "").strip()
    base = (env.get("AMOCRM_BASE_URL") or "").rstrip("/")
    if not api_id:
        sys.exit(f"Нет LIVEINFORM_API_ID в {ENV}")
    if not token or not base:
        sys.exit("Нет amo токена или URL")

    ping = ping_liveinform(api_id)
    print(f"LiveInform: {ping}")
    if ping != "ок":
        sys.exit(1)

    opener = amo_opener(env)
    leads = list_sent(opener, base, token)
    print(f"Отправлен в Продажи 2MY: {len(leads)}")
    if not leads:
        print("Нечего синхронизировать. МойСклад не жду: как появится трек в сделке — этот же скрипт.")
        return

    for lead in leads:
        lid = lead.get("id")
        tracking = track_of(lead)
        if not tracking:
            print(f"  {lid}: нет трека, пропуск")
            continue
        body = li_get(TRACK_URL, {"api_id": api_id, "tracking": tracking})
        codes = {str(e.get("code")) for e in (body.get("error") or []) if isinstance(e, dict)}
        if "208" in codes:
            if not args.apply:
                print(f"  {lid}: трек {tracking} не в LiveInform, --apply поставит на отслеживание")
                continue
            added = li_get(ADD_URL, {"api_id": api_id, "tracking": tracking, "order_id": str(lid)})
            if added.get("status") == "ERROR":
                print(f"  {lid}: add ошибка {li_errors(added)}")
                continue
            print(f"  {lid}: поставлен на отслеживание")
            continue
        if delivered(body):
            if not args.apply:
                print(f"  {lid}: доставлен, --apply переведёт в Получен")
                continue
            st, b = amo_req(opener, base, token, "PATCH", f"/api/v4/leads/{lid}", {
                "status_id": STATUS_RECEIVED,
                "pipeline_id": PIPELINE_SALES_NEW,
            })
            print(f"  {lid}: Получен [{st}]")
            continue
        print(f"  {lid}: в пути status={body.get('status')} track_status={body.get('track_status')}")


if __name__ == "__main__":
    main()
