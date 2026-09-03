"""Алерты в Telegram: чтобы о поломке узнавали мы, а не клиент.

Повод завести: отвал софтфона 102 наблюдатель на Asterisk зафиксировал 07.08, а
мы узнали о нём от клиента 13.08 — неделю клиенты звонили в пустоту, потому что
журнал наблюдателя никто не читает. Алерт нужен там, где факт уже известен
системе, но не человеку.

Алерты шлют оба конца: хаб (очередь, активность менеджеров, всплеск сделок) и
наблюдатель на Asterisk через `POST /internal/alert` — токен бота живёт в одном
месте, в `.env` хаба.

Без `TELEGRAM_BOT_TOKEN` и `TELEGRAM_CHAT_ID` модуль молчит и ничего не ломает.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request

from app.config import settings

log = logging.getLogger("alerts")

# Одна и та же поломка не должна приходить каждые пять минут: молчание после
# первого сообщения делает алерты читаемыми, а не фоном, который глушат.
_last_sent: dict[str, float] = {}
DEFAULT_COOLDOWN = 3600.0


def notify(text: str, key: str = "", cooldown: float = DEFAULT_COOLDOWN) -> bool:
    """Отправляет сообщение. `key` задаёт «одну и ту же поломку» для молчания."""
    token = (settings.telegram_bot_token or "").strip()
    chat = (settings.telegram_chat_id or "").strip()
    if not token or not chat:
        log.info("alert (не отправлен, нет токена): %s", text.replace("\n", " ")[:200])
        return False

    if key:
        last = _last_sent.get(key, 0.0)
        if time.monotonic() - last < cooldown:
            return False

    payload = json.dumps({"chat_id": chat, "text": text,
                          "parse_mode": "HTML",
                          "disable_web_page_preview": True}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload, headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            ok = 200 <= resp.status < 300
    except (urllib.error.URLError, OSError) as exc:
        log.warning("alert send failed: %s", exc)
        return False
    if ok and key:
        _last_sent[key] = time.monotonic()
    return ok


_user_names: dict[str, Any] = {"map": None, "ts": 0.0}
_USER_TTL = 600.0


def _user_name(client: Any, user_id: int | None) -> str:
    """Имя ответственного для текста алерта. Не достали — показываем id."""
    if not user_id:
        return "не назначен"
    now = time.monotonic()
    if _user_names["map"] is None or now - float(_user_names["ts"]) > _USER_TTL:
        try:
            _user_names["map"] = {int(u["id"]): str(u.get("name") or "") for u in client.users()}
            _user_names["ts"] = now
        except Exception as exc:  # noqa: BLE001 — алерт важнее красивого имени
            log.warning("users fetch for alert failed: %s", exc)
            _user_names["map"] = _user_names["map"] or {}
    return (_user_names["map"] or {}).get(int(user_id)) or f"id {user_id}"


def _esc(value: str) -> str:
    return str(value or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def new_lead(client: Any, lead: dict[str, Any], phone: str = "") -> bool:
    """Заявка пришла — сказать об этом сразу, а не по факту просроченной задачи.

    Просил клиент 28.08.2026: лиды падают ночью на менеджера, которого нет на
    смене, и первым это замечает он сам, а не система."""
    if not settings.alert_new_lead:
        return False
    from app.chat_events import lead_url

    lead_id = int(lead.get("id") or 0)
    if not lead_id:
        return False
    name = _esc(lead.get("name") or f"Сделка #{lead_id}")
    owner = _user_name(client, lead.get("responsible_user_id"))
    tags = ", ".join(
        _esc(t.get("name")) for t in ((lead.get("_embedded") or {}).get("tags") or [])
        if t.get("name")
    )
    lines = [
        "🆕 <b>Новая заявка</b> | LicenseBridge\n",
        f"▪️ <b>Сделка:</b> <a href='{lead_url(lead_id)}'>{name}</a>",
        f"▪️ <b>Ответственный:</b> {_esc(owner)}",
    ]
    if phone:
        lines.append(f"▪️ <b>Телефон:</b> {_esc(phone)}")
    if tags:
        lines.append(f"▪️ <b>Метки:</b> {tags}")
    # key по сделке: повторная доставка вебхука Kommo не даёт второго сообщения
    return notify("\n".join(lines), key=f"new_lead:{lead_id}", cooldown=24 * 3600)
