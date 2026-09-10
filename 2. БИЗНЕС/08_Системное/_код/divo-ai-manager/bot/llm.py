"""Вызов LLM с цепочкой запасных вариантов.

Два бэкенда: OpenRouter и Gemini напрямую. OpenRouter отдаёт 403 с российских
IP, поэтому на VPS работает только прямой Gemini. Цепочка перебирает всё, что
настроено, и берёт первый живой ответ.
"""
from __future__ import annotations

import logging

import httpx

from bot import prompt
from bot.config import settings

log = logging.getLogger("llm")
URL = "https://openrouter.ai/api/v1/chat/completions"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent"


class LlmError(RuntimeError):
    pass


def _system_message(model: str, system: str) -> dict:
    """Постоянную часть промпта помечаем к кэшированию, поправки хода - нет.

    Каркас, база знаний и сток — это 48 тысяч токенов, одинаковых во всех
    репликах диалога. Без кэша каждый ход стоит как первый. У Anthropic
    кэш живёт 5 минут, чтение из него — десятая часть цены ввода.
    """
    static, _, dynamic = system.partition(prompt.CACHE_SPLIT)
    if not model.startswith("anthropic/"):
        return {"role": "system", "content": system.replace(prompt.CACHE_SPLIT, "\n\n")}
    parts = [
        {
            "type": "text",
            "text": static,
            "cache_control": {"type": "ephemeral"},
        }
    ]
    if dynamic.strip():
        parts.append({"type": "text", "text": dynamic})
    return {"role": "system", "content": parts}


def _openrouter_body(model: str, messages: list[dict]) -> dict:
    """Sonnet 5 ломается на temperature и на effort «minimal» — это параметры Gemini."""
    body = {
        "model": model,
        "messages": messages,
        "max_tokens": settings.max_tokens,
        "usage": {"include": True},
    }
    if model.startswith("anthropic/"):
        body["reasoning"] = {"effort": "low", "exclude": True}
    else:
        body["temperature"] = settings.temperature
        body["reasoning"] = {"effort": "minimal", "exclude": True}
    return body


async def _call(client: httpx.AsyncClient, model: str, messages: list[dict]) -> str:
    resp = await client.post(
        URL,
        headers={
            "Authorization": "Bearer %s" % settings.openrouter_key,
            "HTTP-Referer": "https://msproduct.ru",
            "X-Title": "DIVO Motors AI manager",
        },
        json=_openrouter_body(model, messages),
    )
    if resp.status_code != 200:
        raise LlmError("%s: HTTP %s %s" % (model, resp.status_code, resp.text[:300]))
    data = resp.json()
    choices = data.get("choices") or []
    if not choices:
        raise LlmError("%s: пустой ответ %s" % (model, str(data)[:300]))
    choice = choices[0]
    usage = data.get("usage") or {}
    details = usage.get("prompt_tokens_details") or {}
    log.info(
        "%s finish=%s tokens in=%s out=%s кэш чтение=%s запись=%s цена=%s",
        model,
        choice.get("finish_reason"),
        usage.get("prompt_tokens"),
        usage.get("completion_tokens"),
        details.get("cached_tokens"),
        details.get("cache_write_tokens"),
        usage.get("cost"),
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


async def key_budget() -> dict:
    """Остаток по ключу OpenRouter: {'limit': .., 'remaining': .., 'usage': ..}.

    Лимит ключа - главная причина полного молчания бота: 403 приходит на любую
    модель сразу, запасной вариант не спасает.
    """
    if not settings.openrouter_key:
        return {}
    async with httpx.AsyncClient(timeout=30, proxy=settings.llm_proxy or None) as client:
        resp = await client.get(
            "https://openrouter.ai/api/v1/key",
            headers={"Authorization": "Bearer %s" % settings.openrouter_key},
        )
    if resp.status_code != 200:
        raise LlmError("статус ключа: HTTP %s %s" % (resp.status_code, resp.text[:200]))
    data = resp.json().get("data") or {}
    return {
        "limit": data.get("limit"),
        "remaining": data.get("limit_remaining"),
        "usage": data.get("usage"),
    }


async def reply(system: str, history: list[dict]) -> str:
    """history — [{role: user|assistant, content: ...}] в порядке диалога."""
    chain = _chain()
    if not chain:
        raise LlmError("не настроен ни один ключ: OPENROUTER_API_KEY или GEMINI_API_KEY")
    plain = system.replace(prompt.CACHE_SPLIT, "\n\n")

    last: Exception | None = None
    async with httpx.AsyncClient(timeout=90, proxy=settings.llm_proxy or None) as client:
        for backend, model in chain:
            try:
                if backend == "gemini":
                    text = await _call_gemini(client, model, plain, history)
                else:
                    text = await _call(
                        client, model, [_system_message(model, system)] + history
                    )
                if text.strip():
                    return text.strip()
                last = LlmError("%s/%s: ответ пустой строкой" % (backend, model))
            except (httpx.HTTPError, LlmError) as exc:
                log.warning("модель %s/%s не ответила: %s", backend, model, exc)
                last = exc
    raise LlmError("все модели не ответили: %s" % last)
