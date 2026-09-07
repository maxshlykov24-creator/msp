"""Вызов LLM с цепочкой запасных вариантов.

Два бэкенда: OpenRouter и Gemini напрямую. OpenRouter отдаёт 403 с российских
IP, поэтому на VPS работает только прямой Gemini. Цепочка перебирает всё, что
настроено, и берёт первый живой ответ.
"""
from __future__ import annotations

import logging

import httpx

from bot.config import settings

log = logging.getLogger("llm")
URL = "https://openrouter.ai/api/v1/chat/completions"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent"


class LlmError(RuntimeError):
    pass


async def _call(client: httpx.AsyncClient, model: str, messages: list[dict]) -> str:
    resp = await client.post(
        URL,
        headers={
            "Authorization": "Bearer %s" % settings.openrouter_key,
            "HTTP-Referer": "https://msproduct.ru",
            "X-Title": "DIVO Motors AI manager",
        },
        json={
            "model": model,
            "messages": messages,
            "temperature": settings.temperature,
            # У Gemini 3.5 thinking ест тот же max_tokens, что и ответ.
            # 700 не хватало: фраза обрывалась на «за 1».
            "max_tokens": settings.max_tokens,
            "reasoning": {"effort": "minimal", "exclude": True},
        },
    )
    if resp.status_code != 200:
        raise LlmError("%s: HTTP %s %s" % (model, resp.status_code, resp.text[:300]))
    data = resp.json()
    choices = data.get("choices") or []
    if not choices:
        raise LlmError("%s: пустой ответ %s" % (model, str(data)[:300]))
    choice = choices[0]
    usage = data.get("usage") or {}
    log.info(
        "%s finish=%s tokens in=%s out=%s reasoning=%s",
        model,
        choice.get("finish_reason"),
        usage.get("prompt_tokens"),
        usage.get("completion_tokens"),
        (usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
    )
    if choice.get("finish_reason") == "length":
        raise LlmError("%s: ответ обрезан по лимиту токенов" % model)
    return (choice.get("message") or {}).get("content") or ""


async def _call_gemini(
    client: httpx.AsyncClient, model: str, system: str, history: list[dict]
) -> str:
    contents = [
        {
            "role": "model" if m["role"] == "assistant" else "user",
            "parts": [{"text": m["content"]}],
        }
        for m in history
    ]
    resp = await client.post(
        GEMINI_URL % model,
        params={"key": settings.gemini_key},
        json={
            "system_instruction": {"parts": [{"text": system}]},
            "contents": contents,
            "generationConfig": {
                "temperature": settings.temperature,
                "maxOutputTokens": settings.max_tokens,
            },
        },
    )
    if resp.status_code != 200:
        raise LlmError("%s: HTTP %s %s" % (model, resp.status_code, resp.text[:300]))
    candidates = resp.json().get("candidates") or []
    if not candidates:
        raise LlmError("%s: пустой ответ %s" % (model, resp.text[:300]))
    parts = ((candidates[0].get("content") or {}).get("parts")) or []
    return "".join(p.get("text", "") for p in parts)


def _chain() -> list[tuple[str, str]]:
    """Кто и в каком порядке пробует ответить: [(бэкенд, модель), ...]."""
    chain: list[tuple[str, str]] = []
    if settings.openrouter_key:
        for model in (settings.model, settings.model_fallback):
            if model and ("openrouter", model) not in chain:
                chain.append(("openrouter", model))
    if settings.gemini_key:
        for model in (settings.gemini_model, settings.gemini_model_fallback):
            if model and ("gemini", model) not in chain:
                chain.append(("gemini", model))
    return chain


async def reply(system: str, history: list[dict]) -> str:
    """history — [{role: user|assistant, content: ...}] в порядке диалога."""
    chain = _chain()
    if not chain:
        raise LlmError("не настроен ни один ключ: OPENROUTER_API_KEY или GEMINI_API_KEY")
    messages = [{"role": "system", "content": system}] + history

    last: Exception | None = None
    async with httpx.AsyncClient(timeout=90, proxy=settings.llm_proxy or None) as client:
        for backend, model in chain:
            try:
                if backend == "gemini":
                    text = await _call_gemini(client, model, system, history)
                else:
                    text = await _call(client, model, messages)
                if text.strip():
                    return text.strip()
                last = LlmError("%s/%s: ответ пустой строкой" % (backend, model))
            except (httpx.HTTPError, LlmError) as exc:
                log.warning("модель %s/%s не ответила: %s", backend, model, exc)
                last = exc
    raise LlmError("все модели не ответили: %s" % last)
