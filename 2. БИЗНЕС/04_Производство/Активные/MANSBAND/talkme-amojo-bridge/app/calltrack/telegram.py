from __future__ import annotations

import logging

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)
_warned = False


def send(text: str) -> bool:
    """Сообщение в Telegram. Без токена возвращает False и пишет в лог один раз."""
    global _warned
    settings = get_settings()
    token = (settings.telegram_bot_token or "").strip()
    chat_id = (settings.telegram_chat_id or "").strip()
    if not token or not chat_id:
        if not _warned:
            log.warning("Telegram для коллтрекинга не настроен")
            _warned = True
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        r = httpx.post(url, json={"chat_id": chat_id, "text": (text or "")[:4000]}, timeout=10.0)
    except Exception:
        log.exception("telegram send failed")
        return False
    if r.status_code >= 300:
        log.error("telegram send http=%s", r.status_code)
        return False
    return True


def incident(db, key: str, active: bool, message: str, now=None) -> bool:
    """Один сигнал, повтор через 30 минут, затем восстановление. Неудача не гасит сигнал."""
    import datetime as dt
    import json
    from sqlalchemy import select
    from app.models import KvState

    now = now or dt.datetime.now(dt.timezone.utc)
    row = db.execute(select(KvState).where(KvState.key == "ct_alert:" + key)).scalar_one_or_none()
    state = json.loads(row.value) if row and row.value else {}
    sent = False
    if active:
        last = float(state.get("last_sent", 0))
        if not last or now.timestamp() - last >= 1800:
            sent = send(message)
            if sent:
                state["last_sent"] = now.timestamp()
                state["notified"] = True
        state["active"] = True
    elif state.get("notified"):
        sent = send("Восстановлено: " + key)
        if sent:
            state = {}
    else:
        state = {}
    if row is None:
        row = KvState(key="ct_alert:" + key, value="{}")
        db.add(row)
    row.value = json.dumps(state, ensure_ascii=False)
    row.updated_at = now
    db.flush()
    return sent
