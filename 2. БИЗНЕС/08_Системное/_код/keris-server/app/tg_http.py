"""HTTP к api.telegram.org с принудительным IPv6.

С RU-VPS Telegram по IPv4 таймаутится (блок), по IPv6 — отвечает.
Патч getaddrinfo ставится один раз при импорте и не снимается: только хосты
Telegram режутся до AF_INET6, остальные DNS как были. Раньше ipv6_only
патчил сокет глобально на время запроса и в finally откатывал — при двух
отправках сразу один поток снимал патч у другого, «спасибо за запись» уходило
в IPv4 и падало в TimeoutError (KERIS-1087, 2026-08-31).
"""
from __future__ import annotations

import logging
import socket
import time
from contextlib import contextmanager
from json import dumps as json_dumps
from typing import Any, Iterator

import requests

log = logging.getLogger("keris.tg_http")

_real_getaddrinfo = socket.getaddrinfo


def _is_telegram_host(host: object) -> bool:
    if isinstance(host, (bytes, bytearray)):
        name = host.decode("ascii", "replace")
    else:
        name = str(host or "")
    name = name.rstrip(".").lower()
    return name == "api.telegram.org" or name.endswith(".telegram.org")


def _telegram_ipv6_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):  # noqa: A002
    if _is_telegram_host(host):
        return _real_getaddrinfo(host, port, socket.AF_INET6, type, proto, flags)
    return _real_getaddrinfo(host, port, family, type, proto, flags)


def install_telegram_ipv6() -> None:
    """Один раз на процесс. Повторный вызов не стопорит чужой патч и не дублирует."""
    current = socket.getaddrinfo
    if getattr(current, "_keris_tg_ipv6", False):
        return
    _telegram_ipv6_getaddrinfo._keris_tg_ipv6 = True  # type: ignore[attr-defined]
    socket.getaddrinfo = _telegram_ipv6_getaddrinfo


install_telegram_ipv6()


@contextmanager
def ipv6_only() -> Iterator[None]:
    """Совместимость: постоянный патч уже стоит, снимать его нельзя."""
    install_telegram_ipv6()
    yield


def tg_post(url: str, *, json: dict | None = None, timeout: float = 10) -> requests.Response:
    with ipv6_only():
        return requests.post(url, json=json, timeout=timeout)


def tg_get_file_bytes(bot_token: str, file_id: str, *, timeout: float = 30) -> bytes | None:
    """Скачивает файл к себе: getFile отдаёт file_path, живущий около часа, поэтому
    хранить file_id вместо файла нельзя — через месяц клиент увидит битые картинки."""
    try:
        with ipv6_only():
            meta = requests.post(
                f"https://api.telegram.org/bot{bot_token}/getFile",
                json={"file_id": file_id},
                timeout=timeout,
            )
            if meta.status_code != 200:
                log.warning("getFile HTTP %s: %s", meta.status_code, meta.text[:200])
                return None
            file_path = ((meta.json() or {}).get("result") or {}).get("file_path")
            if not file_path:
                return None
            blob = requests.get(
                f"https://api.telegram.org/file/bot{bot_token}/{file_path}",
                timeout=timeout,
            )
            if blob.status_code != 200:
                log.warning("скачивание файла HTTP %s", blob.status_code)
                return None
            return blob.content
    except Exception:  # noqa: BLE001 — загрузка фото не должна ронять запрос
        log.warning("не удалось скачать файл из Telegram", exc_info=True)
        return None


def tg_send_photos(
    bot_token: str,
    chat_id: str | int,
    photos: list[tuple[str, bytes]],
    *,
    caption: str = "",
    timeout: float = 60,
) -> bool:
    """Фото-отчёт в чат: одно фото — sendPhoto, несколько — sendMediaGroup.

    Файлы уходят multipart из нашего хранилища, а не по file_id: file_id живёт в
    том боте, куда фото загрузили, и в клиентском боте не действителен.
    """
    if not photos:
        return False
    try:
        with ipv6_only():
            if len(photos) == 1:
                name, blob = photos[0]
                resp = requests.post(
                    f"https://api.telegram.org/bot{bot_token}/sendPhoto",
                    data={"chat_id": str(chat_id), "caption": caption[:1024], "parse_mode": "HTML"},
                    files={"photo": (name, blob)},
                    timeout=timeout,
                )
            else:
                media = []
                files = {}
                for idx, (name, blob) in enumerate(photos[:10]):
                    key = f"photo{idx}"
                    item = {"type": "photo", "media": f"attach://{key}"}
                    if idx == 0 and caption:
                        item["caption"] = caption[:1024]
                        item["parse_mode"] = "HTML"
                    media.append(item)
                    files[key] = (name, blob)
                resp = requests.post(
                    f"https://api.telegram.org/bot{bot_token}/sendMediaGroup",
                    data={"chat_id": str(chat_id), "media": json_dumps(media)},
                    files=files,
                    timeout=timeout,
                )
        if resp.status_code != 200:
            log.warning("отправка фото chat_id=%s -> %s %s", chat_id, resp.status_code, resp.text[:300])
            return False
        return bool((resp.json() or {}).get("ok"))
    except Exception:  # noqa: BLE001
        log.warning("ошибка отправки фото chat_id=%s", chat_id, exc_info=True)
        return False


def tg_send_message(
    bot_token: str,
    chat_id: str | int,
    text: str,
    *,
    parse_mode: str = "HTML",
    timeout: float = 10,
    attempts: int = 3,
    reply_markup: dict | None = None,
    disable_web_page_preview: bool = True,
) -> bool:
    """sendMessage с ретраями на таймаут и 5xx. 4xx не ретраим."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": disable_web_page_preview,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    for attempt in range(1, attempts + 1):
        try:
            resp = tg_post(url, json=payload, timeout=timeout)
        except Exception:
            log.warning(
                "telegram request failed attempt=%s/%s chat_id=%s",
                attempt, attempts, chat_id, exc_info=True,
            )
            if attempt < attempts:
                time.sleep(0.4 * attempt)
            continue
        if resp.status_code == 200:
            try:
                body = resp.json()
            except ValueError:
                body = {}
            if body.get("ok"):
                return True
            log.warning("telegram not ok chat_id=%s body=%s", chat_id, resp.text[:300])
            return False
        log.warning(
            "telegram HTTP %s attempt=%s/%s chat_id=%s body=%s",
            resp.status_code, attempt, attempts, chat_id, resp.text[:300],
        )
        if resp.status_code in (400, 401, 403, 404):
            return False
        if attempt < attempts:
            time.sleep(0.4 * attempt)
    return False
