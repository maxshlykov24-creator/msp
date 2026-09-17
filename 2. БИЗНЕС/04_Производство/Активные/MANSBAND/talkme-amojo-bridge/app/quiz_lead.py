from __future__ import annotations

from typing import Any, Optional

from app.talkme_parse import normalize_phone

UTM_KEYS = (
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_referrer",
    "roistat",
    "yclid",
)


def _s(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return str(v)
    return str(v).strip()


def _from_utm_object(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key in UTM_KEYS:
        short = key.replace("utm_", "")
        val = raw.get(key)
        if val in (None, "") and short != key:
            val = raw.get(short)
        if val not in (None, ""):
            out[key] = _s(val)
    return out


def parse_quiz_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Достаём имя, телефон, ответы и метки из произвольного JSON квиза."""
    nested = data.get("data") if isinstance(data.get("data"), dict) else {}
    contact = data.get("contact") if isinstance(data.get("contact"), dict) else {}
    lead = data.get("lead") if isinstance(data.get("lead"), dict) else {}

    name = (
        _s(data.get("name"))
        or _s(data.get("fio"))
        or _s(data.get("full_name"))
        or _s(contact.get("name"))
        or _s(nested.get("name"))
    )
    phone = (
        _s(data.get("phone"))
        or _s(data.get("tel"))
        or _s(contact.get("phone"))
        or _s(nested.get("phone"))
    )
    email = _s(data.get("email")) or _s(contact.get("email")) or _s(nested.get("email"))

    answers = data.get("answers")
    if answers is None:
        answers = data.get("quiz") or lead.get("answers") or nested.get("answers")

    tracking = {k: _s(data.get(k)) for k in UTM_KEYS if _s(data.get(k))}
    tracking.update(_from_utm_object(data.get("utm")))
    tracking.update(_from_utm_object(nested.get("utm") if isinstance(nested, dict) else None))
    if _s(data.get("roistatVisitId")):
        tracking["roistat"] = _s(data.get("roistatVisitId"))
    page_url = _s(data.get("page_url") or data.get("url") or data.get("referer") or data.get("referrer"))
    comment = _s(data.get("comment") or data.get("message") or data.get("text"))

    return {
        "name": name,
        "phone": phone,
        "email": email,
        "answers": answers,
        "tracking": tracking,
        "page_url": page_url,
        "comment": comment,
    }


def answers_as_lines(answers: Any) -> list[str]:
    lines: list[str] = []
    if isinstance(answers, dict):
        for k, v in answers.items():
            if v in (None, ""):
                continue
            if isinstance(v, (list, tuple)):
                v = ", ".join(_s(x) for x in v if x not in (None, ""))
            lines.append(f"{_s(k)}: {_s(v)}")
        return lines
    if isinstance(answers, list):
        for item in answers:
            if isinstance(item, dict):
                q = _s(item.get("question") or item.get("q") or item.get("title") or item.get("name"))
                a = item.get("answer")
                if a is None:
                    a = item.get("value") or item.get("a") or item.get("text")
                if isinstance(a, (list, tuple)):
                    a = ", ".join(_s(x) for x in a if x not in (None, ""))
                a = _s(a)
                if q and a:
                    lines.append(f"{q}: {a}")
                elif a:
                    lines.append(a)
            elif item not in (None, ""):
                lines.append(_s(item))
        return lines
    if answers not in (None, ""):
        return [_s(answers)]
    return []


def build_note(*, parsed: dict[str, Any]) -> str:
    parts = ["Заявка с квиза zamer.mansband.ru", ""]
    if parsed.get("name"):
        parts.append(f"Имя: {parsed['name']}")
    if parsed.get("phone"):
        parts.append(f"Телефон: {parsed['phone']}")
    if parsed.get("email"):
        parts.append(f"Email: {parsed['email']}")
    lines = answers_as_lines(parsed.get("answers"))
    if lines:
        parts.append("")
        parts.append("Ответы квиза:")
        parts.extend(f"- {line}" for line in lines)
    if parsed.get("comment"):
        parts.append("")
        parts.append(f"Комментарий: {parsed['comment']}")
    if parsed.get("page_url"):
        parts.append("")
        parts.append(f"Страница: {parsed['page_url']}")
    tracking = parsed.get("tracking") or {}
    utm_lines = [f"{k}={v}" for k, v in tracking.items() if v]
    if utm_lines:
        parts.append("")
        parts.append("Метки: " + ", ".join(utm_lines))
    return "\n".join(parts).strip()


def lead_title(parsed: dict[str, Any]) -> str:
    name = parsed.get("name") or ""
    return f"Квиз: {name}" if name else "Квиз zamer.mansband.ru"


def require_phone(parsed: dict[str, Any]) -> Optional[str]:
    raw = parsed.get("phone") or ""
    if not normalize_phone(raw):
        return None
    return raw
