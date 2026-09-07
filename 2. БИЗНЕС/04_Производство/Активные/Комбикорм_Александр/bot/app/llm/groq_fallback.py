"""Groq LLM-фолбэк: только для строк «❓», когда код не распознал позицию.

Строгий JSON-ответ, кандидаты из RapidFuzz — не весь каталог (731 поз.).
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Optional

import aiohttp

log = logging.getLogger(__name__)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


@dataclass
class LlmMatch:
    product_id: Optional[int]
    qty: Optional[float]
    unit: str = "bag"
    confident: bool = False


class GroqFallback:
    def __init__(self, api_key: str, model: str = "llama-3.3-70b-versatile"):
        self.api_key = (api_key or "").strip()
        self.model = (model or "llama-3.3-70b-versatile").strip()

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    async def ping(self) -> tuple[bool, str]:
        """Проверка ключа (для деплоя/диагностики)."""
        if not self.available:
            return False, "GROQ_API_KEY пустой"
        try:
            text = await self._chat(
                "Ответь одним словом: ок",
                max_tokens=8,
                json_mode=False,
            )
            return bool(text.strip()), text.strip() or "пустой ответ"
        except Exception as e:  # noqa: BLE001
            return False, str(e)

    async def resolve_line(
        self,
        raw_line: str,
        candidates: list,
        *,
        parsed_qty: Optional[float] = None,
        parsed_unit: str = "bag",
    ) -> Optional[LlmMatch]:
        if not self.available:
            return None

        cand_block = "\n".join(
            f"- id={c.product_id}: {c.name}"
            for c in candidates[:10]
        ) or "- нет кандидатов"

        system = (
            "Ты извлекаешь товар из голосовой строки продавца комбикорма/зерна. "
            "Ответь ТОЛЬКО валидным JSON без markdown и комментариев:\n"
            '{"product_id": число|null, "qty": число|null, "unit": "bag"|"kg", "confident": true|false}\n'
            "product_id — только id из списка кандидатов. "
            "Если не уверен — product_id=null, confident=false. "
            "qty — количество мешков или кг; если в строке уже есть — используй его."
        )
        user = (
            f"Строка: {raw_line}\n"
            f"Разобрано кодом: qty={parsed_qty}, unit={parsed_unit}\n"
            f"Кандидаты:\n{cand_block}"
        )

        try:
            content = await self._chat(system + "\n\n" + user, max_tokens=120, json_mode=True)
            obj = self._parse_json(content)
            if not obj:
                return None
            pid = obj.get("product_id")
            if pid is not None:
                pid = int(pid)
            qty = obj.get("qty")
            if qty is not None:
                qty = float(qty)
            unit = obj.get("unit") or parsed_unit
            if unit not in ("bag", "kg"):
                unit = parsed_unit
            confident = bool(obj.get("confident"))
            if pid is None or not confident:
                return LlmMatch(product_id=None, qty=qty or parsed_qty, unit=unit, confident=False)
            return LlmMatch(product_id=pid, qty=qty or parsed_qty, unit=unit, confident=True)
        except Exception:  # noqa: BLE001
            log.exception("Groq fallback failed for %r", raw_line[:80])
            return None

    async def _chat(self, prompt: str, *, max_tokens: int = 120, json_mode: bool = False) -> str:
        payload: dict = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(GROQ_URL, json=payload, headers=headers) as resp:
                body = await resp.text()
                if resp.status != 200:
                    raise RuntimeError(f"Groq HTTP {resp.status}: {body[:300]}")
                data = json.loads(body)
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError(f"Groq: нет choices: {str(data)[:300]}")
        content = (choices[0].get("message") or {}).get("content") or ""
        return content.strip()

    @staticmethod
    def _parse_json(text: str) -> Optional[dict[str, Any]]:
        text = text.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            m = re.search(r"\{[^{}]*\}", text, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group(0))
                except json.JSONDecodeError:
                    return None
        return None
