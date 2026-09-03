"""
Groq Chat Completions API (OpenAI-совместимый).
https://console.groq.com/docs/quickstart
"""

from __future__ import annotations

import json
import logging
from typing import Any

import aiohttp

from config import (
    GROQ_API_KEY,
    GROQ_MODEL,
    LLM_SYSTEM_PROMPT,
    LLM_SUMMARIZE_SYSTEM_PROMPT,
    LLM_TIMEOUT_SEC,
)

logger = logging.getLogger(__name__)

GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"


async def chat(
    history_messages: list[dict[str, str]],
    user_text: str,
    *,
    rolling_summaries: list[str] | None = None,
) -> str:
    """
    history_messages: последние реплики user|assistant.
    rolling_summaries — выжимки из прошлых эпизодов (склеиваются в system).
    """
    key = (GROQ_API_KEY or "").strip()
    if not key or "YOUR_" in key:
        raise RuntimeError(
            "GROQ_API_KEY пустой или плейсхолдер — задай в .env (console.groq.com)."
        )

    system_content = (LLM_SYSTEM_PROMPT or "").strip()
    rs = [s.strip() for s in (rolling_summaries or []) if (s or "").strip()]
    if rs:
        system_content += (
            "\n\n--- Сжатый контекст из прежних реплик (сохрани согласованность) ---\n"
            + "\n\n".join(rs)
        )

    msgs: list[dict[str, str]] = [{"role": "system", "content": system_content}]
    for m in history_messages:
        role = m.get("role")
        content = (m.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            msgs.append({"role": role, "content": content})
    msgs.append({"role": "user", "content": user_text.strip()})

    model = (GROQ_MODEL or "llama-3.3-70b-versatile").strip()
    payload: dict[str, Any] = {
        "model": model,
        "messages": msgs,
        "temperature": 0.7,
        "max_tokens": 4096,
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }

    timeout = aiohttp.ClientTimeout(
        total=None,
        sock_connect=30,
        sock_read=max(60, int(LLM_TIMEOUT_SEC)),
    )
    async with aiohttp.ClientSession() as session:
        async with session.post(
            GROQ_CHAT_URL,
            headers=headers,
            json=payload,
            timeout=timeout,
        ) as resp:
            body_text = await resp.text()
            if resp.status != 200:
                logger.error(
                    "Groq HTTP %s: %s",
                    resp.status,
                    body_text[:1200],
                )
                raise RuntimeError(
                    _format_groq_error(resp.status, body_text)
                )
            try:
                data = json.loads(body_text)
            except json.JSONDecodeError as e:
                logger.error("Groq не JSON: %r", body_text[:500])
                raise RuntimeError(
                    f"Groq вернул не JSON: {body_text[:400]!r}"
                ) from e

    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError(
            f"Groq: нет choices в ответе: {str(data)[:500]}"
        )
    msg = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = (
        (msg or {}).get("content") if isinstance(msg, dict) else None
    )
    if not content or not str(content).strip():
        raise RuntimeError(f"Groq: пустой ответ: {str(data)[:500]}")
    out = str(content).strip()
    logger.info("Groq: ответ, len=%s", len(out))
    return out


def _format_batch_for_summary(batch: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for it in batch:
        if not isinstance(it, dict):
            continue
        role = it.get("role")
        content = (it.get("content") or "").strip()
        if not content:
            continue
        if role == "user":
            lines.append(f"Пользователь: {content}")
        elif role == "assistant":
            lines.append(f"Ассистент: {content}")
        elif role == "system" and it.get("kind") == "rolling_summary":
            lines.append(f"Ранее сохранённая выжимка:\n{content}")
    return "\n\n".join(lines)


async def summarize_history_batch(batch: list[dict[str, Any]]) -> str:
    """
    Сжимает первые N записей истории (user/assistant/старые выжимки) в один текст.
    """
    key = (GROQ_API_KEY or "").strip()
    if not key or "YOUR_" in key:
        raise RuntimeError(
            "GROQ_API_KEY пустой — нужен для саммаризации истории."
        )

    dialogue = _format_batch_for_summary(batch)
    if not dialogue.strip():
        return ""

    msgs: list[dict[str, str]] = [
        {"role": "system", "content": LLM_SUMMARIZE_SYSTEM_PROMPT.strip()},
        {
            "role": "user",
            "content": "Сожми следующий фрагмент в одну выжимку:\n\n" + dialogue,
        },
    ]
    model = (GROQ_MODEL or "llama-3.3-70b-versatile").strip()
    payload: dict[str, Any] = {
        "model": model,
        "messages": msgs,
        "temperature": 0.35,
        "max_tokens": 2048,
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    timeout = aiohttp.ClientTimeout(
        total=None,
        sock_connect=30,
        sock_read=max(60, int(LLM_TIMEOUT_SEC)),
    )
    async with aiohttp.ClientSession() as session:
        async with session.post(
            GROQ_CHAT_URL,
            headers=headers,
            json=payload,
            timeout=timeout,
        ) as resp:
            body_text = await resp.text()
            if resp.status != 200:
                logger.error(
                    "Groq summarize HTTP %s: %s",
                    resp.status,
                    body_text[:800],
                )
                raise RuntimeError(_format_groq_error(resp.status, body_text))
            data = json.loads(body_text)

    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("Groq summarize: нет choices")
    msg = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = (msg or {}).get("content") if isinstance(msg, dict) else None
    out = str(content or "").strip()
    if not out:
        raise RuntimeError("Groq summarize: пустой ответ")
    logger.info("Groq: саммаризация истории, len=%s", len(out))
    return out
    if status == 429:
        return (
            "Groq: слишком много запросов (429). Подожди минуту или проверь лимит на console.groq.com."
        )
    if status in (401, 403):
        return "Groq: неверный или просроченный API-ключ (401/403)."
    snippet = body_text[:500]
    try:
        err = json.loads(body_text)
        if isinstance(err, dict) and err.get("error"):
            e = err["error"]
            if isinstance(e, dict) and e.get("message"):
                return f"Groq: {e['message']}"
    except json.JSONDecodeError:
        pass
    return f"Groq HTTP {status}: {snippet}"
