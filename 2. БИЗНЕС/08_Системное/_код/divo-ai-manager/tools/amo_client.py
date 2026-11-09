"""Клиент amoCRM для DIVO: чтение и запись сделки.

Токен берётся из .env проекта или из DIVO_Motors/06_Доступы/.env.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve()
ROOT = HERE.parents[1]
LOCAL_ENV = ROOT / ".env"


def _divo_env_paths() -> tuple[Path, ...]:
    out = [LOCAL_ENV]
    here = HERE
    for parent in here.parents:
        divo = parent / "2. БИЗНЕС" / "04_Производство" / "Активные" / "DIVO_Motors"
        for rel in ("06_Доступы/.env", ".env"):
            path = divo / rel
            if path.exists() and path not in out:
                out.append(path)
    return tuple(out)


ENV_CANDIDATES = _divo_env_paths()

NIKITA_USER_ID = 13334858
# Ответственный на заявках бота до того, как менеджер нажал кнопку: владелец.
# Живому Никите чужие заявки не падают, а Максим видит весь поток от бота.
OWNER_USER_ID = 9490530
BOT_RESPONSIBLE = OWNER_USER_ID
PIPELINE_SALES = 10372290
PIPELINE_TECH = 10447334

# Telegram → amo. Имена сверки с /api/v4/users 2026-09-16.
AMO_USER_BY_TG = {
    435207481: 9490530,  # Максим Шлыков
    212666249: 13334858,  # Никита Яменский
    821842895: 12826002,  # Николас
    282491919: 13180098,  # Евгений
    434232049: 13835174,  # Эльзар / El’zar
    558228956: 14181846,  # Назар, в Telegram имя U
}
AMO_USER_BY_NICK = {
    "maxim_shlykov": 9490530,
    "msp_msproduct": 9490530,
    "nikita_yamenskii": 13334858,
    "nikolas413": 12826002,
    "vladimir0vich1": 13180098,
    "elzar_asadzade": 13835174,
}
NAME_ALIAS = {
    "женя": "евгений",
    "evgeniy": "евгений",
    "evgeny": "евгений",
    "eugene": "евгений",
    "elzar": "эльзар",
    "asadzade": "эльзар",
    "асадзаде": "эльзар",
    "nazar": "назар",
    "nikita": "никита",
    "nicolas": "николас",
    "nicholas": "николас",
    "maxim": "максим",
}
KNOWN_USERS = [
    {"id": 9490530, "name": "Максим"},
    {"id": 12826002, "name": "Николас"},
    {"id": 13180098, "name": "Евгений"},
    {"id": 13334858, "name": "Никита"},
    {"id": 13835174, "name": "Эльзар Асадзаде"},
    {"id": 14181846, "name": "Назар"},
]
_users_cache: tuple[float, list[dict]] | None = None
STATUS_NEW = 82003646
# В воронке DIVO нет этапа «Взято в работу». Следующий после новой заявки — этот.
STATUS_IN_WORK = 82003650  # Контакт установлен
STATUS_WON = 142
STATUS_LOST = 143
STATUS_SPAM = 82249454
STATUS_TECH_UNSORTED = 82517822
STATUS_TECH_CHAT = 82517842
TECH_OPEN = {STATUS_TECH_UNSORTED, STATUS_TECH_CHAT}
FIELD_SOURCE = 2026903
FIELD_VIN = 2027421
FIELD_BRAND = 2027423
FIELD_MODEL = 2027425
FIELD_YEAR = 2027427
FIELD_KM = 2051185
FIELD_LISTING_URL = 2051461
SOURCE_AVITO_CHAT = 1641531
SOURCE_AUTORU_CHAT = 1641533
CLOSED = {STATUS_WON, STATUS_LOST, STATUS_SPAM}


def _load_env() -> None:
    for path in ENV_CANDIDATES:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env()

DOMAIN = os.environ.get("AMOCRM_BASE_DOMAIN") or os.environ.get("AMOCRM_SUBDOMAIN", "")
if DOMAIN and "." not in DOMAIN:
    DOMAIN = f"{DOMAIN}.amocrm.ru"
TOKEN = os.environ.get("AMOCRM_LONG_LIVED_TOKEN") or os.environ.get("AMO_ACCESS_TOKEN", "")


class AmoError(RuntimeError):
    def __init__(self, code: int, detail: str) -> None:
        super().__init__(f"amo {code}: {detail}")
        self.code = code
        self.detail = detail


def request(
    path: str,
    params: dict | None = None,
    method: str = "GET",
    body: object | None = None,
    attempts: int = 3,
) -> tuple[int, dict]:
    """Запрос к amo. 204 и 4xx не бросают исключение, возвращают код."""
    if not DOMAIN or not TOKEN:
        raise AmoError(0, "нет AMOCRM_BASE_DOMAIN или AMOCRM_LONG_LIVED_TOKEN")
    url = f"https://{DOMAIN}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    payload = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/json",
        "User-Agent": "divo-ai-manager/1.0",
    }
    if payload is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=payload, headers=headers, method=method)
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=40) as resp:
                raw = resp.read()
                if resp.status == 204 or not raw:
                    return resp.status, {}
                return resp.status, json.loads(raw)
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", "replace")
            if exc.code == 429 and attempt < attempts - 1:
                time.sleep(2 * (attempt + 1))
                continue
            try:
                data = json.loads(body_text)
            except json.JSONDecodeError:
                data = {"detail": body_text[:400]}
            return exc.code, data
        except urllib.error.URLError as exc:
            if attempt < attempts - 1:
                time.sleep(2 * (attempt + 1))
                continue
            raise AmoError(0, str(exc.reason)) from exc
    raise AmoError(0, "не достучались")


def get(path: str, params: dict | None = None) -> dict:
    code, data = request(path, params)
    if code >= 400:
        raise AmoError(code, str(data.get("detail") or data.get("title") or data)[:300])
    return data


def items(data: dict, key: str) -> list[dict]:
    return (data.get("_embedded") or {}).get(key) or []


def paged(path: str, params: dict, key: str, max_pages: int = 50):
    """Итератор по страницам списочного метода amo."""
    params = dict(params)
    page = 1
    while page <= max_pages:
        params["page"] = page
        code, data = request(path, params)
        if code == 204 or not data:
            return
        if code >= 400:
            raise AmoError(code, str(data.get("detail") or data)[:300])
        rows = items(data, key)
        if not rows:
            return
        yield from rows
        if not (data.get("_links") or {}).get("next"):
            return
        page += 1
        time.sleep(0.2)


def write(path: str, body: object, method: str = "POST", *, attempts: int = 3) -> dict:
    code, data = request(path, method=method, body=body, attempts=attempts)
    if code >= 400:
        raise AmoError(code, str(data.get("detail") or data.get("title") or data)[:300])
    return data


def amo_phone(raw: str) -> str:
    """Номер для карточки amo: +7XXXXXXXXXX, иначе + и цифры с кодом страны.

    10 цифр без 7 и 8 считаем российскими. 8XXXXXXXXXXX → +7.
    """
    digits = "".join(ch for ch in (raw or "") if ch.isdigit())
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) == 11 and digits[0] == "8":
        digits = "7" + digits[1:]
    elif len(digits) == 10:
        digits = "7" + digits
    if len(digits) < 10:
        return ""
    return "+" + digits


def lead_url(lead_id: int) -> str:
    host = DOMAIN if DOMAIN.endswith(".amocrm.ru") else f"{DOMAIN}.amocrm.ru"
    return f"https://{host}/leads/detail/{int(lead_id)}"


def _text_field(field_id: int, value: str) -> dict:
    return {"field_id": int(field_id), "values": [{"value": str(value)}]}


def _enum_field(field_id: int, enum_id: int) -> dict:
    return {"field_id": int(field_id), "values": [{"enum_id": int(enum_id)}]}


def find_contact(phone: str) -> dict | None:
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    if len(digits) < 10:
        return None
    query = digits[-10:]
    code, data = request("/api/v4/contacts", {"query": query, "limit": "10", "with": "leads"})
    if code >= 400 or not data:
        return None
    # CRM query is fuzzy: never trust the first search result.
    if (data.get("_links") or {}).get("next"):
        return None
    matches = [c for c in items(data, "contacts") if contact_has_phone(c, phone)]
    return matches[0] if len(matches) == 1 else None


def open_leads_of(contact: dict) -> list[dict]:
    leads = items(contact.get("_embedded") or {}, "leads")
    out: list[dict] = []
    for row in leads:
        lead_id = row.get("id")
        if not lead_id:
            continue
        code, lead = request(f"/api/v4/leads/{lead_id}")
        if code >= 400 or not lead:
            continue
        pipe = lead.get("pipeline_id")
        if pipe not in {PIPELINE_SALES, PIPELINE_TECH}:
            continue
        if lead.get("status_id") in CLOSED:
            continue
        out.append(lead)
    out.sort(key=lambda x: int(x.get("updated_at") or 0), reverse=True)
    return out


def get_lead(lead_id: int) -> dict:
    return get(f"/api/v4/leads/{int(lead_id)}", {"with": "contacts"})


def create_contact(name: str, phone: str, *, marker: str = "") -> int:
    item: dict = {
        "name": (name or "Клиент DIVO").strip() or "Клиент DIVO",
        "responsible_user_id": BOT_RESPONSIBLE,
    }
    formatted = amo_phone(phone)
    if formatted:
        item["custom_fields_values"] = [
            {
                "field_code": "PHONE",
                "values": [{"value": formatted, "enum_code": "WORK"}],
            }
        ]
    if marker:
        item["_embedded"] = {"tags": [{"name": marker}]}
    data = write("/api/v4/contacts", [item], attempts=1 if marker else 3)
    rows = items(data, "contacts")
    if not rows or not rows[0].get("id"):
        raise AmoError(0, "контакт не создался")
    return int(rows[0]["id"])


def create_lead(
    *,
    name: str,
    contact_id: int | None,
    price: int = 0,
    source_enum: int | None,
    fields: dict[int, str] | None = None,
    marker: str = "",
) -> dict:
    custom = []
    if source_enum:
        custom.append(_enum_field(FIELD_SOURCE, source_enum))
    for field_id, value in (fields or {}).items():
        if value:
            custom.append(_text_field(field_id, value))
    item: dict = {
        "name": name,
        "pipeline_id": PIPELINE_SALES,
        "status_id": STATUS_NEW,
        "responsible_user_id": BOT_RESPONSIBLE,
    }
    if price:
        item["price"] = int(price)
    if custom:
        item["custom_fields_values"] = custom
    if contact_id:
        item["_embedded"] = {"contacts": [{"id": int(contact_id), "is_main": True}]}
    if marker:
        item.setdefault("_embedded", {})["tags"] = [{"name": marker}]
    data = write("/api/v4/leads", [item], attempts=1 if marker else 3)
    rows = items(data, "leads")
    if not rows or not rows[0].get("id"):
        raise AmoError(0, "сделка не создалась")
    lead_id = int(rows[0]["id"])
    return get_lead(lead_id)


def _fold_name(value: str) -> str:
    """El’zar и Эльзар должны стать одним ключом: без апострофа, ё=е."""
    text = (value or "").strip().lower().replace("ё", "е")
    text = re.sub(r"['’‘`ʼʻ]", "", text)
    text = re.sub(r"[^a-zа-я0-9]+", " ", text)
    return " ".join(text.split())


def _norm_name(value: str) -> str:
    folded = _fold_name(value)
    if not folded:
        return ""
    if folded in NAME_ALIAS:
        return NAME_ALIAS[folded]
    first = folded.split()[0]
    return NAME_ALIAS.get(first, first)


def _tg_map_from_env() -> dict[int, int]:
    out = dict(AMO_USER_BY_TG)
    raw = os.environ.get("AMO_TG_MAP") or ""
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if ":" not in chunk:
            continue
        left, right = chunk.split(":", 1)
        try:
            out[int(left.strip())] = int(right.strip())
        except ValueError:
            continue
    return out


def list_users() -> list[dict]:
    global _users_cache
    now = time.time()
    if _users_cache and now - _users_cache[0] < 300:
        return _users_cache[1]
    try:
        rows = items(get("/api/v4/users", {"limit": "250"}), "users")
    except Exception:
        rows = []
    users = rows or list(KNOWN_USERS)
    _users_cache = (now, users)
    return users


def match_amo_user(
    users: list[dict],
    *,
    tg_id: int = 0,
    first: str = "",
    last: str = "",
    username: str = "",
    tg_map: dict[int, int] | None = None,
) -> int | None:
    """Кто нажал кнопку в Telegram → id ответственного в amo."""
    mapped = (tg_map or AMO_USER_BY_TG).get(int(tg_id or 0))
    if mapped:
        return int(mapped)
    nick = (username or "").lstrip("@").lower()
    if nick and nick in AMO_USER_BY_NICK:
        return int(AMO_USER_BY_NICK[nick])
    needle = _norm_name(first) or _norm_name(last)
    if len(needle) < 2:
        return None
    hits: list[int] = []
    for user in users or []:
        uid = user.get("id")
        name = _norm_name(str(user.get("name") or ""))
        if not uid or not name:
            continue
        if name == needle or needle in str(user.get("name") or "").lower().replace("ё", "е"):
            hits.append(int(uid))
    if len(hits) == 1:
        return hits[0]
    return None


def resolve_responsible(
    *,
    tg_id: int = 0,
    first: str = "",
    last: str = "",
    username: str = "",
) -> int | None:
    return match_amo_user(
        list_users(),
        tg_id=tg_id,
        first=first,
        last=last,
        username=username,
        tg_map=_tg_map_from_env(),
    )


def user_name(user_id: int | None) -> str:
    if not user_id:
        return ""
    for user in list_users():
        if int(user.get("id") or 0) == int(user_id):
            return str(user.get("name") or "")
    return ""


def set_lead_status(
    lead_id: int,
    status_id: int,
    responsible_user_id: int | None = None,
) -> dict:
    body: dict = {
        "id": int(lead_id),
        "pipeline_id": PIPELINE_SALES,
        "status_id": int(status_id),
    }
    if responsible_user_id:
        body["responsible_user_id"] = int(responsible_user_id)
    write("/api/v4/leads", [body], method="PATCH")
    return get_lead(int(lead_id))


def lead_calls_since(lead_id: int, since_ts: int) -> list[dict]:
    """Входящие и исходящие звонки по сделке после since_ts (unix)."""
    code, data = request(
        "/api/v4/leads/%s/notes" % int(lead_id),
        {"limit": "50"},
    )
    if code >= 400 or not data:
        return []
    out = []
    start = int(since_ts or 0)
    for note in items(data, "notes"):
        if note.get("note_type") not in {"call_in", "call_out"}:
            continue
        created = int(note.get("created_at") or 0)
        if created >= start:
            out.append(note)
    return out


def add_note(lead_id: int, text: str) -> None:
    body = [{"note_type": "common", "params": {"text": (text or "")[:9000]}}]
    write(f"/api/v4/leads/{int(lead_id)}/notes", body)


GENERIC_PEERS = {"пользователь", "клиент", "user", "клиент divo", "гость"}
TITLE_STOP = {
    "at", "amt", "cvt", "км", "л", "л.с", "лс", "класс", "class", "the",
    "авто", "ру", "авито", "продаж", "новый", "новые",
}


def _norm_txt(value: str) -> str:
    text = (value or "").lower().replace("ё", "е")
    text = text.replace("[авто.ру]", " ").replace("[авито]", " ")
    text = re.sub(r"[^a-zа-я0-9]+", " ", text)
    return " ".join(text.split())


def _tokens(value: str) -> set[str]:
    return {t for t in _norm_txt(value).split() if len(t) >= 2 and t not in TITLE_STOP}


def score_widget_lead(
    lead_name: str,
    *,
    peer: str = "",
    car: str = "",
    channel: str = "",
    from_name: str = "",
    created_at: int = 0,
    now: float | None = None,
) -> int:
    """Насколько сделка виджета похожа на наш чат. 0 = мимо."""
    # Similarity and names cannot establish buyer identity.
    return 0


def _lead_id_of_unsorted(row: dict) -> int | None:
    for lead in items(row, "leads") or ((row.get("_embedded") or {}).get("leads") or []):
        lid = lead.get("id")
        if lid:
            return int(lid)
    return None


def iter_unsorted(max_pages: int = 5) -> list[dict]:
    """Неразобранное воронки «Техническое»: uid, from, source, lead_id."""
    out: list[dict] = []
    for page in range(1, max_pages + 1):
        code, data = request(
            "/api/v4/leads/unsorted",
            {
                "limit": "50",
                "page": str(page),
                "filter[pipeline_id]": str(PIPELINE_TECH),
            },
        )
        if code >= 400 or not data:
            break
        rows = items(data, "unsorted")
        if not rows:
            break
        out.extend(rows)
        if len(rows) < 50:
            break
        time.sleep(0.15)
    return out


_UNSORTED_SNAPSHOT: tuple[float, list[dict]] = (0, [])


def all_unsorted() -> list[dict]:
    """Complete, short-lived snapshot; never cache a partial CRM response."""
    global _UNSORTED_SNAPSHOT
    at, cached = _UNSORTED_SNAPSHOT
    if time.time() - at < 120:
        return cached
    out = []
    seen = set()
    for page in range(1, 101):
        code, data = request("/api/v4/leads/unsorted", {
            "limit": "250", "page": str(page),
            "filter[pipeline_id]": str(PIPELINE_TECH),
        })
        if code >= 400:
            raise AmoError(code, "incomplete unsorted snapshot")
        rows = items(data or {}, "unsorted")
        for row in rows:
            uid = str(row.get("uid") or "")
            if not uid or uid in seen:
                raise AmoError(0, "duplicate or missing unsorted identity")
            seen.add(uid)
            out.append(row)
        if not (data or {}).get("_links", {}).get("next"):
            _UNSORTED_SNAPSHOT = (time.time(), out)
            return out
        if not rows:
            break
    raise AmoError(0, "unsorted pagination incomplete")


def provider_bound_lead(channel: str, chat_id: str, customer_id: str = "", url: str = "") -> dict | None:
    """Resolve only immutable provider identity. Names/listing similarity are never identity.

    CRM origin.chat_id is usually an amojo UUID, not a marketplace chat ID.
    If the widget exposes no provider identity, keep the handoff pending.
    """
    room = str(chat_id or "").removeprefix("av:").removeprefix("ar:")
    if not room:
        return None
    wanted_item = listing_item_id(url)
    found = {}
    for row in all_unsorted():
        meta = unsorted_meta(row)
        if meta.get("channel") != channel:
            continue
        md = row.get("metadata") or {}
        origin = md.get("origin") or {}
        exact_room = room in {
            str(origin.get("chat_id") or ""),
            str(origin.get("conversation_id") or ""),
            str(md.get("provider_chat_id") or ""),
        }
        # Auto.ru's widget can put the opaque room/customer ID in `from`.
        # Human aliases and full names are deliberately not accepted.
        if channel == "Авто.ру" and str(md.get("from") or "") == room:
            exact_room = True
        exact_customer = bool(customer_id and str(md.get("provider_customer_id") or "") == str(customer_id))
        if channel == "Авто.ру" and customer_id and str(md.get("from") or "") == str(customer_id):
            exact_customer = True
        if not exact_room and not (exact_customer and wanted_item):
            continue
        lid = meta.get("lead_id")
        if not lid:
            continue
        lead = get_lead(int(lid))
        if lead.get("is_deleted") or lead.get("status_id") in CLOSED:
            continue
        if lead.get("pipeline_id") not in {PIPELINE_TECH, PIPELINE_SALES}:
            continue
        if wanted_item and listing_item_id(lead_listing_url(lead)) != wanted_item:
            continue
        source_contacts = {int(c["id"]) for c in (row.get("_embedded") or {}).get("contacts") or []}
        lead_contacts = {int(c["id"]) for c in (lead.get("_embedded") or {}).get("contacts") or []}
        if len(source_contacts) == 1 and source_contacts <= lead_contacts:
            lead["_provider_contact_id"] = next(iter(source_contacts))
        elif len(lead_contacts) != 1:
            continue
        lead["_unsorted_uid"] = meta.get("uid") or ""
        found[int(lid)] = lead
    return next(iter(found.values())) if len(found) == 1 else None


def matching_message_triples(provider: list[dict], crm_rows: list[dict]) -> list[list[dict]]:
    """Three consecutive distinct seconds, same direction; never skip a message."""
    def ordered(rows):
        seen = {}
        for row in rows:
            mid = str(row.get("id") or "")
            if not mid or row.get("direction") not in {"in", "out"} or not row.get("at"):
                raise ValueError("Incomplete message evidence")
            signature = (int(row["at"]), row["direction"])
            if mid in seen and seen[mid] != signature:
                raise ValueError("Conflicting message identity")
            seen[mid] = signature
        return sorted((at, direction, mid) for mid, (at, direction) in seen.items())
    left, right = ordered(provider), ordered(crm_rows)
    def windows(rows):
        counts = {}
        for at, _, _ in rows:
            counts[at] = counts.get(at, 0) + 1
        for i in range(len(rows) - 2):
            triple = rows[i:i+3]
            if any(counts[r[0]] != 1 for r in triple):
                continue  # Same-second ordering is not evidence.
            yield tuple((r[0], r[1]) for r in triple), triple
    index = dict(windows(right))
    matches = []
    for key, triple in windows(left):
        if key in index:
            matches.append([{"at": a[0], "direction": a[1], "provider_id": a[2], "crm_id": b[2]}
                            for a, b in zip(triple, index[key])])
    return matches


def avito_timed_message_lead(refs: list[dict], url: str) -> dict | None:
    """Owner-approved temporal correlation, not immutable provider identity.

    Complete event window, unique talk and listing, all directions included.
    No contact/phone search. Every read is across stages, with bounded pagination.
    """
    item = listing_item_id(url)
    if not item or len(refs) < 3:
        return None
    lo, hi = min(int(r["at"]) for r in refs), max(int(r["at"]) for r in refs)
    groups = {}
    for page in range(1, 21):
        code, data = request("/api/v4/events", {
            "filter[type]": "incoming_chat_message,outgoing_chat_message",
            "filter[created_at][from]": lo, "filter[created_at][to]": hi,
            "limit": 100, "page": page,
        }, attempts=1)
        if code not in (200, 204):
            raise AmoError(code, "Message correlation events unavailable")
        for event in items(data, "events"):
            typ = event.get("type")
            if typ not in {"incoming_chat_message", "outgoing_chat_message"}:
                continue
            for value in event.get("value_after") or []:
                msg = value.get("message") or {}
                if msg.get("origin") != "avito":
                    continue
                if not msg.get("talk_id") or not msg.get("id") or not event.get("created_at"):
                    raise AmoError(0, "Incomplete Avito event")
                groups.setdefault(int(msg["talk_id"]), []).append({
                    "id": str(msg["id"]), "at": int(event["created_at"]),
                    "direction": "in" if typ == "incoming_chat_message" else "out",
                })
        if not (data.get("_links") or {}).get("next"):
            break
    else:
        raise AmoError(0, "Message correlation pagination incomplete")
    candidates = []
    for talk_id, rows in groups.items():
        triples = matching_message_triples(refs, rows)
        if not triples:
            continue
        code, talk = request("/api/v4/talks/%s" % talk_id, attempts=1)
        if code != 200:
            raise AmoError(code, "Candidate talk unavailable")
        if talk.get("origin") != "avito" or talk.get("entity_type") != "lead" or not talk.get("contact_id"):
            raise AmoError(0, "Candidate talk identity unavailable")
        lead = get_lead(int(talk["entity_id"]))
        if not lead or not lead.get("id"):
            raise AmoError(0, "Candidate lead unavailable")
        lead_item = listing_item_id(lead_listing_url(lead))
        if not lead_item:
            raise AmoError(0, "Candidate listing unavailable")
        if lead_item != item:
            continue
        if lead.get("is_deleted") or lead.get("status_id") in CLOSED:
            continue
        contacts = {int(c["id"]) for c in lead.get("_embedded", {}).get("contacts", [])}
        if lead.get("pipeline_id") not in {PIPELINE_TECH, PIPELINE_SALES} or contacts != {int(talk["contact_id"])}:
            return None
        lead["_provider_contact_id"] = int(talk["contact_id"])
        lead["_provider_talk_id"] = talk_id
        lead["_message_time_evidence"] = triples[-1]
        candidates.append(lead)
    return candidates[0] if len(candidates) == 1 else None


def autoru_message_lead(refs: list[dict], url: str) -> dict | None:
    """Resolve an integration reply by its exact CRM message UUID, never by name.

    Auto.ru echoes the CRM outgoing message ID as provided_id. Incoming
    provided_id is NOT a CRM ID and must never be submitted by the caller.
    Search all stages through events/talks, including merged/moved leads.
    """
    item = listing_item_id(url)
    wanted = {str(r.get("id")): int(r.get("at") or 0) for r in refs
              if r.get("id") and r.get("at")}
    if not item or not wanted:
        return None
    matches = {}
    for page in range(1, 21):
        code, data = request("/api/v4/events", {
            "filter[type]": "outgoing_chat_message",
            "filter[created_at][from]": min(wanted.values()) - 120,
            "filter[created_at][to]": max(wanted.values()) + 120,
            "limit": 100, "page": page,
        })
        if code >= 400:
            raise AmoError(code, "CRM message identity unavailable")
        for event in items(data or {}, "events"):
            if event.get("type") != "outgoing_chat_message" or event.get("entity_type") not in {"contact", "lead"}:
                continue
            for value in event.get("value_after") or []:
                msg = value.get("message") or {}
                if (str(msg.get("id")) not in wanted or msg.get("origin") != "amo.ext.31941682"
                        or not msg.get("talk_id")):
                    continue
                code, talk = request("/api/v4/talks/%s" % int(msg["talk_id"]))
                if code != 200:
                    raise AmoError(code, "CRM talk identity unavailable")
                contact = (event.get("entity_id") if event.get("entity_type") == "contact" else
                           ((event.get("_embedded") or {}).get("entity") or {}).get("linked_talk_contact_id"))
                if (talk.get("entity_type") != "lead" or talk.get("origin") != msg["origin"]
                        or int(talk.get("contact_id") or 0) != int(contact or 0)):
                    return None
                if event.get("entity_type") == "lead" and int(event["entity_id"]) != int(talk["entity_id"]):
                    return None  # A moved/merged event needs fresh reconciliation, not a guess.
                lead = get_lead(int(talk["entity_id"]))
                contacts = {int(c["id"]) for c in (lead.get("_embedded") or {}).get("contacts", [])}
                if (lead.get("is_deleted") or lead.get("status_id") in CLOSED
                        or lead.get("pipeline_id") not in {PIPELINE_TECH, PIPELINE_SALES}
                        or talk["contact_id"] not in contacts
                        or listing_item_id(lead_listing_url(lead)) != item):
                    return None
                lead["_provider_contact_id"] = int(talk["contact_id"])
                lead["_provider_talk_id"] = int(msg["talk_id"])
                lead["_provider_message_id"] = str(msg["id"])
                matches[(lead["id"], talk["contact_id"], msg["talk_id"])] = lead
        if not (data or {}).get("_links", {}).get("next"):
            return next(iter(matches.values())) if len(matches) == 1 else None
    raise AmoError(0, "CRM message identity pagination incomplete")


def autoru_bound_lead(room_id: str, customer_id: str = "", url: str = "", customer_names: list[str] | None = None) -> int | None:
    # customer_names kept only for call compatibility; never used for matching.
    lead = provider_bound_lead("Авто.ру", room_id, customer_id, url)
    return int(lead["id"]) if lead else None


def unsorted_meta(row: dict) -> dict:
    md = row.get("metadata") or {}
    client = md.get("client") or {}
    source_uid = str(row.get("source_uid") or "")
    channel = ""
    if "avito" in source_uid:
        channel = "Авито"
    elif "amo.ext" in source_uid:
        channel = "Авто.ру"
    return {
        "uid": row.get("uid") or "",
        "lead_id": _lead_id_of_unsorted(row),
        "from": str(md.get("from") or client.get("name") or ""),
        "channel": channel,
        "created_at": int(row.get("created_at") or 0),
        "source_uid": source_uid,
    }


def autoru_unsorted_lead(rows: list[dict], room_id: str) -> int | None:
    """Сделка интеграции Авто.ру по id чата кабинета.

    В неразобранном amo кладёт id комнаты в metadata.from.
    Если комната попала в две карточки, берём ту, что создана раньше.
    """
    needle = (room_id or "").strip()
    if not needle:
        return None
    found: list[tuple[int, int]] = []
    for row in rows or []:
        meta = unsorted_meta(row)
        if meta.get("channel") != "Авто.ру":
            continue
        if str(meta.get("from") or "") != needle:
            continue
        lid = meta.get("lead_id")
        if lid:
            found.append((int(meta.get("created_at") or 0), int(lid)))
    if not found:
        return None
    ids = {lid for _, lid in found}
    return next(iter(ids)) if len(ids) == 1 else None


def listing_item_id(url: str) -> str:
    """Provider listing identity; optional brand/model paths are not IDs."""
    from urllib.parse import urlsplit
    parsed = urlsplit((url or "").strip())
    if parsed.hostname in {"auto.ru", "www.auto.ru"}:
        match = re.search(r"/(\d{6,}-[a-zA-Z0-9]+)/?$", parsed.path)
        if match:
            return match.group(1).lower()
    match = re.search(r"(\d{6,})/?$", parsed.path)
    return match.group(1) if match else ""


def lead_listing_url(lead: dict) -> str:
    for field in lead.get("custom_fields_values") or []:
        if int(field.get("field_id") or 0) != FIELD_LISTING_URL:
            continue
        for val in field.get("values") or []:
            text = str(val.get("value") or "").strip()
            if text:
                return text
    return ""


def different_buyer(peer: str, other: str) -> bool:
    """Два явных имени и это не один человек.

    Пустое имя и «Пользователь» не считаем другим клиентом: так виджет
    подписывает дубль того же чата. «Василий» и «Кирилл» по одному объявлению
    это разные покупатели.
    """
    peer_n = _norm_txt(peer)
    other_n = _norm_txt(other)
    if not peer_n or peer_n in GENERIC_PEERS:
        return False
    if not other_n or other_n in GENERIC_PEERS:
        return False
    return not (peer_n == other_n or peer_n in other_n or other_n in peer_n)


def _buyer_name(cand: dict) -> str:
    lead = cand.get("lead") or {}
    return str(cand.get("from_name") or lead.get("_widget_from") or "")


def choose_widget_lead(
    candidates: list[dict], *, item_id: str = "", peer: str = ""
) -> dict | None:
    """Legacy fuzzy selector disabled. Use provider_bound_lead instead."""
    return None


def _original_talk(cluster: list[dict]) -> dict:
    def created(cand: dict) -> int:
        return int(cand.get("created_at") or 0) or 10**18

    def talk_created(cand: dict) -> int:
        return int(cand.get("talk_created") or 0)

    def talk_updated(cand: dict) -> int:
        return int(cand.get("talk_updated") or 0)

    with_talks = [c for c in cluster if talk_created(c)]
    if not with_talks:
        return min(cluster, key=created)
    live: list[dict] = []
    for cand in with_talks:
        siblings = [created(other) for other in cluster if other is not cand]
        later = max(siblings) if siblings else 0
        if talk_updated(cand) >= later:
            live.append(cand)
    pick_from = live or with_talks
    return min(pick_from, key=lambda c: (talk_created(c) or created(c), created(c)))


def lead_talk_span(lead_id: int) -> tuple[int, int]:
    """Когда на сделке начался и когда обновлялся разговор. (0, 0), если беседы нет."""
    try:
        code, data = request(
            "/api/v4/talks",
            {
                "filter[entity_id]": str(int(lead_id)),
                "filter[entity_type]": "lead",
                "limit": "20",
            },
        )
    except AmoError:
        return 0, 0
    if code >= 400 or not data:
        return 0, 0
    talks = items(data, "talks")
    if not talks:
        return 0, 0
    created_vals = [int(t.get("created_at") or 0) for t in talks if int(t.get("created_at") or 0)]
    updated_vals = [int(t.get("updated_at") or 0) for t in talks]
    return (min(created_vals) if created_vals else 0, max(updated_vals) if updated_vals else 0)


def _widget_cluster(candidates: list[dict], item_id: str, peer: str = "") -> list[dict]:
    """Те же карточки, между которыми выбирает choose_widget_lead."""
    pool = [c for c in candidates if int(c.get("score") or 0) >= 40 and c.get("lead")]
    pool = [c for c in pool if not different_buyer(peer, _buyer_name(c))]
    if not pool:
        return []
    want = (item_id or "").strip()
    same = [c for c in pool if want and str(c.get("item_id") or "") == want]
    if same:
        return same
    strangers = [
        c for c in candidates
        if int(c.get("score") or 0) >= 40 and c.get("lead")
        and want and str(c.get("item_id") or "") == want
        and different_buyer(peer, _buyer_name(c))
    ]
    if strangers:
        return []
    best = max(int(c.get("score") or 0) for c in pool)
    return [c for c in pool if int(c.get("score") or 0) == best]


def find_unsorted_uid(lead_id: int, rows: list[dict] | None = None) -> str:
    needle = int(lead_id)
    for row in rows or iter_unsorted(8):
        if _lead_id_of_unsorted(row) == needle:
            return str(row.get("uid") or "")
    return ""


def accept_unsorted(uid: str, status_id: int = STATUS_TECH_CHAT) -> None:
    if not uid:
        return
    body = {"user_id": BOT_RESPONSIBLE, "status_id": int(status_id)}
    code, data = request(
        "/api/v4/leads/unsorted/%s/accept" % uid,
        method="POST",
        body=body,
    )
    if code >= 400:
        raise AmoError(code, str(data.get("detail") or data.get("title") or data)[:300])


def get_contact(contact_id: int) -> dict:
    return get("/api/v4/contacts/%s" % int(contact_id))


def contact_has_phone(contact: dict, phone: str) -> bool:
    want = "".join(ch for ch in amo_phone(phone) if ch.isdigit())
    if not want:
        return False
    tail = want[-10:]
    for field in contact.get("custom_fields_values") or []:
        code = str(field.get("field_code") or "").upper()
        name = str(field.get("field_name") or "").lower()
        if code != "PHONE" and "телефон" not in name:
            continue
        for val in field.get("values") or []:
            have = "".join(ch for ch in str(val.get("value") or "") if ch.isdigit())
            if not have:
                continue
            if have == want or have[-10:] == tail:
                return True
    return False


def link_contact(lead_id: int, contact_id: int) -> None:
    write(
        "/api/v4/leads",
        [
            {
                "id": int(lead_id),
                "_embedded": {"contacts": [{"id": int(contact_id), "is_main": True}]},
            }
        ],
        method="PATCH",
    )


def set_contact_phone(contact_id: int, phone: str) -> None:
    formatted = amo_phone(phone)
    if int(contact_id or 0) <= 0 or not formatted:
        return
    contact = get_contact(contact_id)
    if contact_has_phone(contact, phone):
        return
    values = []
    for field in contact.get("custom_fields_values") or []:
        if field.get("field_code") == "PHONE":
            values.extend({k: v for k, v in row.items() if k in {"value", "enum_id", "enum_code"}}
                          for row in field.get("values") or [])
    values.append({"value": formatted, "enum_code": "WORK"})
    write("/api/v4/contacts", [{"id": int(contact_id), "custom_fields_values": [
        {"field_code": "PHONE", "values": values}
    ]}], method="PATCH")


def move_lead_to_sales(
    lead_id: int,
    *,
    status_id: int = STATUS_NEW,
    responsible_user_id: int | None = BOT_RESPONSIBLE,
    price: int = 0,
    fields: dict[int, str] | None = None,
    unsorted_uid: str = "",
) -> dict:
    """Техническое / неразобранное → Продажи, «Новая заявка»."""
    lead = get_lead(int(lead_id))
    original_name = str(lead.get("name") or "").strip()
    if int(lead.get("status_id") or 0) == STATUS_TECH_UNSORTED:
        uid = unsorted_uid or find_unsorted_uid(int(lead_id))
        if uid:
            try:
                accept_unsorted(uid, status_id=int(status_id))
            except AmoError:
                try:
                    accept_unsorted(uid)
                except AmoError:
                    pass
            lead = get_lead(int(lead_id))
    custom = []
    for field_id, value in (fields or {}).items():
        if value:
            custom.append(_text_field(field_id, value))
    body: dict = {
        "id": int(lead_id),
        "pipeline_id": PIPELINE_SALES,
        "status_id": int(status_id),
    }
    if responsible_user_id:
        body["responsible_user_id"] = int(responsible_user_id)
    if price:
        body["price"] = int(price)
    if custom:
        body["custom_fields_values"] = custom
    if original_name and original_name not in {"|", "-"}:
        body["name"] = original_name
    write("/api/v4/leads", [body], method="PATCH")
    return get_lead(int(lead_id))


def close_lead_spam(lead_id: int, note: str = "") -> None:
    body = {
        "id": int(lead_id),
        "pipeline_id": PIPELINE_SALES,
        "status_id": STATUS_SPAM,
    }
    write("/api/v4/leads", [body], method="PATCH")
    if note:
        add_note(int(lead_id), note)


def _lead_as_widget(lead_id: int, rows: list[dict]) -> dict | None:
    lead = get_lead(int(lead_id))
    if int(lead.get("status_id") or 0) in CLOSED:
        return None
    lead["_unsorted_uid"] = find_unsorted_uid(int(lead_id), rows)
    return lead


def exact_chat_marker_lead(chat_id: str, url: str, contact_id: int) -> dict | None:
    """Recover a moved/replaced bot-owned lead, never a similar customer.

    Caller must already have a verified binding. Require its same contact,
    exact full-chat marker, listing, and a unique live target across all stages.
    """
    import hashlib
    if not chat_id.startswith(("av:", "ar:")) or not contact_id or not listing_item_id(url):
        return None
    marker = "DIVO_CHAT_" + hashlib.sha256(chat_id.encode()).hexdigest()[:32]
    found = {}
    for page in range(1, 21):
        code, data = request("/api/v4/leads", {"query": marker, "with": "contacts", "limit": 250, "page": page})
        if code not in (200, 204):
            raise AmoError(code, "Chat marker lookup unavailable")
        for lead in items(data, "leads"):
            if marker not in {t.get("name") for t in lead.get("_embedded", {}).get("tags", [])}:
                continue
            if lead.get("is_deleted") or lead.get("status_id") in CLOSED:
                continue
            if lead.get("pipeline_id") not in {PIPELINE_TECH, PIPELINE_SALES}:
                continue
            contacts = {int(c["id"]) for c in lead.get("_embedded", {}).get("contacts", [])}
            if contacts != {int(contact_id)} or listing_item_id(lead_listing_url(lead)) != listing_item_id(url):
                return None
            lead["_provider_contact_id"] = int(contact_id)
            found[int(lead["id"])] = lead
        if not (data.get("_links") or {}).get("next"):
            return next(iter(found.values())) if len(found) == 1 else None
    raise AmoError(0, "Chat marker pagination incomplete")


def find_widget_lead(
    *, peer: str = "", car: str = "", channel: str = "", url: str = "",
    chat_id: str = "", customer_id: str = "", customer_names: list[str] | None = None,
) -> dict | None:
    # Names and car titles are display data, not identity evidence.
    if channel not in {"Авито", "Авто.ру"}:
        return None
    return provider_bound_lead(channel, chat_id, customer_id, url)


def owned_entity(kind: str, marker: str, since: int) -> dict | None:
    """Reconcile a write intent by its exact tag; names/phones never identify a chat."""
    matches = []
    params = {"filter[created_at][from]": max(0, since - 300), "limit": 250}
    if kind == "leads":
        params["with"] = "contacts"
    for row in paged("/api/v4/" + kind, params, kind):
        if any(t.get("name") == marker for t in (row.get("_embedded") or {}).get("tags", [])):
            matches.append(row)
    if len(matches) > 1:
        raise AmoError(0, "Ambiguous chat marker; automatic binding blocked")
    return matches[0] if matches else None
