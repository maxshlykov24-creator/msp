from __future__ import annotations

import logging
import time

from groq import AsyncGroq

from app.config import get_settings

log = logging.getLogger(__name__)

_MAX_IN = 1000
_STATS_TTL_SECONDS = 60 * 60
_stats_cache: dict[int, tuple[float, str, str]] = {}

SYS_REFLECTION = (
    "Ты — тихий братский свидетель в евангельской традиции. "
    "Брат поделился молитвенной нуждой и откровениями недели. "
    "Ответь 2–3 тёплыми предложениями: отразь суть пережитого, укрепи. "
    "Без цитат из Библии — он сам читает Слово. Без советов и оценок. "
    "Только любовь и наблюдение."
)

SYS_DIGEST = (
    "Ты помогаешь братству. Ниже — отрывки откровений нескольких братьев за одну неделю. "
    "Напиши 3–4 предложения: общая духовная нить недели, без имён, без назидания, "
    "без цитат из Писания. Это пойдёт в общий сообщение группе как духовный итог."
)

SYS_STATS = (
    "Ты — любящий наставник в евангельско-пятидесятнической традиции. "
    "Перед тобой текстовые цифры о дисциплине брата за несколько недель (молитва, проповеди, встречи, план, серии). "
    "Напиши 3–4 предложения: наведи мягкий, любящий фокус на тенденции. "
    "Без давления, осуждения и «надо было». Один деликатный акцент допустим. "
    "Как мог бы коротко сказать любящий старец, глядя на брата с уважением."
)


async def _chat(messages: list[dict], purpose: str) -> str | None:
    settings = get_settings()
    key = (settings.groq_api_key or "").strip()
    if not key:
        return None
    model = settings.groq_model or "llama-3.3-70b-versatile"
    try:
        client = AsyncGroq(api_key=key)
        resp = await client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=500,
            temperature=0.5,
        )
        choice = resp.choices[0].message.content
        out = (choice or "").strip()
        return out[:3500] if out else None
    except Exception:
        log.exception("Groq chat failed (%s)", purpose)
        return None


async def get_reflection(q4_need: str, q5_rev: str) -> str | None:
    """Личное сообщение брату после отчёта."""
    user_text = (
        f"Молитвенная нужда и просьбы о поддержке:\n{_clip(q4_need)}\n\n"
        f"Откровения недели:\n{_clip(q5_rev)}"
    )
    messages = [
        {"role": "system", "content": SYS_REFLECTION},
        {"role": "user", "content": user_text},
    ]
    return await _chat(messages, "reflection")


async def get_digest_summary(revelations_blob: str) -> str | None:
    """Сводка для дайджеста понедельника."""
    blob = _clip(revelations_blob)
    if len(blob.strip()) < 30:
        return None
    messages = [
        {"role": "system", "content": SYS_DIGEST},
        {"role": "user", "content": blob},
    ]
    return await _chat(messages, "digest")


async def get_analytics_comment(stats_plain: str, user_id: int) -> str | None:
    """Мягкий коммент к блоку статистики."""
    now = time.monotonic()
    cached = _stats_cache.get(user_id)
    if cached and cached[1] == stats_plain and now - cached[0] < _STATS_TTL_SECONDS:
        return cached[2]

    messages = [
        {"role": "system", "content": SYS_STATS},
        {"role": "user", "content": _clip(stats_plain)},
    ]
    result = await _chat(messages, "stats")
    if result:
        _stats_cache[user_id] = (now, stats_plain, result)
    return result


def _clip(s: str) -> str:
    s = (s or "").strip()
    if len(s) <= _MAX_IN:
        return s
    return s[: _MAX_IN] + "…"
