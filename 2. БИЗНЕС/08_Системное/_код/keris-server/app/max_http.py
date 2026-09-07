"""HTTP к MAX Bot API (platform-api2.max.ru). Без IPv6-костыля: API российский."""
from __future__ import annotations

import logging
from typing import Any, Optional

import requests

from .config import settings

log = logging.getLogger("keris.max_http")

# platform-api2.max.ru требует корневой Минцифры; на VPS проходит platform-api.max.ru.
BASE = "https://platform-api.max.ru"


def _headers() -> dict[str, str]:
    return {
        "Authorization": (settings.max_bot_token or "").strip(),
        "Content-Type": "application/json",
    }


def _upload_image(data: bytes, filename: str) -> Optional[dict[str, Any]]:
    """Загрузка картинки в MAX двумя шагами: /uploads даёт адрес, туда — файл.

    Ответ на второй шаг приходит в двух формах (`token` или словарь `photos`),
    поэтому нормализуем в payload вложения.
    """
    try:
        slot = requests.post(
            f"{BASE}/uploads",
            params={"type": "image"},
            headers={"Authorization": (settings.max_bot_token or "").strip()},
            timeout=15,
        )
        if slot.status_code >= 400:
            log.warning("MAX uploads -> %s %s", slot.status_code, slot.text[:200])
            return None
        url = (slot.json() or {}).get("url")
        if not url:
            return None
        sent = requests.post(url, files={"data": (filename, data)}, timeout=60)
        if sent.status_code >= 400:
            log.warning("MAX upload файла -> %s %s", sent.status_code, sent.text[:200])
            return None
        body = sent.json() or {}
        if body.get("token"):
            return {"token": body["token"]}
        photos = body.get("photos") or {}
        for item in photos.values():
            if isinstance(item, dict) and item.get("token"):
                return {"photos": photos}
        return None
    except Exception:  # noqa: BLE001 — фото-отчёт best-effort
        log.warning("ошибка загрузки картинки в MAX", exc_info=True)
        return None


def send_photos(user_id: int, text: str, images: list[tuple[str, bytes]]) -> bool:
    """Фото-отчёт в MAX. Не получилось загрузить картинки — уходит текст со
    ссылками на кабинет, поэтому клиент всё равно узнаёт про отчёт."""
    if not settings.max_bot_token or not images:
        return False
    attachments = []
    for name, blob in images[:10]:
        payload = _upload_image(blob, name)
        if payload:
            attachments.append({"type": "image", "payload": payload})
    if not attachments:
        return False
    try:
        r = requests.post(
            f"{BASE}/messages",
            params={"user_id": str(user_id)},
            headers=_headers(),
            json={"text": text[:4000], "format": "html", "attachments": attachments},
            timeout=30,
        )
        if r.status_code >= 400:
            log.warning("MAX send photos user_id=%s -> %s %s", user_id, r.status_code, r.text[:200])
            return False
        return True
    except Exception:  # noqa: BLE001
        log.warning("ошибка отправки фото в MAX user_id=%s", user_id, exc_info=True)
        return False


def send_message(
    user_id: int,
    text: str,
    buttons: Optional[list[list[dict[str, Any]]]] = None,
) -> bool:
    if not settings.max_bot_token:
        log.info("MAX_BOT_TOKEN не задан — сообщение в MAX не отправлено (user_id=%s)", user_id)
        return False
    body: dict[str, Any] = {
        "text": text[:4000],
        "format": "html",
        "disable_link_preview": True,
    }
    if buttons:
        body["attachments"] = [
            {"type": "inline_keyboard", "payload": {"buttons": buttons}}
        ]
    try:
        r = requests.post(
            f"{BASE}/messages",
            params={"user_id": str(user_id)},
            headers=_headers(),
            json=body,
            timeout=10,
        )
        if r.status_code >= 400:
            log.warning("MAX send user_id=%s -> %s %s", user_id, r.status_code, r.text[:200])
            return False
        return True
    except Exception:  # noqa: BLE001
        log.warning("ошибка отправки в MAX user_id=%s", user_id, exc_info=True)
        return False
