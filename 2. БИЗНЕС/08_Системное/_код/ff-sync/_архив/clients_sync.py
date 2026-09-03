"""Новые имена с листа Клиенты → клиент в базе и контрагент в МойСклад."""

from db import (
    code_taken,
    get_client,
    get_client_by_name,
    insert_client,
    list_cabinets,
    list_clients,
)
from net import env

TR = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "e",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "y",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "h",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "sch",
    "ъ": "",
    "ы": "y",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
}


def code_from_name(name):
    raw = []
    for ch in (name or "").lower():
        if ch in TR:
            raw.append(TR[ch])
        elif ch.isalnum() and ch.isascii():
            raw.append(ch)
    base = "".join(raw).upper()[:12] or "C"
    if base[0].isdigit():
        base = "C" + base[:11]
    code = base
    n = 2
    while code_taken(code):
        suffix = str(n)
        code = (base[: 12 - len(suffix)] + suffix)
        n += 1
    return code


def cabinets_label(client_id):
    rows = [c for c in list_cabinets() if c["client_id"] == client_id and c["active"]]
    if not rows:
        return "нет кабинета"
    return "+".join(sorted(set(r["marketplace"] for r in rows)))


def client_status(client):
    if not client:
        return "появится после обработки"
    if not client["ms_counterparty_id"]:
        return "нет контрагента в МойСклад"
    if cabinets_label(client["id"]) == "нет кабинета":
        return "клиент есть, ждёт кабинет WB или Ozon"
    return "готов"


def ensure_named_client(name):
    text = (name or "").strip()
    if not text:
        return None
    found = get_client_by_name(text)
    if found:
        return found
    from cli import find_or_create_agent

    code = code_from_name(text)
    if get_client(code) and get_client(code)["name"].strip().lower() == text.lower():
        return get_client(code)
    store_id = env("MS_STORE_ID")
    org_id = env("MS_ORG_ID")
    agent_id = find_or_create_agent(text)
    insert_client(code, text, agent_id, org_id, store_id)
    print("клиент с листа: %s → %s" % (text, code))
    return get_client(code)


def clean_name(raw):
    text = (raw or "").strip()
    if not text or text.lower() in ("название", "клиент"):
        return ""
    for sep in (" — ", " - ", "—", "–"):
        if sep in text:
            left, right = text.split(sep, 1)
            if get_client(left.strip().upper()):
                return get_client(left.strip().upper())["name"]
            return right.strip() or text
    return text


def sheet_names(ws):
    values = ws.get_all_values()
    if not values:
        return []
    start = 1
    if values and str(values[0][0] or "").startswith("Шаг"):
        start = 2
    names = []
    for line in values[start:]:
        name = clean_name(line[0] if line else "")
        if name:
            names.append(name)
    return names


def ingest_new_names(book):
    ws = {s.title: s for s in book.worksheets()}["Клиенты"]
    created = []
    for name in sheet_names(ws):
        before = get_client_by_name(name)
        row = ensure_named_client(name)
        if row and not before:
            created.append(row["code"])
    return created


def gray_row(client):
    if not client:
        return ["", "", "", "появится после обработки"]
    return [
        client["code"],
        client["ms_counterparty_id"] or "",
        cabinets_label(client["id"]),
        client_status(client),
    ]


def refresh_client_gray(book):
    from sheets import CLIENTS_DATA_START, setup_clients

    setup_clients(book)
    ws = {s.title: s for s in book.worksheets()}["Клиенты"]
    values = ws.get_all_values()
    start = CLIENTS_DATA_START
    known = set()
    updates = []
    last_used = start - 1
    for i, line in enumerate(values[start - 1 :], start=start):
        raw = (line[0] if line else "").strip()
        name = clean_name(raw)
        if not name:
            continue
        last_used = i
        known.add(name.lower())
        if name != raw:
            updates.append({"range": "A%s" % i, "values": [[name]]})
        updates.append({"range": "B%s:E%s" % (i, i), "values": [gray_row(get_client_by_name(name))]})
    next_row = last_used + 1
    for client in list_clients():
        if client["name"].strip().lower() in known:
            continue
        updates.append(
            {
                "range": "A%s:E%s" % (next_row, next_row),
                "values": [[client["name"]] + gray_row(client)],
            }
        )
        next_row += 1
    if updates:
        ws.batch_update(updates, value_input_option="RAW")
    print("лист Клиенты: серые колонки обновлены")
