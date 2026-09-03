"""Bitrix24 REST через входящий вебхук (напрямую — РФ-эндпоинт).

Базовый URL — BITRIX_WEBHOOK_BASE (вида https://<portal>/rest/<user>/<secret>/).
Метод вызывается как <base><method>.json
"""
from config import settings
from net import direct_client


def _configured() -> bool:
    return bool(settings.bitrix_webhook_base)


def call(method: str, params: dict) -> dict:
    if not _configured():
        raise RuntimeError("BITRIX_WEBHOOK_BASE не задан")
    url = f"{settings.bitrix_webhook_base}{method}.json"
    with direct_client() as cli:
        r = cli.post(url, json=params)
        r.raise_for_status()
        return r.json()


def call_recording(call_id) -> tuple | None:
    """voximplant.statistic.get по CALL_ID → (record_url, entity_type, entity_id, phone, duration).
    Событие ONVOXIMPLANTCALLEND ссылку на запись НЕ несёт — достаём её отсюда.
    None — если статистики/записи ещё нет (бывает с задержкой)."""
    if not _configured() or not call_id:
        return None
    data = call("voximplant.statistic.get", {"FILTER": {"CALL_ID": call_id}})
    rows = (data or {}).get("result") or []
    if not rows:
        return None
    r = rows[0]
    return (r.get("CALL_RECORD_URL") or None, r.get("CRM_ENTITY_TYPE"),
            r.get("CRM_ENTITY_ID"), r.get("PHONE_NUMBER"), r.get("CALL_DURATION"))


def add_timeline_comment(entity_id: int, text: str, entity_type: str = "deal") -> dict:
    """Добавить комментарий в таймлайн сделки (опциональная запись результата обратно в CRM)."""
    return call("crm.timeline.comment.add", {
        "fields": {"ENTITY_ID": entity_id, "ENTITY_TYPE": entity_type, "COMMENT": text}
    })
