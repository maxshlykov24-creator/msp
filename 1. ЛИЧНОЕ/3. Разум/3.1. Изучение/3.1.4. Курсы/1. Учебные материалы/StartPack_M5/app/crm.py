"""Достаём ссылку на запись звонка из вебхука CRM.

AmoCRM: вебхук на примечание-звонок, ссылка обычно в params.link (или дотягиваем по element_id).
Bitrix24: событие ONVOXIMPLANTCALLEND ссылку НЕ несёт — берём CALL_ID и дотягиваем
          через voximplant.statistic.get.
"""
import amocrm
import bitrix


def flatten(data, prefix: str = "") -> dict:
    """Разворачиваем вложенный JSON/форму в плоский словарь со строковыми ключами."""
    out = {}
    if isinstance(data, dict):
        items = data.items()
    elif isinstance(data, list):
        items = ((str(i), v) for i, v in enumerate(data))
    else:
        return {prefix: data}
    for k, v in items:
        key = f"{prefix}[{k}]" if prefix else str(k)
        if isinstance(v, (dict, list)):
            out.update(flatten(v, key))
        else:
            out[key] = v
    return out


def _find(flat: dict, *substrs) -> str | None:
    """Первое значение, у которого ключ содержит все подстроки (без регистра)."""
    for k, v in flat.items():
        kl = k.lower()
        if v not in (None, "") and all(s in kl for s in substrs):
            return v
    return None


def _clean_url(url: str | None) -> str | None:
    """Убрать экранирование слэшей (вебхуки иногда шлют https:\\/\\/...)."""
    return url.replace("\\", "") if url else url


def _extract_amo(flat: dict) -> tuple[str | None, dict]:
    link = _find(flat, "link")
    phone = _find(flat, "phone")
    duration = _find(flat, "duration")
    lead_id = _find(flat, "element_id") or _find(flat, "lead", "id") or _find(flat, "id")
    if not link and lead_id:
        link, phone, duration = amocrm.latest_call_recording(lead_id)
    return _clean_url(link), {"source": "AmoCRM", "phone": phone, "duration": duration, "deal": lead_id}


def _extract_bitrix(flat: dict) -> tuple[str | None, dict]:
    call_id = _find(flat, "call_id")
    rec = bitrix.call_recording(call_id) if call_id else None
    if rec:
        url, _etype, eid, phone, dur = rec
        return url, {"source": "Bitrix24", "phone": phone, "duration": dur, "deal": eid}
    # запасной путь: некоторые телефонии кладут ссылку прямо в вебхук
    url = _clean_url(_find(flat, "record") or _find(flat, "url"))
    return url, {"source": "Bitrix24", "phone": _find(flat, "phone"),
                 "duration": _find(flat, "duration"), "deal": _find(flat, "entity_id")}


def extract(source: str, payload: dict) -> tuple[str | None, dict]:
    flat = flatten(payload)
    return _extract_amo(flat) if source == "amo" else _extract_bitrix(flat)
