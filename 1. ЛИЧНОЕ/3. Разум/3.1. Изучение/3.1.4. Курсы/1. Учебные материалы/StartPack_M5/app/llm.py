"""Опциональная оценка звонка через AssemblyAI LLM Gateway (OpenAI-совместимый, ключ — тот же AssemblyAI).

Универсальный скелет оценки (prompts/eval_skeleton.txt) + КОНТЕКСТ КОМПАНИИ ученика → строгий JSON.
Контекст компании докручивает оценку под конкретный бизнес (продукт, ICP, чек-лист, запрещённые фразы).
"""
import json
import re
from pathlib import Path

from config import settings
from net import proxied_client

GW_URL = "https://llm-gateway.assemblyai.com/v1/chat/completions"
PROMPTS = Path(__file__).parent / "prompts"


def _skeleton() -> str:
    # Стиль оценки выбирается PRESET (coach|auditor); по умолчанию — общий скелет.
    for name in (f"{settings.preset}.txt", "eval_skeleton.txt"):
        p = PROMPTS / name
        if p.exists():
            return p.read_text(encoding="utf-8")
    raise RuntimeError("Не найден файл скелета оценки в app/prompts/")


def _strip_fences(content: str) -> str:
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.MULTILINE).strip()
    i = s.find("{")
    return s[i:] if i > 0 else s


def _parse_json(content: str) -> dict:
    for cand in (content, _strip_fences(content)):
        try:
            return json.loads(cand)
        except (json.JSONDecodeError, TypeError):
            continue
    raise json.JSONDecodeError("LLM вернул неразбираемый JSON", content or "", 0)


def evaluate(transcript: str, company_context: str = "") -> dict:
    """Оценить звонок. company_context — текст из company_context.md (контекст бизнеса ученика)."""
    if not settings.assemblyai_api_key:
        raise RuntimeError("ASSEMBLYAI_API_KEY не задан в .env")
    user = f"КОНТЕКСТ КОМПАНИИ:\n{company_context}\n\nТРАНСКРИПТ ЗВОНКА:\n{transcript}"
    headers = {"Authorization": f"Bearer {settings.assemblyai_api_key}",
               "Content-Type": "application/json"}
    body = {
        "model": settings.llm_model,
        "messages": [
            {"role": "system", "content": _skeleton()},
            {"role": "user", "content": user},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
        "max_tokens": 4000,
    }
    with proxied_client(timeout=180) as cli:
        r = cli.post(GW_URL, headers=headers, json=body)
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
    return _parse_json(content)
