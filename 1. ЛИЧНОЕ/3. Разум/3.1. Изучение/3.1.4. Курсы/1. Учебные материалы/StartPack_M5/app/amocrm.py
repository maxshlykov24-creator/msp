"""AmoCRM API v4 (напрямую — РФ-эндпоинт).

Нужны AMOCRM_SUBDOMAIN + AMOCRM_LONG_TOKEN (долгосрочный токен интеграции).
В AmoCRM звонки приходят примечанием (note) типа call_in / call_out со ссылкой на запись
в params.link. Webhook на добавление примечания несёт element_id сделки/контакта — по нему
при необходимости дотягиваем запись через API.
"""
from config import settings
from net import direct_client


def _base() -> str:
    return f"https://{settings.amocrm_subdomain}.amocrm.ru/api/v4"


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.amocrm_long_token}",
            "Content-Type": "application/json"}


def _configured() -> bool:
    return bool(settings.amocrm_subdomain and settings.amocrm_long_token)


def _pick_call(notes_json) -> tuple | None:
    """Из ответа /notes вернуть (link, phone, duration) самого свежего звонка с записью."""
    for n in ((notes_json or {}).get("_embedded", {}) or {}).get("notes", []):
        if n.get("note_type") in ("call_in", "call_out"):
            p = n.get("params") or {}
            if p.get("link"):
                return p["link"].replace("\\", ""), p.get("phone"), p.get("duration")
    return None


def latest_call_recording(lead_id) -> tuple:
    """Ссылка на запись последнего звонка по сделке. Звонки могут висеть на связанных контактах:
    сделка → её ноты, затем → связанные контакты → их ноты. (url, phone, duration) или (None, None, None)."""
    if not _configured() or not lead_id:
        return None, None, None
    with direct_client(timeout=60) as cli:
        def gj(path):
            r = cli.get(f"{_base()}{path}", headers=_headers())
            if r.status_code == 204:
                return None
            r.raise_for_status()
            return r.json()
        hit = _pick_call(gj(f"/leads/{lead_id}/notes?order[created_at]=desc&limit=100"))
        if hit:
            return hit
        lead = gj(f"/leads/{lead_id}?with=contacts")
        for c in ((lead or {}).get("_embedded", {}) or {}).get("contacts", []):
            hit = _pick_call(gj(f"/contacts/{c['id']}/notes?order[created_at]=desc&limit=100"))
            if hit:
                return hit
    return None, None, None


def add_note(lead_id: int, text: str) -> dict:
    """Прикрепить примечание к сделке (опциональная запись результата обратно в CRM)."""
    if not _configured():
        raise RuntimeError("AmoCRM не сконфигурирован (.env)")
    url = f"{_base()}/leads/{lead_id}/notes"
    body = [{"note_type": "common", "params": {"text": text}}]
    with direct_client() as cli:
        r = cli.post(url, headers=_headers(), json=body)
        r.raise_for_status()
        return r.json()
