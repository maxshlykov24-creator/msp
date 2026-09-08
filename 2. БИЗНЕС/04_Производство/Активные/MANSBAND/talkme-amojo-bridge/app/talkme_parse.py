from __future__ import annotations

import uuid
from typing import Any, Optional, Tuple
from urllib.parse import parse_qs, urlparse

UTM_KEYS = (
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_referrer",
)


def parse_talkme_incoming(d: dict[str, Any]) -> Tuple[Optional[str], str, str, str, Optional[str], Optional[str]]:
    """
    Пытается вытащить: secret, dialog_id, text, msg_id, visitor_name, visitor_external_id.
    Формат Talk-me может отличаться — при первом реальном хуке смотрите лог.
    """
    sec = d.get("secret")
    if sec is not None and not isinstance(sec, str):
        sec = str(sec)
    if sec is None:
        for k in ("secretKey", "key", "webhookSecret", "webhook_key"):
            if k in d and isinstance(d[k], str):
                sec = d[k]
                break

    # Частые варианты вложенности
    data = d.get("data")
    if not isinstance(data, dict):
        data = d
    if isinstance(d.get("payload"), dict):
        data = d["payload"]

    dialog_id = None
    for key in (
        "dialogId",
        "dialog_id",
        "chatId",
        "chat_id",
        "sessionId",
        "conversationId",
    ):
        if key in data:
            dialog_id = str(data[key])
            break
    if not dialog_id and isinstance(data.get("dialog"), dict):
        d0 = data["dialog"]
        if "id" in d0:
            dialog_id = str(d0["id"])
    if not dialog_id and isinstance(d.get("dialog"), dict) and "id" in d["dialog"]:  # type: ignore[index]
        dialog_id = str(d["dialog"]["id"])  # type: ignore[index]

    text = ""
    for key in ("text", "message", "content", "body"):
        v = data.get(key)
        if isinstance(v, str) and v.strip():
            text = v
            break
        if isinstance(v, dict) and isinstance(v.get("text"), str):
            text = v["text"]
            break
    if not text:
        m = d.get("message")
        if isinstance(m, str):
            text = m
    if not text and isinstance(data.get("message"), dict):
        m0 = data["message"]
        c0 = m0.get("content")
        if isinstance(c0, dict):
            t0 = c0.get("text")
            if isinstance(t0, str) and t0.strip():
                text = t0
        if not text:
            t0 = m0.get("text")
            if isinstance(t0, str) and t0.strip():
                text = t0

    # newOfflineQuestion / dialogStarted / clientFillForm*: текст в data.dialog.messages[-1] или data.messages[-1]
    def _last_client_msg(arr: Any) -> Optional[dict[str, Any]]:
        if not isinstance(arr, list):
            return None
        for m in reversed(arr):
            if isinstance(m, dict) and m.get("whoSend") == "client":
                return m
        for m in reversed(arr):
            if isinstance(m, dict):
                return m
        return None

    last_msg: Optional[dict[str, Any]] = None
    if not text:
        dlg = data.get("dialog") if isinstance(data.get("dialog"), dict) else None
        if dlg is not None:
            last_msg = _last_client_msg(dlg.get("messages"))
    if not text and last_msg is None:
        last_msg = _last_client_msg(data.get("messages"))
    if not text and isinstance(last_msg, dict):
        c0 = last_msg.get("content") if isinstance(last_msg.get("content"), dict) else None
        if c0 and isinstance(c0.get("text"), str) and c0["text"].strip():
            text = c0["text"]
        elif isinstance(last_msg.get("text"), str) and last_msg["text"].strip():
            text = last_msg["text"]

    msg_id = None
    for key in ("messageId", "message_id", "uuid", "messageUuid"):
        v = data.get(key)
        if v is not None and isinstance(v, (str, int)):
            msg_id = str(v)
            break
    if not msg_id and isinstance(data.get("message"), dict):
        mid = data["message"].get("id")
        if mid is not None and isinstance(mid, (str, int)):
            msg_id = str(mid)
    if not msg_id and isinstance(last_msg, dict):
        mid = last_msg.get("id") or last_msg.get("tag")
        if mid is not None and isinstance(mid, (str, int)):
            msg_id = str(mid)
    if not msg_id:
        msg_id = str(uuid.uuid4())

    name = None
    if isinstance(data.get("visitor"), dict):
        name = (data["visitor"] or {}).get("name")
    if isinstance(name, str):
        pass
    else:
        name = data.get("visitorName") if isinstance(data.get("visitorName"), str) else None

    ext = None
    if isinstance(data.get("visitor"), dict):
        ext = (data["visitor"] or {}).get("externalId") or (data["visitor"] or {}).get("id")
    if ext is not None:
        ext = str(ext)

    if not dialog_id:
        dialog_id = "unknown-" + str(uuid.uuid4())[:8]

    return (sec, dialog_id, text, msg_id, name, ext)


def talkme_incoming_has_attachments(d: dict[str, Any]) -> bool:
    """Есть ли во входящем вебхуке вложения/стикер в message.content
    или в последнем сообщении dialog.messages / messages."""
    data = d.get("data") if isinstance(d.get("data"), dict) else d
    if isinstance(d.get("payload"), dict):
        data = d["payload"]

    def _has_in(content: Any) -> bool:
        if not isinstance(content, dict):
            return False
        att = content.get("attachments")
        if isinstance(att, list) and len(att) > 0:
            return True
        if content.get("sticker"):
            return True
        return False

    msg = data.get("message")
    if isinstance(msg, dict) and _has_in(msg.get("content")):
        return True

    for arr_key in ("messages",):
        arr = data.get(arr_key)
        if isinstance(arr, list):
            for m in reversed(arr):
                if isinstance(m, dict) and _has_in(m.get("content")):
                    return True
                break
    dlg = data.get("dialog")
    if isinstance(dlg, dict):
        arr = dlg.get("messages")
        if isinstance(arr, list):
            for m in reversed(arr):
                if isinstance(m, dict) and _has_in(m.get("content")):
                    return True
                break
    return False


def parse_talkme_profile(d: dict[str, Any]) -> dict[str, Optional[str]]:
    """Профиль посетителя из вебхука Talk-me.

    Ключи: name, phone, email, profile_link, client_id — строки или None.
    """
    data = d.get("data") if isinstance(d.get("data"), dict) else d
    if isinstance(d.get("payload"), dict):
        data = d["payload"]

    client = data.get("client") if isinstance(data.get("client"), dict) else {}
    visitor = data.get("visitor") if isinstance(data.get("visitor"), dict) else {}

    def _s(v: Any) -> Optional[str]:
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    name = (
        _s(client.get("name"))
        or _s(visitor.get("name"))
        or _s(data.get("visitorName"))
    )
    if not name:
        first = _s(client.get("firstName")) or _s(visitor.get("firstName"))
        last = _s(client.get("lastName")) or _s(visitor.get("lastName"))
        joined = " ".join(x for x in (first, last) if x)
        name = joined or None

    phone = _s(client.get("phone")) or _s(visitor.get("phone"))
    email = _s(client.get("email")) or _s(visitor.get("email"))
    profile_link = _s(client.get("infoLink")) or _s(client.get("profileLink"))
    client_id = (
        _s(client.get("clientId"))
        or _s(client.get("id"))
        or _s(data.get("clientId"))
    )

    return {
        "name": name,
        "phone": phone,
        "email": email,
        "profile_link": profile_link,
        "client_id": client_id,
    }


def normalize_phone(raw: Any) -> Optional[str]:
    """Последние 10 цифр номера — ключ сопоставления с контактом amoCRM."""
    if raw is None:
        return None
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    if len(digits) < 10:
        return None
    return digits[-10:]


def parse_talkme_tracking(d: dict[str, Any]) -> dict[str, Optional[str]]:
    """Рекламные метки визита из вебхука Talk-me.

    Ключи: utm_source, utm_medium, utm_campaign, utm_term, utm_content, utm_referrer,
    roistat, yclid, referer, landing_url, search_keyword — строки или None.

    Что где лежит (проверено на боевой базе моста 2026-09-08, 190 бесед):
    utm — объект в client.utm; идентификатор визита Roistat — client.roistatVisitId (94% бесед);
    yclid / ysclid — только в URL входа client.lastVisit.page.url.
    """
    data = d.get("data") if isinstance(d.get("data"), dict) else d
    if isinstance(d.get("payload"), dict):
        data = d["payload"]

    client = data.get("client") if isinstance(data.get("client"), dict) else {}

    def _s(v: Any) -> Optional[str]:
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    out: dict[str, Optional[str]] = {}

    utm = client.get("utm") if isinstance(client.get("utm"), dict) else {}
    for k in UTM_KEYS:
        out[k] = _s(utm.get(k))

    out["roistat"] = _s(client.get("roistatVisitId"))
    out["referer"] = _s(client.get("referer"))
    out["search_keyword"] = _s(client.get("searchKeyword"))

    last_visit = client.get("lastVisit") if isinstance(client.get("lastVisit"), dict) else {}
    page = last_visit.get("page") if isinstance(last_visit.get("page"), dict) else {}
    url = _s(page.get("url"))
    if not url:
        page2 = data.get("page") if isinstance(data.get("page"), dict) else {}
        url = _s(page2.get("url"))
    out["landing_url"] = url

    yclid = None
    if url:
        try:
            qs = parse_qs(urlparse(url).query)
        except Exception:
            qs = {}
        for key in ("yclid", "ysclid", "gclid"):
            v = qs.get(key)
            if v and v[0].strip():
                yclid = v[0].strip()
                break
        # UTM из адреса входа — запасной источник, если client.utm пуст.
        for k in UTM_KEYS:
            if not out.get(k):
                v = qs.get(k)
                if v and v[0].strip():
                    out[k] = v[0].strip()
    out["yclid"] = yclid

    return out


def tracking_is_empty(tracking: Optional[dict[str, Any]]) -> bool:
    if not tracking:
        return True
    meaningful = (*UTM_KEYS, "roistat", "yclid")
    return not any(tracking.get(k) for k in meaningful)
