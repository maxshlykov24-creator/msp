#!/usr/bin/env python3
"""Разовый проход: переписки Wazzup → этапы и поля воронки «Продажи».

Команды:
  python3 actualize_sales.py dump
  python3 actualize_sales.py classify
  python3 actualize_sales.py report
  python3 actualize_sales.py apply --dry-run
  python3 actualize_sales.py apply --high
  python3 actualize_sales.py apply --ids 1,2
  python3 actualize_sales.py probe-write

Ничего не пишет в amo без apply. Ключи — в .env, дампы — в _data/.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "_data"
CTX = ssl.create_default_context()

PIPE_PRODAZHI = 11036674
PIPE_SHCHENKI = 11036834
PIPE_RASSROCHKA = 11036838
PIPE_CLIENTS = 11036874

ST = {
    "new": 86717266,
    "work": 86717270,
    "wait": 86717274,
    "picked": 86717278,
    "booked": 86718354,
    "docs": 86718358,
    "sold": 142,
    "lost": 143,
}
ST_SH = {"free": 86718366, "booked": 86718370, "docs": 86718374, "sold": 142}
ST_RASS = {
    "active": 86718382,
    "late1": 86718386,
    "late3": 86718514,
    "late7": 86718518,
}
ST_CL = {
    "bought": 86718682,
    "m1": 86718686,
    "m6": 86718690,
    "y1": 86718694,
    "y2": 86718718,
}

F = {
    "src": 1820835,
    "comment": 1820837,
    "paytype": 1820839,
    "wait_until": 1820841,
    "rass_sum": 1820845,
    "next_pay": 1820847,
    "debt": 1820849,
    "puppy_id": 1820851,
    "rass_term": 1820869,
    "lost_reason": 1820879,
    "lost_comment": 1820881,
    "contact_way": 1820923,
    "prepay": 1820831,
    "prepay_status": 1820833,
    "bron_date": 1820827,
    "bron_till": 1820829,
}
C_PITOMNIK = 1820903

LOST_REASON_GHOST = "Не вышел на связь"
LOST_REASON_PRICE = "Не устроила цена"

STAGE_NAME = {
    ST["new"]: "Новая заявка",
    ST["work"]: "Взято в работу",
    ST["wait"]: "Ожидает помета",
    ST["picked"]: "Щенок подобран",
    ST["booked"]: "Щенок забронирован",
    ST["docs"]: "Документы оформлены",
    ST["sold"]: "Уехал в семью",
    ST["lost"]: "Провал",
}

SKIP_NAME = re.compile(
    r"\[ТЕСТ\]|тест бота|кассов|отчет|отчёт|keris club\)|сотрудник|админ",
    re.I,
)
AUTO_HINT = re.compile(
    r"нерабочее время|ответим с 10|автоответ|это автоматическ",
    re.I,
)

RE_SOLD = re.compile(
    r"уехал(а|и)? в семью|уже дома|переезд состоял|"
    r"щенок у вас|малыш у вас|отдал[аи] (щенк|малыш)|в новой семье|"
    r"(?<![Нн]е )(?<![Нн]е  )забрал[аи] (щенк|малыш)",
    re.I,
)
RE_DOCS = re.compile(
    r"договор подписан|документы оформ|документы готов|"
    r"финальн(ый|ого) расч[её]т",
    re.I,
)
RE_BOOKED = re.compile(
    r"бронь оплач|оплатил[аи] (бронь|задаток|предоплат)|"
    r"внесл[аи] (бронь|задаток|предоплат)|"
    r"задаток (приш[её]л|получил|есть)|бронь прошла|"
    r"предоплат[аыуе] (прошла|пришла|есть|внесен)|"
    r"я (забронировал|внесла? предоплат)|забронировала? (этого|эту|щенк)",
    re.I,
)
GROOMING_HINT = re.compile(
    r"чек-лист грумер|зоосалон|чек-лист администратор|инвентаризация кассы|"
    r"стандарты смены|yandex\.ru/business|договор сегодня сможете",
    re.I,
)
RE_WAIT = re.compile(
    r"жду пом[её]т|лист ожидания|когда (будут|следующ).{0,20}щен|"
    r"нет в наличии|следующ(ий|его) пом[её]т|ожидаем пом[её]т",
    re.I,
)
RE_PICKED = re.compile(
    r"хочу (этого|эту|его|её|ее)|беру (этого|эту|его)|"
    r"забронировать (этого|эту)|нравится (этот|эта|он|она)",
    re.I,
)
RE_REFUSE = re.compile(
    r"передумал|не будем брать|не будем забир|отказ|"
    r"больше не актуально|не интересно",
    re.I,
)
RE_PRICE = re.compile(r"дорого|не по карману|не устроил[аи]? цен", re.I)
RE_DIALOG = re.compile(
    r"щенк|мальтип|цена|стоим|бронь|пом[её]т|девочк|мальчик|"
    r"микро|мини|окрас|абрикос",
    re.I,
)
RE_INSTALL = re.compile(r"рассрочк", re.I)
RE_SOURCE = {
    "Рекомендации": re.compile(r"по рекомендац|знакомая|подруга посоветовал", re.I),
    "Инстаграм": re.compile(r"инстаграм|instagram", re.I),
    "Телеграм-канал": re.compile(r"из канала|в канале видел|телеграм.?канал", re.I),
    "Яндекс.Реклама": re.compile(r"яндекс.?директ|из рекламы", re.I),
    "Тильда": re.compile(r"с сайта|на сайте заявка", re.I),
}


def load_env() -> None:
    path = HERE / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'").strip('"'))


def settings() -> tuple[str, str, str]:
    load_env()
    base = os.environ.get("AMOCRM_BASE_URL", "https://kerisclub.amocrm.ru").rstrip("/")
    token = os.environ.get("AMOCRM_TOKEN", "").strip()
    wz = os.environ.get("WAZZUP_API_KEY", "").strip()
    if ":" in wz:
        # кабинет отдаёт «id:ключ»; в Authorization идёт только ключ
        wz = wz.split(":", 1)[1].strip()
    if not token:
        sys.exit("Нет AMOCRM_TOKEN в .env")
    if not wz:
        sys.exit("Нет WAZZUP_API_KEY в .env")
    return base, token, wz


def amo(method: str, path: str, body=None, params=None, timeout=30):
    base, token, _ = settings()
    query = f"?{urllib.parse.urlencode(params)}" if params else ""
    url = f"{base}{path}{query}"
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {"error": raw.decode("utf-8", errors="replace")[:400]}
        return exc.code, parsed


def amo_pages(path: str, params: dict | None = None, key: str = "leads"):
    page = 1
    params = dict(params or {})
    while page <= 40:
        q = dict(params)
        q["page"] = str(page)
        q.setdefault("limit", "250")
        code, data = amo("GET", path, params=q)
        if code == 204:
            return
        if not (200 <= code < 300):
            sys.exit(f"amo {code} {path}: {data}")
        rows = (data.get("_embedded") or {}).get(key) or []
        if not rows:
            return
        yield from rows
        if len(rows) < int(q["limit"]):
            return
        page += 1
        time.sleep(0.16)


def wz(method: str, path: str, payload=None, version=3, timeout=30):
    _, _, key = settings()
    base = "https://api.wazzup24.com/v3" if version == 3 else "https://api.wazzup24.com/v2"
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{base}{path}",
        data=data,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {"error": raw[:400]}
        return exc.code, parsed


def digits(value: str) -> str:
    d = re.sub(r"\D", "", value or "")
    if len(d) == 11 and d[0] in "78":
        return d[-10:]
    return d[-10:] if len(d) >= 10 else d


def save_json(name: str, obj) -> Path:
    DATA.mkdir(exist_ok=True)
    path = DATA / name
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_json(name: str):
    path = DATA / name
    if not path.exists():
        sys.exit(f"нет {path} — сначала dump")
    return json.loads(path.read_text(encoding="utf-8"))


def cmd_events() -> None:
    """Все incoming/outgoing chat events — без текста, но с talk_id и временем."""
    rows = []
    for kind in ("incoming_chat_message", "outgoing_chat_message"):
        page = 1
        while page <= 80:
            code, data = amo(
                "GET",
                "/api/v4/events",
                params={
                    "filter[type]": kind,
                    "limit": "100",
                    "page": str(page),
                },
            )
            if code == 204:
                break
            if not (200 <= code < 300):
                print("events", kind, code, data)
                break
            evs = (data.get("_embedded") or {}).get("events") or []
            if not evs:
                break
            rows.extend(evs)
            print(f"  {kind} page {page}: +{len(evs)} total {len(rows)}")
            if len(evs) < 100:
                break
            page += 1
            time.sleep(0.16)
    save_json("amo_chat_events.json", rows)
    print(f"событий чата {len(rows)}")


def cmd_ingest_csv(path: str) -> None:
    raw = Path(path).read_text(encoding="utf-8-sig")
    rows = parse_csv(raw)
    existing = []
    old = DATA / "wazzup_messages.json"
    if old.exists():
        existing = json.loads(old.read_text(encoding="utf-8"))
    seen = {(msg_when(r), msg_text(r), str(r.get("chatId") or r.get("chat_id") or "")) for r in existing}
    added = 0
    for row in rows:
        marker = (msg_when(row), msg_text(row), str(row.get("chatId") or row.get("chat_id") or ""))
        if marker in seen:
            continue
        existing.append(row)
        seen.add(marker)
        added += 1
    save_json("wazzup_messages.json", existing)
    print(f"из CSV {len(rows)} строк, новых {added}, всего {len(existing)}")
    if rows:
        print("колонки:", ", ".join(rows[0].keys()))
    code, data = wz("GET", "/channels")
    print("channels", code)
    rows = data if isinstance(data, list) else (data.get("data") or data.get("channels") or [])
    for row in rows:
        print(
            f"  {row.get('channelId')}  {row.get('transport')}  "
            f"{row.get('plainId') or row.get('name') or ''}"
        )
    save_json("wazzup_channels.json", data)


def fetch_dump_csv(days: int, channel_id: str | None) -> str:
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days)
    body = {
        "start_at": start.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "end_at": now.strftime("%Y-%m-%dT%H:%M:%S.999Z"),
    }
    if channel_id:
        body["channel_id"] = channel_id
    code, created_raw = wz("POST", "/messages/messages_dump", body, version=2)
    created = created_raw.get("data") if isinstance(created_raw, dict) and "data" in created_raw else created_raw
    if not isinstance(created, dict):
        created = {}
    export_id = created.get("export_id")
    if not export_id:
        sys.exit(f"Wazzup dump {code}: {created_raw}")
    deadline = time.time() + 180
    while time.time() < deadline:
        code, status_raw = wz("GET", f"/messages/messages_dump/{export_id}", version=2)
        status = status_raw.get("data") if isinstance(status_raw, dict) and "data" in status_raw else status_raw
        if not isinstance(status, dict):
            status = {}
        state = status.get("status")
        url = status.get("url")
        if state in ("done", "webhook_failed") and url:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, context=CTX, timeout=120) as resp:
                return resp.read().decode("utf-8-sig")
        if state in ("done", "webhook_failed") and not url:
            sys.exit(f"дамп {export_id} без url: {status}")
        time.sleep(2)
    sys.exit("дамп Wazzup не успел за 180 с")


def parse_csv(raw: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(raw))
    return [dict(row) for row in reader]


def cmd_dump(days: int) -> None:
    DATA.mkdir(exist_ok=True)
    code, ch_raw = wz("GET", "/channels")
    save_json("wazzup_channels.json", ch_raw)
    channels = ch_raw if isinstance(ch_raw, list) else (ch_raw.get("data") or [])
    print(f"каналов Wazzup: {len(channels)}")
    all_rows: list[dict] = []
    try:
        if channels:
            for ch in channels:
                cid = ch.get("channelId")
                print(f"  dump {ch.get('transport')} {cid} …")
                raw = fetch_dump_csv(days, cid)
                rows = parse_csv(raw)
                for row in rows:
                    row["_channelId"] = cid
                    row["_transport"] = ch.get("transport")
                print(f"    строк {len(rows)}")
                all_rows.extend(rows)
                (DATA / f"wazzup_{cid}.csv").write_text(raw, encoding="utf-8")
        else:
            raw = fetch_dump_csv(days, None)
            all_rows = parse_csv(raw)
            (DATA / "wazzup_all.csv").write_text(raw, encoding="utf-8")
    except SystemExit as exc:
        print(f"Wazzup dump недоступен: {exc}")
    save_json("wazzup_messages.json", all_rows)
    print(f"всего сообщений {len(all_rows)}")
    if all_rows:
        print("колонки:", ", ".join(all_rows[0].keys()))

    leads = []
    for lead in amo_pages("/api/v4/leads", {"filter[pipeline_id]": str(PIPE_PRODAZHI), "with": "contacts"}):
        leads.append(lead)
        time.sleep(0.05)
    save_json("amo_sales_leads.json", leads)
    print(f"сделок Продажи {len(leads)}")

    talks = list(amo_pages("/api/v4/talks", key="talks"))
    save_json("amo_talks.json", talks)
    print(f"бесед {len(talks)}")

    puppies = list(amo_pages("/api/v4/leads", {"filter[pipeline_id]": str(PIPE_SHCHENKI)}))
    save_json("amo_puppies.json", puppies)
    print(f"щенков {len(puppies)}")

    existing_cl = list(amo_pages("/api/v4/leads", {"filter[pipeline_id]": str(PIPE_CLIENTS)}))
    save_json("amo_clients.json", existing_cl)
    existing_r = list(amo_pages("/api/v4/leads", {"filter[pipeline_id]": str(PIPE_RASSROCHKA)}))
    save_json("amo_rassrochka.json", existing_r)


def contact_phones(lead: dict) -> list[str]:
    out = []
    for c in (lead.get("_embedded") or {}).get("contacts") or []:
        cid = c.get("id")
        if not cid:
            continue
        code, data = amo("GET", f"/api/v4/contacts/{cid}")
        time.sleep(0.12)
        if not (200 <= code < 300):
            continue
        for cf in data.get("custom_fields_values") or []:
            if cf.get("field_code") == "PHONE" or cf.get("field_id") == 1820533:
                for val in cf.get("values") or []:
                    d = digits(str(val.get("value") or ""))
                    if d:
                        out.append(d)
    return out


def cmd_phones() -> None:
    leads = load_json("amo_sales_leads.json")
    cache = {}
    path = DATA / "contact_phones.json"
    if path.exists():
        cache = json.loads(path.read_text(encoding="utf-8"))
    for i, lead in enumerate(leads, 1):
        key = str(lead["id"])
        if key in cache:
            continue
        cache[key] = contact_phones(lead)
        if i % 25 == 0:
            save_json("contact_phones.json", cache)
            print(f"телефоны {i}/{len(leads)}")
    save_json("contact_phones.json", cache)
    print(f"телефоны готовы {len(cache)}")


def msg_text(row: dict) -> str:
    return (row.get("text") or row.get("message") or row.get("body") or "").strip()


def msg_when(row: dict) -> str:
    return str(row.get("dateTime") or row.get("datetime") or row.get("createdAt") or row.get("date") or "")


def msg_out(row: dict) -> bool:
    echo = str(row.get("isEcho") or row.get("outgoing") or row.get("fromMe") or "").lower()
    author = str(row.get("author") or row.get("authorName") or "").lower()
    return echo in ("true", "1", "yes", "outgoing") or "keris" in author


def chat_key(row: dict) -> str:
    for k in ("chatId", "chat_id", "chatid", "contactId", "contact_id"):
        if row.get(k):
            return str(row[k])
    phone = digits(str(row.get("chatType") and row.get("chatId") or row.get("phone") or ""))
    return phone or json.dumps(row, ensure_ascii=False)[:80]


def join_messages() -> dict[int, list[dict]]:
    msgs = load_json("wazzup_messages.json")
    talks = load_json("amo_talks.json")
    leads = load_json("amo_sales_leads.json")
    phones = {}
    ppath = DATA / "contact_phones.json"
    if ppath.exists():
        phones = json.loads(ppath.read_text(encoding="utf-8"))

    by_chat: dict[str, list[dict]] = defaultdict(list)
    by_phone: dict[str, list[dict]] = defaultdict(list)
    for row in msgs:
        by_chat[str(row.get("chatId") or row.get("chat_id") or "")].append(row)
        blob = " ".join(str(v) for v in row.values())
        d = digits(blob)
        if len(d) >= 10:
            by_phone[d[-10:]].append(row)

    lead_ids = {int(l["id"]) for l in leads}
    talk_by_lead: dict[int, list[dict]] = defaultdict(list)
    for t in talks:
        eid = t.get("entity_id")
        if eid and int(eid) in lead_ids:
            talk_by_lead[int(eid)].append(t)

    bridge_path = DATA / "amobridge_chats.json"
    bridge: dict[str, list] = {}
    if bridge_path.exists():
        bridge = json.loads(bridge_path.read_text(encoding="utf-8"))

    joined: dict[int, list[dict]] = {}
    for lead in leads:
        lid = int(lead["id"])
        acc: list[dict] = []
        if str(lid) in bridge and isinstance(bridge[str(lid)], list) and any(msg_text(r) for r in bridge[str(lid)]):
            joined[lid] = list(bridge[str(lid)])
            continue
        seen = set()
        for t in talk_by_lead.get(lid, []):
            cid = str(t.get("chat_id") or "")
            for row in by_chat.get(cid, []):
                marker = (msg_when(row), msg_text(row), msg_out(row))
                if marker in seen:
                    continue
                seen.add(marker)
                acc.append(row)
        if not acc:
            for ph in phones.get(str(lid), []):
                for row in by_phone.get(ph, []):
                    marker = (msg_when(row), msg_text(row), msg_out(row))
                    if marker in seen:
                        continue
                    seen.add(marker)
                    acc.append(row)
        if not acc:
            for t in talk_by_lead.get(lid, []):
                acc.append({
                    "chatId": t.get("chat_id"),
                    "text": "",
                    "dateTime": str(t.get("updated_at") or t.get("created_at") or ""),
                    "_talk_only": True,
                    "_origin": t.get("origin"),
                })
        acc.sort(key=lambda r: msg_when(r))
        joined[lid] = acc
    return joined


def cf_map(lead: dict) -> dict:
    out = {}
    for cf in lead.get("custom_fields_values") or []:
        vals = cf.get("values") or []
        out[cf.get("field_id")] = vals[0].get("value") if vals else None
    return out


def extract_fields(texts: str, puppy_names: list[str]) -> dict:
    fields = {}
    prefs = []
    if re.search(r"девочк|сук[аи]", texts, re.I):
        prefs.append("девочка")
    if re.search(r"мальчик|кобел", texts, re.I):
        prefs.append("мальчик")
    if re.search(r"микро", texts, re.I):
        prefs.append("микро")
    if re.search(r"\bмини\b", texts, re.I):
        prefs.append("мини")
    color = re.search(r"(абрикос|apricot|ice gold|кремов|ред|red|палевый)", texts, re.I)
    if color:
        prefs.append(color.group(1))
    hit_names = [n for n in puppy_names if n and re.search(rf"\b{re.escape(n)}\b", texts, re.I)]
    if hit_names:
        prefs.append("кличка: " + ", ".join(hit_names))
        fields["puppy_names"] = hit_names
    if prefs:
        fields["comment"] = "; ".join(dict.fromkeys(prefs))
    if RE_INSTALL.search(texts):
        fields["paytype"] = "Рассрочка"
        m = re.search(r"рассрочк\w* на\s*(3|6|10)", texts, re.I)
        if m:
            fields["rass_term"] = m.group(1)
    elif re.search(r"полн(ая|ую) оплат", texts, re.I):
        fields["paytype"] = "Полная оплата"
    for name, rx in RE_SOURCE.items():
        if rx.search(texts):
            fields["source"] = name
            break
    date_m = re.search(
        r"(январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр)\w*\s*20\d{2}",
        texts,
        re.I,
    )
    if date_m and RE_WAIT.search(texts):
        fields["wait_hint"] = date_m.group(0)
    return fields


def classify_one(lead: dict, rows: list[dict], puppy_names: list[str]) -> dict:
    name = lead.get("name") or ""
    current = int(lead.get("status_id") or 0)
    lid = int(lead["id"])
    texts_in = []
    texts_out = []
    quotes = []
    for row in rows:
        text = msg_text(row)
        if not text:
            continue
        if msg_out(row):
            texts_out.append(text)
        else:
            texts_in.append(text)
        if len(quotes) < 8 and not AUTO_HINT.search(text):
            who = "клуб" if msg_out(row) else "клиент"
            quotes.append(f"{who}: {text[:160]}")
    blob_in = "\n".join(texts_in)
    blob_all = "\n".join(texts_in + texts_out)
    # шаблон бота содержит «бронирование 30%» — не считать это бронью
    live_bits = []
    for row in rows:
        text = msg_text(row)
        if not text:
            continue
        if str(row.get("author") or "").lower() == "robot":
            continue
        live_bits.append(text)
    blob_live = "\n".join(live_bits) or blob_in
    last_when = msg_when(rows[-1]) if rows else ""
    last_out = msg_out(rows[-1]) if rows else False

    rec = {
        "id": lid,
        "name": name,
        "current": current,
        "current_name": STAGE_NAME.get(current, str(current)),
        "proposed": None,
        "proposed_name": None,
        "confidence": "low",
        "reason": "",
        "skip": False,
        "quotes": quotes[:4],
        "fields": {},
        "spawn_clients": False,
        "spawn_rass": False,
        "purchase_date": None,
        "msg_in": len(texts_in),
        "msg_out": len(texts_out),
        "msg_total": len(rows),
        "last_when": last_when,
        "pattern": "",
    }

    if SKIP_NAME.search(name):
        rec.update(skip=True, reason="служебная/тест", pattern="skip_name", confidence="high")
        return rec
    if GROOMING_HINT.search(blob_all):
        rec.update(skip=True, reason="переписка по салону/грумингу, не продажа щенка", pattern="grooming", confidence="high")
        return rec
    if not blob_in and not blob_all and rows:
        rec.update(
            skip=True,
            reason="беседа есть, текста в API нет",
            pattern="no_text_has_talk",
            confidence="high",
        )
        return rec
    if not rows:
        rec.update(skip=True, reason="нет текста переписки", pattern="no_text", confidence="high")
        return rec
    if not blob_in and all(AUTO_HINT.search(t) for t in texts_out if t):
        rec.update(skip=True, reason="только автоответ", pattern="auto_only", confidence="high")
        return rec

    fields = extract_fields(blob_live, puppy_names)
    rec["fields"] = fields

    def set_stage(key: str, conf: str, reason: str, pattern: str):
        rec["proposed"] = ST[key]
        rec["proposed_name"] = STAGE_NAME[ST[key]]
        rec["confidence"] = conf
        rec["reason"] = reason
        rec["pattern"] = pattern

    if RE_SOLD.search(blob_live):
        set_stage("sold", "high", "в переписке факт переезда/дома", "sold")
        rec["spawn_clients"] = True
        rec["purchase_date"] = last_when[:10] if last_when else None
        rec["fields"]["pitomnik"] = True
        return rec
    if RE_DOCS.search(blob_live) and RE_BOOKED.search(blob_live):
        set_stage("docs", "high", "документы + бронь", "docs")
        return rec
    if RE_BOOKED.search(blob_live):
        set_stage("booked", "high", "явная предоплата/бронь", "booked")
        rec["fields"]["prepay_status"] = "Оплачена"
        if fields.get("paytype") == "Рассрочка":
            rec["spawn_rass"] = True
        return rec
    if RE_REFUSE.search(blob_live):
        set_stage("lost", "high", "явный отказ", "lost_refuse")
        rec["fields"]["lost_reason"] = LOST_REASON_PRICE if RE_PRICE.search(blob_live) else LOST_REASON_GHOST
        rec["fields"]["lost_comment"] = "отказ в переписке"
        return rec
    if RE_PRICE.search(blob_in) and not RE_BOOKED.search(blob_live) and len(texts_in) <= 4:
        set_stage("lost", "medium", "уперлись в цену, брони нет", "lost_price")
        rec["fields"]["lost_reason"] = LOST_REASON_PRICE
        return rec
    if RE_WAIT.search(blob_live):
        set_stage("wait", "high", "ждут помёт", "wait")
        return rec
    if RE_PICKED.search(blob_live) or fields.get("puppy_names"):
        set_stage("picked", "medium", "назван конкретный щенок, оплаты нет", "picked")
        return rec

    silent_days = None
    if last_when and last_out:
        try:
            dt = datetime.fromisoformat(last_when.replace("Z", "+00:00").replace(" ", "T")[:19])
            silent_days = (datetime.now() - dt.replace(tzinfo=None)).days
        except ValueError:
            silent_days = None
    if (
        silent_days is not None
        and silent_days >= 21
        and len(texts_in) >= 2
        and RE_DIALOG.search(blob_in)
        and not RE_BOOKED.search(blob_all)
    ):
        set_stage("lost", "medium", f"тишина {silent_days} дн после ответа клуба", "lost_ghost")
        rec["fields"]["lost_reason"] = LOST_REASON_GHOST
        rec["fields"]["lost_comment"] = f"нет ответа {silent_days} дн"
        rec["pattern"] = "lost_ghost"
        return rec

    if RE_DIALOG.search(blob_in) or (len(texts_in) >= 2 and len(texts_out) >= 1):
        set_stage("work", "high", "живой диалог по щенку", "work")
        return rec
    if texts_in:
        set_stage("work", "medium", "есть входящие, мало контекста", "work_thin")
        return rec

    rec.update(skip=True, reason="нечего классифицировать", pattern="emptyish", confidence="low")
    return rec


def cmd_classify() -> None:
    leads = load_json("amo_sales_leads.json")
    puppies = load_json("amo_puppies.json")
    names = []
    for p in puppies:
        nm = p.get("name") or ""
        nm = re.sub(r"^\[ТЕСТ\]\s*", "", nm)
        nm = re.sub(r"[^\wА-Яа-яёЁ\- ]+", " ", nm).strip()
        if nm:
            names.append(nm.split()[0])
        for cf in p.get("custom_fields_values") or []:
            if cf.get("field_id") == 1820803:
                val = (cf.get("values") or [{}])[0].get("value")
                if val:
                    names.append(str(val))
    names = sorted({n for n in names if len(n) >= 3})
    print("клички в витрине:", names)

    ppath = DATA / "contact_phones.json"
    if not ppath.exists():
        print("телефоны не сняты — join только по chat_id бесед")
    joined = join_messages()
    with_text = sum(1 for v in joined.values() if v)
    print(f"склейка: {with_text}/{len(leads)} сделок с текстом")

    results = []
    for lead in leads:
        rec = classify_one(lead, joined.get(int(lead["id"]), []), names)
        if rec["proposed"] and rec["proposed"] == rec["current"] and rec["confidence"] == "high":
            rec["skip"] = True
            rec["reason"] = "уже на нужном этапе"
            rec["pattern"] = "already_ok"
        results.append(rec)
    save_json("classified.json", results)
    by_pat = Counter(r["pattern"] for r in results)
    by_st = Counter(r["proposed_name"] or ("skip:" + r["pattern"]) for r in results)
    by_conf = Counter(r["confidence"] for r in results)
    print("паттерны", dict(by_pat))
    print("предложение", dict(by_st))
    print("уверенность", dict(by_conf))


def cmd_report(limit: int) -> None:
    rows = load_json("classified.json")
    print("\n=== сводка ===")
    print(f"всего {len(rows)}")
    print("skip", sum(1 for r in rows if r["skip"]))
    print("high", sum(1 for r in rows if r["confidence"] == "high" and not r["skip"] and r["proposed"]))
    print("medium", sum(1 for r in rows if r["confidence"] == "medium" and not r["skip"]))
    print("sold", sum(1 for r in rows if r["proposed"] == ST["sold"]))
    print("booked", sum(1 for r in rows if r["proposed"] == ST["booked"]))
    print("clients spawn", sum(1 for r in rows if r.get("spawn_clients")))
    print("rass spawn", sum(1 for r in rows if r.get("spawn_rass")))
    print("\nпо этапам:")
    for name, n in Counter(r["proposed_name"] or ("—" if r["skip"] else "?") for r in rows).most_common():
        print(f"  {n:4d}  {name}")
    print("\nпаттерны:")
    for name, n in Counter(r["pattern"] for r in rows).most_common():
        print(f"  {n:4d}  {name}")

    high = [r for r in rows if r["confidence"] == "high" and not r["skip"] and r["proposed"]]
    # разнотипный пакет
    pack = []
    used = set()
    for pat in ("sold", "booked", "docs", "wait", "work", "lost_refuse", "picked"):
        for r in high:
            if r["pattern"] == pat and r["id"] not in used:
                pack.append(r)
                used.add(r["id"])
                if sum(1 for x in pack if x["pattern"] == pat) >= 5:
                    break
    for r in high:
        if len(pack) >= limit:
            break
        if r["id"] not in used:
            pack.append(r)
            used.add(r["id"])
    save_json("first_pack.json", pack)
    print(f"\n=== первый пакет {len(pack)} ===")
    for r in pack:
        q = (r.get("quotes") or ["—"])[0]
        print(
            f"{r['id']}  {r['name'][:28]:<28}  {r['current_name']:<20} → {r['proposed_name']:<22}  "
            f"{r['pattern']}  {q[:70]}"
        )

    med = [r for r in rows if r["confidence"] == "medium" and not r["skip"]]
    save_json("ambiguous.json", med)
    print("\n=== спорные паттерны ===")
    for pat, n in Counter(r["pattern"] for r in med).most_common():
        print(f"  {n:4d}  {pat}")
        sample = next(r for r in med if r["pattern"] == pat)
        print(f"       напр. {sample['id']} {sample['name']}: {sample['reason']}")


def cf_payload(fields: dict) -> list[dict]:
    # в проходе пишем только свободный текст: списки amo бьют NotSupportedChoice
    out = []
    if fields.get("comment"):
        out.append({"field_id": F["comment"], "values": [{"value": fields["comment"][:500]}]})
    if fields.get("lost_comment"):
        out.append({"field_id": F["lost_comment"], "values": [{"value": fields["lost_comment"][:500]}]})
    return out


def note_text(rec: dict) -> str:
    quotes = " | ".join(rec.get("quotes") or [])[:400]
    return (
        f"Проход 2026-09-17: {rec.get('current_name')} → {rec.get('proposed_name')}. "
        f"{rec.get('reason')}. {quotes}"
    )


def clients_status(purchase_date: str | None) -> int:
    if not purchase_date:
        return ST_CL["bought"]
    try:
        dt = datetime.fromisoformat(purchase_date[:10])
    except ValueError:
        return ST_CL["bought"]
    days = (datetime.now() - dt).days
    if days >= 730:
        return ST_CL["y2"]
    if days >= 365:
        return ST_CL["y1"]
    if days >= 180:
        return ST_CL["m6"]
    if days >= 30:
        return ST_CL["m1"]
    return ST_CL["bought"]


def contact_id_of(lead: dict) -> int | None:
    contacts = (lead.get("_embedded") or {}).get("contacts") or []
    if contacts:
        return contacts[0].get("id")
    return None


def apply_one(rec: dict, leads_by_id: dict, existing_cl: set, existing_r: set, dry: bool) -> dict:
    lead = leads_by_id[rec["id"]]
    body = {
        "status_id": rec["proposed"],
        "pipeline_id": PIPE_PRODAZHI,
    }
    cfs = cf_payload(rec.get("fields") or {})
    if cfs:
        body["custom_fields_values"] = cfs
    result = {"id": rec["id"], "ok": False, "dry": dry}
    if dry:
        result["ok"] = True
        result["body"] = body
        return result
    code, data = amo("PATCH", "/api/v4/leads", [{**body, "id": rec["id"]}])
    if not (200 <= code < 300):
        result["error"] = f"{code} {data}"
        return result
    amo_timeout_ok = True
    try:
        amo("POST", f"/api/v4/leads/{rec['id']}/notes", [{"note_type": "common", "params": {"text": note_text(rec)}}])
    except Exception as exc:
        result["note_error"] = str(exc)[:200]
        amo_timeout_ok = False
    cid = contact_id_of(lead)
    if rec.get("fields", {}).get("pitomnik") and cid:
        amo("PATCH", "/api/v4/contacts", [{"id": cid, "custom_fields_values": [
            {"field_id": C_PITOMNIK, "values": [{"value": True}]}
        ]}])
    if rec.get("spawn_clients") and cid and cid not in existing_cl:
        st = clients_status(rec.get("purchase_date"))
        code2, data2 = amo("POST", "/api/v4/leads", [{
            "name": f"Сопровождение — {rec.get('name')}",
            "pipeline_id": PIPE_CLIENTS,
            "status_id": st,
            "_embedded": {"contacts": [{"id": cid}]},
        }])
        result["clients"] = f"{code2}"
        if 200 <= code2 < 300:
            existing_cl.add(cid)
    if rec.get("spawn_rass") and cid and cid not in existing_r:
        code3, data3 = amo("POST", "/api/v4/leads", [{
            "name": f"Рассрочка — {rec.get('name')}",
            "pipeline_id": PIPE_RASSROCHKA,
            "status_id": ST_RASS["active"],
            "_embedded": {"contacts": [{"id": cid}]},
            "custom_fields_values": cf_payload(rec.get("fields") or {}),
        }])
        result["rass"] = f"{code3}"
        if 200 <= code3 < 300:
            existing_r.add(cid)
    result["ok"] = True
    time.sleep(0.35)
    return result


def cmd_probe_write() -> None:
    leads = load_json("amo_sales_leads.json")
    lead = leads[0]
    code, data = amo("PATCH", "/api/v4/leads", [{"id": lead["id"], "name": lead.get("name")}])
    print("PATCH leads", code, str(data)[:200])
    code, data = amo(
        "POST",
        f"/api/v4/leads/{lead['id']}/notes",
        [{"note_type": "common", "params": {"text": "Проба записи 2026-09-17 (будет перезаписана проходом)"}}],
    )
    print("POST notes", code, str(data)[:200])


def cmd_apply(mode: str, ids: list[int], dry: bool) -> None:
    rows = load_json("classified.json")
    leads = {int(l["id"]): l for l in load_json("amo_sales_leads.json")}
    existing_cl = set()
    for l in load_json("amo_clients.json"):
        for c in (l.get("_embedded") or {}).get("contacts") or []:
            if c.get("id"):
                existing_cl.add(c["id"])
    existing_r = set()
    for l in load_json("amo_rassrochka.json"):
        for c in (l.get("_embedded") or {}).get("contacts") or []:
            if c.get("id"):
                existing_r.add(c["id"])

    if mode == "pack":
        chosen = load_json("first_pack.json")
    elif mode == "high":
        chosen = [
            r
            for r in rows
            if not r["skip"]
            and r["proposed"]
            and r["confidence"] in ("high", "medium")
        ]
    elif mode == "ids":
        want = set(ids)
        chosen = [r for r in rows if r["id"] in want]
    else:
        sys.exit("mode?")

    print(f"к записи {len(chosen)} dry={dry}")
    log = []
    ok = 0
    log_path = DATA / "apply_log.jsonl"
    if log_path.exists():
        done = set()
        for line in log_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                if row.get("ok") and not row.get("dry"):
                    done.add(row["id"])
        chosen = [r for r in chosen if r["id"] not in done]
        print(f"уже записано {len(done)}, осталось {len(chosen)}")
    for rec in chosen:
        if rec["id"] not in leads:
            continue
        if rec.get("proposed") == rec.get("current"):
            continue
        if rec.get("current") == ST["sold"]:
            continue
        if rec.get("current") == ST["lost"] and rec.get("proposed") == ST["work"]:
            continue
        res = apply_one(rec, leads, existing_cl, existing_r, dry)
        row = {**res, "proposed": rec.get("proposed_name"), "pattern": rec.get("pattern")}
        log.append(row)
        if not dry:
            with log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        if res.get("ok"):
            ok += 1
        else:
            print("fail", rec["id"], res.get("error"))
            if "Payment Required" in str(res.get("error")) or "205" in str(res.get("error")):
                print("стоп: аккаунт не принимает запись")
                break
    save_json("apply_log.json", log)
    print(f"ok {ok}/{len(chosen)}")


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("channels")
    d = sub.add_parser("dump")
    d.add_argument("--days", type=int, default=120)
    sub.add_parser("events")
    ing = sub.add_parser("ingest-csv")
    ing.add_argument("path")
    sub.add_parser("phones")
    sub.add_parser("classify")
    r = sub.add_parser("report")
    r.add_argument("--pack", type=int, default=30)
    sub.add_parser("probe-write")
    a = sub.add_parser("apply")
    a.add_argument("--dry-run", action="store_true")
    a.add_argument("--high", action="store_true")
    a.add_argument("--pack", action="store_true")
    a.add_argument("--ids", default="")
    args = p.parse_args()
    if args.cmd == "channels":
        cmd_channels()
    elif args.cmd == "dump":
        cmd_dump(args.days)
    elif args.cmd == "events":
        cmd_events()
    elif args.cmd == "ingest-csv":
        cmd_ingest_csv(args.path)
    elif args.cmd == "phones":
        cmd_phones()
    elif args.cmd == "classify":
        cmd_classify()
    elif args.cmd == "report":
        cmd_report(args.pack)
    elif args.cmd == "probe-write":
        cmd_probe_write()
    elif args.cmd == "apply":
        ids = [int(x) for x in args.ids.split(",") if x.strip()]
        mode = "ids" if ids else ("high" if args.high else "pack")
        cmd_apply(mode, ids, args.dry_run)


if __name__ == "__main__":
    main()
