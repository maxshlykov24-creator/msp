"""Контрагенты МойСклад с группой Фулфилмент → клиенты и кабинеты в базе."""

from datetime import datetime, timedelta, timezone

from db import (
    cache_count,
    code_taken,
    get_client,
    get_client_by_ms_id,
    get_client_by_name,
    init_db,
    insert_client,
    list_clients,
    run_lock,
    update_cabinet,
    update_client,
    upsert_cabinet,
)
from ms import AGENT_ATTRS, attrs_by_name, ensure_all_attrs, org_id, store_id
from net import MS_BASE, OZON_BASE, WB_BASE, ms_headers, ozon_headers, req, wb_headers

TAG = "Фулфилмент"
MSK = timezone(timedelta(hours=3))

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
        code = base[: 12 - len(suffix)] + suffix
        n += 1
    return code


def now_msk():
    return datetime.now(MSK)


def stamp():
    return now_msk().strftime("%d.%m %H:%M")


def looks_masked(val):
    s = str(val or "").strip()
    return "****" in s and "принят" in s


def looks_token(val):
    s = str(val or "").strip()
    if not s or looks_masked(s) or len(s) < 8:
        return False
    if s.lower() in ("нет", "нет ключа", "-", "—"):
        return False
    return True


def mask_token(kind, token):
    tail = token[-4:] if len(token) >= 4 else token
    return "%s_****%s принят %s" % (kind, tail, stamp())


def parse_num(raw):
    if raw is None or raw == "":
        return None
    try:
        return float(str(raw).replace(",", "."))
    except ValueError:
        return None


def iter_agents():
    offset = 0
    while True:
        r = req(
            "GET",
            MS_BASE + "/entity/counterparty",
            headers=ms_headers(),
            params={"limit": 100, "offset": offset, "expand": "group"},
        )
        if r.status_code != 200:
            print("контрагенты: %s %s" % (r.status_code, (r.text or "")[:200]))
            return
        rows = r.json().get("rows") or []
        if not rows:
            return
        for row in rows:
            yield row
        if len(rows) < 100:
            return
        offset += 100


def group_name(row):
    return ((row.get("group") or {}).get("name") or "").strip()


def has_ff_mark(row):
    """Тег и группа без учёта регистра: в МойСклад пишут и «фулфилмент»."""
    needle = TAG.lower()
    for tag in row.get("tags") or []:
        if str(tag).lower() == needle:
            return True
    return needle in group_name(row).lower()


def is_ff(row, known_ids):
    """Берём контрагента, если тег или группа называется Фулфилмент.

    В МойСклад это разные поля: тег — tags, папка — group. Раньше смотрели
    только тег и только с заглавной буквы.
    """
    if has_ff_mark(row):
        return True
    if row.get("id") in known_ids:
        return True
    return False


def ensure_tag(row):
    tags = list(row.get("tags") or [])
    if any(str(t).lower() == TAG.lower() for t in tags):
        return
    tags.append(TAG)
    r = req(
        "PUT",
        MS_BASE + "/entity/counterparty/" + row["id"],
        headers=ms_headers(),
        json={"tags": tags},
    )
    if r.status_code in (200, 201):
        print("тег %s повесил на %s" % (TAG, row.get("name")))
        row["tags"] = tags


def put_agent_attrs(agent_id, values):
    payload = []
    for name, _typ, key in AGENT_ATTRS:
        if key not in values:
            continue
        from db import get_setting

        aid = get_setting(key)
        if not aid:
            continue
        payload.append(
            {
                "meta": {
                    "href": MS_BASE + "/entity/counterparty/metadata/attributes/" + aid,
                    "type": "attributemetadata",
                    "mediaType": "application/json",
                },
                "value": values[key],
            }
        )
    if not payload:
        return
    r = req(
        "PUT",
        MS_BASE + "/entity/counterparty/" + agent_id,
        headers=ms_headers(),
        json={"attributes": payload},
    )
    if r.status_code not in (200, 201):
        print("не записал поля контрагента %s: %s %s" % (agent_id, r.status_code, (r.text or "")[:160]))


def check_wb(token):
    r = req("GET", WB_BASE + "/ping", headers=wb_headers(token))
    return r.status_code == 200, r.status_code, (r.text or "")[:180]


def check_ozon(client_id, api_key):
    now = datetime.now(timezone.utc)
    payload = {
        "dir": "ASC",
        "filter": {
            "cutoff_from": (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "cutoff_to": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        "limit": 1,
        "offset": 0,
    }
    r = req(
        "POST",
        OZON_BASE + "/v3/posting/fbs/unfulfilled/list",
        headers=ozon_headers(client_id, api_key),
        json=payload,
    )
    return r.status_code == 200, r.status_code, (r.text or "")[:180]


def ensure_client(row):
    ms_id = row.get("id")
    name = (row.get("name") or "").strip()
    found = get_client_by_ms_id(ms_id) or get_client_by_name(name)
    if found:
        patch = {}
        if found["name"] != name and name:
            patch["name"] = name
        if found["ms_counterparty_id"] != ms_id:
            patch["ms_counterparty_id"] = ms_id
        if not found["ms_org_id"]:
            patch["ms_org_id"] = org_id()
        if not found["ms_store_id"]:
            patch["ms_store_id"] = store_id()
        if patch:
            update_client(found["id"], **patch)
            return get_client_by_ms_id(ms_id) or get_client(found["code"])
        return found
    code = code_from_name(name)
    insert_client(code, name, ms_id, org_id(), store_id())
    print("клиент из МойСклад: %s → %s" % (name, code))
    return get_client(code)


def should_pull(cab, token_changed):
    if token_changed:
        return True
    if cache_count(cabinet_id=cab["id"]) == 0:
        return True
    last = cab["last_pull_at"]
    if not last:
        return True
    try:
        when = datetime.fromisoformat(last.replace("Z", "+00:00"))
    except ValueError:
        return True
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - when > timedelta(hours=6)


def pull_if_needed(cab, token_changed):
    from catalog_pull import pull_one

    fresh = get_cabinet_row(cab["id"])
    if not should_pull(fresh, token_changed):
        return cache_count(cabinet_id=cab["id"]), ""
    try:
        n, err = pull_one(fresh)
    except Exception as exc:
        err = str(exc)[:180]
        n = 0
    if not err:
        update_cabinet(cab["id"], last_pull_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    return n, err


def get_cabinet_row(cab_id):
    from db import get_cabinet

    return get_cabinet(cab_id)


def sync_wb(client, raw_token, existing):
    token_changed = False
    token = None
    if looks_token(raw_token):
        token = str(raw_token).strip()
        token_changed = True
    elif existing and existing["token"]:
        token = existing["token"]
    if not token:
        return None, False, "нет токена"
    ok, status, body = check_wb(token)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cab_id = upsert_cabinet(
        client["id"],
        "wb",
        "wb %s" % client["code"],
        token,
        None,
        1 if ok else 0,
        now if ok else None,
        "" if ok else ("%s %s" % (status, body)),
    )
    if token_changed and ok:
        put_agent_attrs(client["ms_counterparty_id"], {"ATTR_AGENT_WB": mask_token("wb", token)})
    n, err = (0, "")
    if ok:
        n, err = pull_if_needed({"id": cab_id}, token_changed)
    if ok and not err:
        return cab_id, True, "WB ok %s · %s товаров" % (stamp(), n)
    if ok:
        return cab_id, True, "WB ok %s · каталог: %s" % (stamp(), err)
    return cab_id, False, "WB ключ отклонён %s" % stamp()


def sync_ozon(client, cid_raw, key_raw, existing):
    cid = str(cid_raw or "").strip() if cid_raw else ""
    if existing and existing["client_id_ext"] and (not cid or looks_masked(cid)):
        cid = existing["client_id_ext"]
    token = None
    token_changed = False
    if looks_token(key_raw):
        token = str(key_raw).strip()
        token_changed = True
    elif existing and existing["token"]:
        token = existing["token"]
    if not token or not cid:
        return None, False, "нет ключа Ozon"
    ok, status, body = check_ozon(cid, token)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cab_id = upsert_cabinet(
        client["id"],
        "ozon",
        "ozon %s" % client["code"],
        token,
        cid,
        1 if ok else 0,
        now if ok else None,
        "" if ok else ("%s %s" % (status, body)),
    )
    if token_changed and ok:
        put_agent_attrs(client["ms_counterparty_id"], {"ATTR_AGENT_OZON_KEY": mask_token("ozon", token)})
    n, err = (0, "")
    if ok:
        n, err = pull_if_needed({"id": cab_id}, token_changed)
    if ok and not err:
        return cab_id, True, "Ozon ok %s · %s товаров" % (stamp(), n)
    if ok:
        return cab_id, True, "Ozon ok %s · каталог: %s" % (stamp(), err)
    return cab_id, False, "Ozon ключ отклонён %s" % stamp()


def sync_one(row):
    client = ensure_client(row)
    ensure_tag(row)
    fields = attrs_by_name(row)
    update_client(
        client["id"],
        tariff_storage=parse_num(fields.get("Тариф хранения, руб/л/сутки")),
    )
    client = get_client(client["code"])
    from db import get_cabinet_by_client_mp

    wb_exist = get_cabinet_by_client_mp(client["id"], "wb")
    oz_exist = get_cabinet_by_client_mp(client["id"], "ozon")
    parts = []
    _cab, _ok, msg = sync_wb(client, fields.get("WB токен"), wb_exist)
    parts.append(msg)
    _cab, _ok, msg = sync_ozon(client, fields.get("Ozon Client-Id"), fields.get("Ozon Api-Key"), oz_exist)
    parts.append(msg)
    note = " / ".join(parts)
    put_agent_attrs(client["ms_counterparty_id"], {"ATTR_AGENT_SYNC": note})
    print("%s: %s" % (client["name"], note))
    return client["code"], note


def run(blocking=True):
    init_db()
    with run_lock(blocking=blocking):
        return _run()


def _run():
    ensure_all_attrs()
    known = {c["ms_counterparty_id"] for c in list_clients() if c["ms_counterparty_id"]}
    seen = set()
    notes = []
    scanned = 0
    for row in iter_agents():
        scanned += 1
        if not is_ff(row, known):
            continue
        seen.add(row.get("id"))
        full = req("GET", MS_BASE + "/entity/counterparty/" + row["id"], headers=ms_headers())
        if full.status_code == 200:
            row = full.json()
        try:
            notes.append(sync_one(row))
        except Exception as exc:
            print("контрагент %s сбой: %s" % (row.get("name"), exc))
            notes.append((row.get("name"), str(exc)))
    print("контрагентов в МойСклад: %s, с группой или тегом %s: %s" % (scanned, TAG, len(seen)))
    if not seen:
        notes.append(("", "нет контрагентов с тегом или группой «%s»" % TAG))
    for client in list_clients():
        ms_id = client["ms_counterparty_id"]
        if not ms_id or ms_id in seen:
            continue
        r = req("GET", MS_BASE + "/entity/counterparty/" + ms_id, headers=ms_headers())
        if r.status_code != 200:
            continue
        try:
            notes.append(sync_one(r.json()))
        except Exception as exc:
            print("контрагент %s сбой: %s" % (client["name"], exc))
    return notes


if __name__ == "__main__":
    run()
