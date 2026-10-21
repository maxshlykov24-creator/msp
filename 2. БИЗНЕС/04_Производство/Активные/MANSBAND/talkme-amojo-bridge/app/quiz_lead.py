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
    comment = _comment_text(data, nested, contact, lead) or _comment_from_answers(answers)

    return {
        "name": name,
        "phone": phone,
        "email": email,
        "answers": answers,
        "tracking": tracking,
        "page_url": page_url,
        "comment": comment,
    }


_COMMENT_KEYS = (
    "comment",
    "comments",
    "message",
    "text",
    "note",
    "user_comment",
    "комментарий",
    "комментарии",
)


def _comment_text(*sources: Any) -> str:
    """Свободный текст квиза: верхний уровень, вложенные объекты, список комментариев."""
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in _COMMENT_KEYS:
            text = _comment_value(source.get(key))
            if text:
                return text
    return ""


def _comment_value(raw: Any) -> str:
    if isinstance(raw, dict):
        for key in ("text", "value", "comment", "message", "body"):
            text = _comment_value(raw.get(key))
            if text:
                return text
        return ""
    if isinstance(raw, (list, tuple)):
        parts = [_comment_value(item) for item in raw]
        return "\n".join(part for part in parts if part)
    return _s(raw)


def _plain_answer(raw: Any) -> str:
    if isinstance(raw, dict):
        for key in ("answer", "value", "label", "a"):
            if raw.get(key) not in (None, ""):
                return _plain_answer(raw.get(key))
        if raw.get("text") not in (None, "") and "comment" not in raw and "note" not in raw:
            return _plain_answer(raw.get("text"))
        return ""
    if isinstance(raw, (list, tuple)):
        parts = [_plain_answer(item) if isinstance(item, dict) else _s(item) for item in raw]
        return ", ".join(part for part in parts if part)
    return _s(raw)


def _comment_from_answers(answers: Any) -> str:
    if isinstance(answers, dict):
        for key, value in answers.items():
            if _s(key).casefold() in _COMMENT_KEYS:
                text = _comment_value(value)
                if text:
                    return text
    if isinstance(answers, list):
        for item in answers:
            if not isinstance(item, dict):
                continue
            title = _s(item.get("question") or item.get("q") or item.get("title") or item.get("name"))
            if title.casefold() in _COMMENT_KEYS:
                text = _comment_value(item.get("answer") or item.get("value") or item.get("text"))
                if text:
                    return text
    return ""


def _answer_line(question: str, answer: Any, comment: Any = None) -> str:
    if isinstance(answer, dict) and comment in (None, ""):
        comment = answer.get("comment") or answer.get("note") or answer.get("message")
    answer_text = _plain_answer(answer)
    extra = _comment_value(comment)
    if question and answer_text and extra:
        return f"{question}: {answer_text} ({extra})"
    if question and answer_text:
        return f"{question}: {answer_text}"
    if question and extra:
        return f"{question}: {extra}"
    return answer_text or extra


def answers_as_lines(answers: Any) -> list[str]:
    lines: list[str] = []
    if isinstance(answers, dict):
        for k, v in answers.items():
            if _s(k).casefold() in _COMMENT_KEYS and _comment_value(v):
                # общий комментарий печатается отдельным блоком, не строкой ответа
                continue
            line = _answer_line(_s(k), v)
            if line:
                lines.append(line)
        return lines
    if isinstance(answers, list):
        for item in answers:
            if isinstance(item, dict):
                q = _s(item.get("question") or item.get("q") or item.get("title") or item.get("name"))
                a = item.get("answer")
                if a is None:
                    a = item.get("value") if item.get("value") is not None else item.get("a")
                if a is None:
                    a = item.get("text") if "comment" not in item and "note" not in item else ""
                line = _answer_line(q, a, item.get("comment") or item.get("note") or item.get("message"))
                if line:
                    lines.append(line)
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


QUIZ_NOTE_MARK = "Заявка с квиза"
QUIZ_LEAD_WINDOW_SEC = 20 * 60


def pick_recent_quiz_lead(
    leads: list[dict[str, Any]],
    *,
    pipeline_id: int,
    now: int,
    window_sec: int = QUIZ_LEAD_WINDOW_SEC,
) -> Optional[dict[str, Any]]:
    """Сделка квиза, созданная только что. Повтор вебхука дописывает примечание в неё."""
    best: Optional[dict[str, Any]] = None
    for lead in leads:
        if int(lead.get("pipeline_id") or 0) != int(pipeline_id):
            continue
        name = _s(lead.get("name"))
        if not name.startswith("Квиз"):
            continue
        created = int(lead.get("created_at") or 0)
        if created <= 0 or now - created > window_sec or created > now + 120:
            continue
        if best is None or created > int(best.get("created_at") or 0):
            best = lead
    return best


def lead_title(parsed: dict[str, Any]) -> str:
    name = parsed.get("name") or ""
    return f"Квиз: {name}" if name else "Квиз zamer.mansband.ru"


def require_phone(parsed: dict[str, Any]) -> Optional[str]:
    raw = parsed.get("phone") or ""
    if not normalize_phone(raw):
        return None
    return raw
