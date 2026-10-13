"""Контрагенты МойСклад с группой Фулфилмент → клиенты и кабинеты в базе."""

import math
import re
import uuid
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
from net import MS_BASE, OZON_BASE, WB_BASE, YANDEX_BASE, env_opt, ms_headers, ozon_headers, req, wb_headers, yandex_headers

TAG = "Фулфилмент"
MSK = timezone(timedelta(hours=3))


def checked_inn(raw):
    inn = str(raw or "").strip()
    if not re.fullmatch(r"(?:[0-9]{10}|[0-9]{12})", inn) or len(set(inn)) == 1:
        raise ValueError("ИНН должен содержать 10 или 12 цифр")
    digits = list(map(int, inn))
    checks = [(9, (2,4,10,3,5,9,4,6,8))] if len(inn) == 10 else [(10,(7,2,4,10,3,5,9,4,6,8)),(11,(3,7,2,4,10,3,5,9,4,6,8))]
    if any(sum(a*b for a,b in zip(digits, weights)) % 11 % 10 != digits[pos] for pos,weights in checks):
        raise ValueError("В ИНН ошибка: не совпала контрольная цифра")
    return inn


def _agent_request(method, path, **kw):
    r = req(method, MS_BASE + path, headers=ms_headers(), **kw)
    if r.status_code not in (200, 201):
        raise ValueError("МойСклад не подтвердил операцию (HTTP %s). Проверь результат перед повтором." % r.status_code)
    return r.json()


def find_by_inn(inn):
    """Проверяем активные и архивные карточки всего аккаунта, без фильтра по тегу."""
    found = {}
    for archived in ("false", "true"):
        offset = 0
        page_ids = set()
        expected = None
        while True:
            data = _agent_request("GET", "/entity/counterparty", params={"filter": "inn=%s;archived=%s" % (inn, archived), "limit": 100, "offset": offset})
            rows = data.get("rows") or []
            size = data.get("meta", {}).get("size")
            if size is None or (expected is not None and size != expected) or (not rows and offset < size):
                raise ValueError("МойСклад не вернул полный список дублей. Создание остановлено")
            expected = size
            for row in rows:
                if str(row.get("inn") or "").strip() != inn:
                    raise ValueError("МойСклад вернул карточку с другим ИНН")
                if row["id"] in page_ids:
                    raise ValueError("МойСклад повторил страницу дублей. Повтори поиск")
                page_ids.add(row["id"])
                found[row["id"]] = row
            offset += len(rows)
            if offset >= size:
                break
    return list(found.values())


def public_agent(row):
    from db import get_cabinet_by_client_mp
    client = get_client_by_ms_id(row["id"])
    fields = attrs_by_name(row)
    out = {k: row.get(k) or "" for k in ("id", "name", "inn", "kpp", "legalTitle", "legalAddress", "ogrn", "ogrnip", "companyType", "phone", "email")}
    out["archived"] = bool(row.get("archived"))
    out["tariff_pick"] = parse_num(fields.get("Сборка, руб/шт"))
    out["client_id"] = client["id"] if client else None
    out["has_wb"] = bool(client and get_cabinet_by_client_mp(client["id"], "wb"))
    out["has_ozon"] = bool(client and get_cabinet_by_client_mp(client["id"], "ozon"))
    return out


def lookup_company(raw):
    inn = checked_inn(raw)
    existing = find_by_inn(inn)
    if existing:
        return {"source": "МойСклад", "existing": [public_agent(r) for r in existing], "company": None, "notice": "Выбери существующую карточку. Дубль создаваться не будет."}
    token = env_opt("DADATA_API_KEY")
    if not token:
        return {"source": "", "existing": [], "company": None, "notice": "Дублей в МойСклад нет. Автозаполнение новых компаний пока не подключено. Заполни реквизиты вручную."}
    r = req("POST", "https://suggestions.dadata.ru/suggestions/api/4_1/rs/findById/party",
            headers={"Authorization": "Token " + token, "Content-Type": "application/json", "Accept": "application/json"},
            json={"query": inn, "branch_type": "MAIN", "count": 20}, timeout=(5, 15))
    if r.status_code != 200:
        raise ValueError("Справочник реквизитов не ответил (HTTP %s). Можно заполнить данные вручную" % r.status_code)
    matches = [x["data"] for x in r.json().get("suggestions", []) if str(x.get("data", {}).get("inn") or "") == inn]
    if len(matches) != 1:
        return {"source": "DaData", "existing": [], "company": None, "notice": "По ИНН не найдена одна головная организация. Проверь ИНН или заполни реквизиты вручную."}
    d = matches[0]
    individual = d.get("type") == "INDIVIDUAL"
    company = {"inn": inn, "companyType": "entrepreneur" if individual else "legal",
               "name": d.get("name", {}).get("short_with_opf") or d.get("name", {}).get("full_with_opf") or "",
               "legalTitle": d.get("name", {}).get("full_with_opf") or "", "kpp": d.get("kpp") or "",
               "legalAddress": (d.get("address") or {}).get("unrestricted_value") or (d.get("address") or {}).get("value") or "",
               "ogrnip" if individual else "ogrn": d.get("ogrn") or ""}
    status = (d.get("state") or {}).get("status") or ""
    return {"source": "DaData", "existing": [], "company": company,
            "notice": "Реквизиты заполнены. Проверь их перед сохранением." if status == "ACTIVE" else "Проверь статус компании в реестре: %s" % (status or "не указан")}


def _onboard_text(data, key, limit=255):
    text = str(data.get(key) or "").strip()
    if len(text) > limit or any(ord(c) < 32 for c in text):
        raise ValueError("Некорректное значение поля %s" % key)
    return text


def _onboard_credentials(data, existing_client, row=None):
    from db import list_cabinets, get_cabinet_by_client_mp
    data = dict(data)
    # Ещё не синхронизированные ключи в выбранной карточке тоже проверяем.
    fields = attrs_by_name(row or {})
    wb = get_cabinet_by_client_mp(existing_client["id"], "wb") if existing_client else None
    ozon = get_cabinet_by_client_mp(existing_client["id"], "ozon") if existing_client else None
    if not data.get("wb_token") and not wb and looks_token(fields.get("WB токен")):
        data["wb_token"] = fields["WB токен"]
    if not data.get("ozon_key") and not ozon and looks_token(fields.get("Ozon Api-Key")):
        data["ozon_key"] = fields["Ozon Api-Key"]
        data["ozon_client_id"] = fields.get("Ozon Client-Id")
    creds = {k: _onboard_text(data, k, 8192 if k != "ozon_client_id" else 40) for k in ("wb_token", "ozon_client_id", "ozon_key")}
    if bool(creds["ozon_key"]) != bool(creds["ozon_client_id"]):
        raise ValueError("Для Ozon нужны оба поля: Client-Id и API Key")
    if creds["ozon_client_id"] and not re.fullmatch(r"[0-9]+", creds["ozon_client_id"]):
        raise ValueError("Ozon Client-Id должен состоять из цифр")
    for cab in list_cabinets():
        if existing_client and cab["client_id"] == existing_client["id"]:
            continue
        if (creds["wb_token"] and cab["marketplace"] == "wb" and cab["token"] == creds["wb_token"]) or (
            cab["marketplace"] == "ozon" and ((creds["ozon_client_id"] and cab["client_id_ext"] == creds["ozon_client_id"]) or (creds["ozon_key"] and cab["token"] == creds["ozon_key"]))):
            raise ValueError("Этот кабинет уже подключён к другому клиенту. Проверь ключи и контрагента")
    if creds["wb_token"]:
        if not looks_token(creds["wb_token"]):
            raise ValueError("Введи полный токен WB")
        ok, status, _body = check_wb(creds["wb_token"])
        if not ok:
            raise ValueError("WB не подтвердил токен (HTTP %s). Контрагент не изменён" % status)
        r = req("POST", "https://content-api.wildberries.ru/content/v2/get/cards/list", headers=wb_headers(creds["wb_token"]),
                json={"settings": {"cursor": {"limit": 1}, "filter": {"withPhoto": -1}}})
        if r.status_code != 200:
            raise ValueError("У токена WB нет подтверждённого доступа к каталогу (HTTP %s)" % r.status_code)
        if not isinstance(r.json().get("cards"), list):
            raise ValueError("WB вернул неполный ответ каталога. Повтори проверку")
        r = req("GET", WB_BASE + "/api/v3/warehouses", headers=wb_headers(creds["wb_token"]))
        if r.status_code != 200 or not isinstance(r.json(), list):
            raise ValueError("У токена WB нет подтверждённого доступа к Маркетплейсу (HTTP %s)" % r.status_code)
    if creds["ozon_key"]:
        if not looks_token(creds["ozon_key"]):
            raise ValueError("Введи полный API Key Ozon")
        ok, status, _body = check_ozon(creds["ozon_client_id"], creds["ozon_key"])
        if not ok:
            raise ValueError("Ozon не подтвердил Client-Id и API Key (HTTP %s). Контрагент не изменён" % status)
    return creds


def _onboard_bank(data):
    bank = {k: _onboard_text(data,k) for k in ("accountNumber", "bic", "bankName", "correspondentAccount")}
    if not any(bank.values()):
        return None
    for key, length in (("accountNumber",20),("bic",9),("correspondentAccount",20)):
        if not re.fullmatch(r"[0-9]{%s}" % length,bank[key]):
            raise ValueError("В банковских реквизитах нужны счёт и корреспондентский счёт по 20 цифр, БИК из 9 цифр")
    if not bank["bankName"]:
        raise ValueError("Укажи наименование банка")
    return bank


def _save_onboard_bank(agent_id, bank):
    if not bank:
        return
    path = "/entity/counterparty/" + agent_id + "/accounts"
    data = _agent_request("GET",path,params={"limit":100})
    rows = data.get("rows") or []
    if data.get("meta",{}).get("size") != len(rows):
        raise ValueError("Банковские счета получены не полностью. Проверь карточку МойСклад")
    if rows:
        if any(all(str(r.get(k) or "")==bank[k] for k in ("accountNumber","bic","correspondentAccount")) for r in rows):
            return
        raise ValueError("У клиента уже есть другие банковские реквизиты. Проверь и измени их в МойСклад, затем оставь банковские поля формы пустыми")
    _agent_request("POST",path,json=bank,retry_safe=False)
    data = _agent_request("GET",path,params={"limit":100})
    if not any(all(str(r.get(k) or "")==bank[k] for k in bank) for r in data.get("rows",[])):
        raise ValueError("Банковские реквизиты не подтверждены. Повтори поиск по ИНН и заверши добавление")


def onboard_client(data):
    """Проверка дубля → проверка ключей → МойСклад → атомарная локальная связь."""
    from db import get_setting, save_onboard_client
    inn = checked_inn(data.get("inn"))
    bank = _onboard_bank(data)
    with run_lock(blocking=False):
        matches = find_by_inn(inn)
        chosen = str(data.get("existing_id") or "")
        if matches and not chosen:
            return {"existing": [public_agent(r) for r in matches]}
        row = next((r for r in matches if r["id"] == chosen), None) if chosen else None
        if chosen and row is None:
            raise ValueError("Выбранная карточка не найдена по этому ИНН. Повтори поиск")
        if row and row.get("archived"):
            raise ValueError("Контрагент в архиве МойСклад. Восстанови его карточку, затем повтори добавление")
        client = get_client_by_ms_id(chosen) if chosen else None
        rate_raw = _onboard_text(data, "tariff_pick", 30)
        rate = parse_num(rate_raw)
        if rate_raw and (rate is None or not math.isfinite(rate) or rate < 0):
            raise ValueError("Ставка сборки должна быть числом от нуля")
        if not row and rate is None:
            raise ValueError("Укажи ставку сборки по умолчанию, ₽/шт")
        if row:
            payload = {"tags": list(dict.fromkeys(list(row.get("tags") or []) + [TAG]))}
            # Дополняем только пустые реквизиты существующей карточки.
            for key in ("legalTitle", "legalAddress", "phone", "email", "kpp", "ogrn", "ogrnip"):
                value = _onboard_text(data,key,1024 if key == "legalAddress" else 255)
                if not row.get(key) and value:
                    if key in ("kpp", "ogrn", "ogrnip") and not re.fullmatch(r"[0-9]{%s}" % {"kpp":9,"ogrn":13,"ogrnip":15}[key],value):
                        raise ValueError("Проверь реквизит %s" % key)
                    payload[key] = value
        else:
            kind = _onboard_text(data, "companyType")
            if kind not in ("legal", "entrepreneur", "individual") or (kind == "legal") != (len(inn) == 10):
                raise ValueError("Тип контрагента не соответствует ИНН")
            payload = {k: _onboard_text(data, k, 1024 if k == "legalAddress" else 255) for k in ("name", "legalTitle", "legalAddress", "kpp", "ogrn", "ogrnip", "phone", "email")}
            for key in ("name", "legalTitle", "legalAddress"):
                if not payload[key]:
                    raise ValueError("Заполни название, полное наименование и юридический адрес")
            if kind == "legal" and (not re.fullmatch(r"[0-9]{9}", payload["kpp"]) or not re.fullmatch(r"[0-9]{13}", payload["ogrn"])):
                raise ValueError("Для организации нужны КПП из 9 цифр и ОГРН из 13 цифр")
            if kind == "entrepreneur" and not re.fullmatch(r"[0-9]{15}", payload["ogrnip"]):
                raise ValueError("Для ИП нужен ОГРНИП из 15 цифр")
            for key in (("ogrnip",) if kind == "legal" else ("kpp", "ogrn")):
                payload.pop(key, None)
            if kind == "individual":
                payload.pop("ogrnip", None)
            payload = {k:v for k,v in payload.items() if v != ""}
            payload.update(inn=inn, companyType=kind, tags=[TAG], syncId=str(uuid.uuid5(uuid.NAMESPACE_URL, "ff-client:" + org_id() + ":" + inn + ":" + payload.get("kpp", ""))))
        creds = _onboard_credentials(data, client, row)
        values = {}
        if rate is not None:
            values["ATTR_AGENT_TARIFF_PICK"] = rate
        if creds["wb_token"]:
            values["ATTR_AGENT_WB"] = mask_token("wb", creds["wb_token"])
        if creds["ozon_key"]:
            values.update(ATTR_AGENT_OZON_CID=creds["ozon_client_id"], ATTR_AGENT_OZON_KEY=mask_token("ozon", creds["ozon_key"]))
        payload["attributes"] = []
        for key, value in values.items():
            aid = get_setting(key)
            if not aid:
                raise ValueError("Не настроены поля клиента в МойСклад. Нажми «Обновить клиентов»")
            payload["attributes"].append({"meta": {"href": MS_BASE + "/entity/counterparty/metadata/attributes/" + aid, "type": "attributemetadata", "mediaType": "application/json"}, "value": value})
        # Повтор с тем же ИНН/КПП использует тот же syncId даже после потери ответа.
        created = _agent_request("PUT" if row else "POST", "/entity/counterparty" + ("/" + chosen if row else ""), json=payload, retry_safe=bool(row))
        full = _agent_request("GET", "/entity/counterparty/" + created["id"])
        if str(full.get("inn") or "") != inn or full.get("archived") or not any(str(t).lower()==TAG.lower() for t in full.get("tags") or []):
            raise ValueError("Карточка МойСклад сохранена, но проверка данных не прошла. Повтори поиск по ИНН")
        for key in ("name", "legalTitle", "legalAddress", "phone", "email", "kpp", "ogrn", "ogrnip", "companyType"):
            if key in payload and str(full.get(key) or "").strip() != str(payload[key]).strip():
                raise ValueError("МойСклад не подтвердил реквизит %s. Повтори поиск и проверь карточку" % key)
        saved = {a.get("meta", {}).get("href", "").rsplit("/", 1)[-1]: a.get("value") for a in full.get("attributes") or []}
        if any(saved.get(get_setting(key)) != value for key, value in values.items()):
            raise ValueError("МойСклад не подтвердил сохранение настроек. Повтори добавление для существующей карточки")
        _save_onboard_bank(full["id"],bank)
        code = client["code"] if client else code_from_name(full["name"])
        local_rate = rate if rate is not None else parse_num(attrs_by_name(full).get("Сборка, руб/шт"))
        cid = save_onboard_client(full, code, org_id(), store_id(), creds, local_rate)
        return {"id": cid, "name": full["name"], "created": row is None,
                "connected": [mp for mp,key in (("WB", "wb_token"), ("Ozon", "ozon_key")) if creds[key]]}


def warm_onboard_catalog(client_id):
    """Каталог только нового/подключённого клиента; ключи не возвращаем браузеру."""
    from db import list_cabinets, set_setting
    import json
    key = "client:catalog-job:%s" % client_id
    try:
        with run_lock():
            set_setting(key, json.dumps({"state": "running"}))
            failed = []
            for cab in list_cabinets():
                if cab["client_id"] == client_id and cab["active"]:
                    _n, err = pull_if_needed(cab, False)
                    if err:
                        failed.append(cab["marketplace"])
            set_setting(key, json.dumps({"state": "error" if failed else "ready", "failed": failed}))
    except Exception:
        set_setting(key, json.dumps({"state": "error", "failed": ["каталог"]}))

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
            raise RuntimeError("МойСклад не отдал список контрагентов: HTTP %s" % r.status_code)
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
    # Пинг идёт по каталогу, а не по unfulfilled/list: у того лимит на секунду
    # выбирается сторонними сервисами клиента, и живой кабинет уходил в 429.
    r = req(
        "POST",
        OZON_BASE + "/v3/product/list",
        headers=ozon_headers(client_id, api_key),
        json={"filter": {"visibility": "ALL"}, "limit": 1},
    )
    return r.status_code == 200, r.status_code, (r.text or "")[:180]


def ensure_client(row):
    ms_id = row.get("id")
    name = (row.get("name") or "").strip()
    found = get_client_by_ms_id(ms_id) or get_client_by_name(name)
    if found and found["ms_counterparty_id"] and found["ms_counterparty_id"] != ms_id:
        raise ValueError("контрагент с именем %s уже связан с другой карточкой МойСклад" % name)
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
    if not ok and existing and (token_changed or status in (429, 500, 502, 503, 504)):
        return existing["id"], False, "WB не проверен (HTTP %s), прежний кабинет сохранён" % status
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
    if not ok and existing and (token_changed or cid != existing["client_id_ext"] or status in (429, 500, 502, 503, 504)):
        return existing["id"], False, "Ozon не проверен (HTTP %s), прежний кабинет сохранён" % status
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


def sync_yandex(client, raw_token, existing):
    """Кабинет Яндекса заводится только если жива ровно одна кампания FBS."""
    token = None
    token_changed = False
    if looks_token(raw_token):
        token = str(raw_token).strip()
        token_changed = True
    elif existing and existing["token"]:
        token = existing["token"]
    if not token:
        return None, False, "нет ключа Яндекс"
    from stock_push import Stop, yandex_fbs_campaigns

    r = req("GET", YANDEX_BASE + "/v2/campaigns", headers=yandex_headers(token))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if r.status_code != 200 or "campaigns" not in (r.json() if r.headers.get("content-type", "").startswith("application/json") else {}):
        if existing and (token_changed or r.status_code in (429, 500, 502, 503, 504)):
            return existing["id"], False, "Яндекс не проверен (HTTP %s), прежний кабинет сохранён" % r.status_code
        body = (r.text or "")[:160]
        cab_id = upsert_cabinet(client["id"], "yandex", "yandex %s" % client["code"], token, None, 0, None, body)
        return cab_id, False, "Яндекс ключ отклонён %s" % stamp()
    live = yandex_fbs_campaigns(r.json().get("campaigns") or [])
    try:
        camp = live[0] if len(live) == 1 else None
        if camp is None:
            raise Stop("кампаний FBS %s" % len(live))
    except Stop as exc:
        if existing:
            return existing["id"], False, "Яндекс %s, прежний кабинет сохранён" % exc
        cab_id = upsert_cabinet(
            client["id"], "yandex", "yandex %s" % client["code"], token, None, 0, None, str(exc)[:180]
        )
        return cab_id, False, "Яндекс %s" % exc
    camp_id = str(camp["id"])
    cab_id = upsert_cabinet(
        client["id"],
        "yandex",
        "yandex %s" % client["code"],
        token,
        camp_id,
        1,
        now,
        "",
    )
    update_cabinet(cab_id, stock_warehouse_id=camp_id)
    if token_changed:
        put_agent_attrs(client["ms_counterparty_id"], {"ATTR_AGENT_YANDEX": mask_token("yandex", token)})
    n, err = pull_if_needed({"id": cab_id}, token_changed)
    if err:
        return cab_id, True, "Яндекс ok %s · каталог: %s" % (stamp(), err)
    return cab_id, True, "Яндекс ok %s · %s товаров" % (stamp(), n)


def sync_one(row):
    client = ensure_client(row)
    ensure_tag(row)
    fields = attrs_by_name(row)
    update_client(
        client["id"],
        tariff_pick=parse_num(fields.get("Сборка, руб/шт")),
    )
    client = get_client(client["code"])
    from db import get_cabinet_by_client_mp

    wb_exist = get_cabinet_by_client_mp(client["id"], "wb")
    oz_exist = get_cabinet_by_client_mp(client["id"], "ozon")
    ya_exist = get_cabinet_by_client_mp(client["id"], "yandex")
    parts = []
    _cab, _ok, msg = sync_wb(client, fields.get("WB токен"), wb_exist)
    parts.append(msg)
    _cab, _ok, msg = sync_ozon(client, fields.get("Ozon Client-Id"), fields.get("Ozon Api-Key"), oz_exist)
    parts.append(msg)
    _cab, _ok, msg = sync_yandex(client, fields.get("Яндекс Api-Key"), ya_exist)
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
