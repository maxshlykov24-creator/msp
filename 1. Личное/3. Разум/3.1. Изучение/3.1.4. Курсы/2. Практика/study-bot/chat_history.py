"""
История диалога LLM в JSON на диске (Распределение/llm/history.json).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import LLM_SUMMARIZE_BATCH, LLM_SUMMARIZE_THRESHOLD

logger = logging.getLogger(__name__)

_MAX_STORED_MESSAGES = 500
_ROLLING_KIND = "rolling_summary"


class ChatHistory:
    """Персистентная история user/assistant + сжатые выжимки для Groq."""

    def __init__(self, distribution_folder: str) -> None:
        self._dir = Path(distribution_folder) / "llm"
        self._path = self._dir / "history.json"

    def _ensure_dir(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)

    def _load_all(self) -> list[dict[str, Any]]:
        self._ensure_dir()
        if not self._path.is_file():
            return []
        try:
            raw = self._path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("chat_history: не прочитать %s: %s", self._path, e)
            return []
        if not isinstance(data, list):
            return []
        return data

    def _save_all(self, items: list[dict[str, Any]]) -> None:
        self._ensure_dir()
        self._path.write_text(
            json.dumps(items, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def rolling_summary_contents(self) -> list[str]:
        """Тексты выжимок в порядке появления в файле."""
        out: list[str] = []
        for it in self._load_all():
            if not isinstance(it, dict):
                continue
            if it.get("kind") != _ROLLING_KIND:
                continue
            if it.get("role") != "system":
                continue
            c = (it.get("content") or "").strip()
            if c:
                out.append(c)
        return out

    def recent(self, n_pairs: int) -> list[dict[str, str]]:
        """
        Последние n_pairs пар user/assistant (без выжимок system).
        """
        all_items = self._load_all()
        ua_only: list[dict[str, str]] = []
        for it in all_items:
            if not isinstance(it, dict):
                continue
            role = it.get("role")
            content = it.get("content")
            if role in ("user", "assistant") and isinstance(content, str):
                ua_only.append({"role": role, "content": content})
        cap = max(0, n_pairs) * 2
        if cap == 0:
            return []
        slice_items = ua_only[-cap:]
        return slice_items

    async def summarize_if_needed(self) -> bool:
        """
        Если записей больше порога — сжать первые LLM_SUMMARIZE_BATCH в одну выжимку.
        Возвращает True, если файл истории изменился.
        """
        from llm_client import summarize_history_batch

        items = self._load_all()
        threshold = max(2, int(LLM_SUMMARIZE_THRESHOLD))
        batch_size = max(1, int(LLM_SUMMARIZE_BATCH))
        if len(items) <= threshold:
            return False

        batch = items[:batch_size]
        rest = items[batch_size:]
        summary_text = await summarize_history_batch(batch)
        if not summary_text:
            return False

        new_head = [
            {
                "role": "system",
                "content": summary_text,
                "kind": _ROLLING_KIND,
                "ts": datetime.now(timezone.utc).isoformat(),
            }
        ]
        merged = new_head + rest
        if len(merged) > _MAX_STORED_MESSAGES:
            merged = merged[-_MAX_STORED_MESSAGES:]
        self._save_all(merged)
        logger.info(
            "chat_history: саммаризация, удалено записей=%s, осталось=%s",
            len(batch),
            len(merged),
        )
        return True

    def append(self, role: str, content: str) -> None:
        if role not in ("user", "assistant"):
            raise ValueError(role)
        text = (content or "").strip()
        if not text:
            return
        items = self._load_all()
        items.append(
            {
                "role": role,
                "content": text,
                "ts": datetime.now(timezone.utc).isoformat(),
            }
        )
        if len(items) > _MAX_STORED_MESSAGES:
            items = items[-_MAX_STORED_MESSAGES:]
        self._save_all(items)

    def reset(self) -> None:
        self._ensure_dir()
        if self._path.is_file():
            self._path.unlink(missing_ok=True)
        logger.info("chat_history: сброшено %s", self._path)
