"""Клиент amoCRM для DIVO: чтение и запись сделки.

Токен берётся из .env проекта или из DIVO_Motors/06_Доступы/.env.
"""
from __future__ import annotations

import json
import os
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
PIPELINE_SALES = 10372290

# Telegram → amo. Имена сверки с /api/v4/users 2026-09-14.
AMO_USER_BY_TG = {
    435207481: 9490530,  # Максим Шлыков
    212666249: 13334858,  # Никита Яменский
    821842895: 12826002,  # Николас
    282491919: 13180098,  # Евгений
}
AMO_USER_BY_NICK = {
    "maxim_shlykov": 9490530,
    "msp_msproduct": 9490530,
    "nikita_yamenskii": 13334858,
    "nikolas413": 12826002,
    "vladimir0vich1": 13180098,
}
NAME_ALIAS = {
    "женя": "евгений",
    "evgeniy": "евгений",
    "evgeny": "евгений",
    "eugene": "евгений",
    "elzar": "эльзар",
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
FIELD_SOURCE = 2026903
FIELD_VIN = 2027421
FIELD_BRAND = 2027423
FIELD_MODEL = 2027425
FIELD_YEAR = 2027427
FIELD_KM = 2051185
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
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=40) as resp:
                raw = resp.read()
                if resp.status == 204 or not raw:
                    return resp.status, {}
                return resp.status, json.loads(raw)
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", "replace")
            if exc.code == 429 and attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            try:
                data = json.loads(body_text)
            except json.JSONDecodeError:
                data = {"detail": body_text[:400]}
            return exc.code, data
        except urllib.error.URLError as exc:
            if attempt < 2:
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


def write(path: str, body: object, method: str = "POST") -> dict:
    code, data = request(path, method=method, body=body)
    if code >= 400:
        raise AmoError(code, str(data.get("detail") or data.get("title") or data)[:300])
    return data


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
    rows = items(data, "contacts")
    return rows[0] if rows else None


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
        if lead.get("pipeline_id") != PIPELINE_SALES:
            continue
        if lead.get("status_id") in CLOSED:
            continue
        out.append(lead)
    out.sort(key=lambda x: int(x.get("updated_at") or 0), reverse=True)
    return out


def get_lead(lead_id: int) -> dict:
    return get(f"/api/v4/leads/{int(lead_id)}")


def create_contact(name: str, phone: str) -> int:
    body = [
        {
            "name": (name or "Клиент DIVO").strip() or "Клиент DIVO",
            "responsible_user_id": NIKITA_USER_ID,
            "custom_fields_values": [
                {
                    "field_code": "PHONE",
                    "values": [{"value": phone, "enum_code": "WORK"}],
                }
            ],
        }
    ]
    data = write("/api/v4/contacts", body)
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
        "responsible_user_id": NIKITA_USER_ID,
    }
    if price:
        item["price"] = int(price)
    if custom:
        item["custom_fields_values"] = custom
    if contact_id:
        item["_embedded"] = {"contacts": [{"id": int(contact_id), "is_main": True}]}
    data = write("/api/v4/leads", [item])
    rows = items(data, "leads")
    if not rows or not rows[0].get("id"):
        raise AmoError(0, "сделка не создалась")
    lead_id = int(rows[0]["id"])
    return get_lead(lead_id)


def _norm_name(value: str) -> str:
    text = (value or "").strip().lower().replace("ё", "е")
    return NAME_ALIAS.get(text, text.split()[0] if text else "")


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
    if not needle:
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
