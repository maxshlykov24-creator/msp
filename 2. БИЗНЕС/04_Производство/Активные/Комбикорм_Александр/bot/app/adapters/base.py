"""Граница мессенджера. Ядро (parser/db/operations/ui) не знает про Telegram/MAX.

Конкретный адаптер обязан уметь: показать текст с клавиатурой, отредактировать
сообщение, скачать голосовое в bytes. Telegram-адаптер — в ``telegram.py``;
MAX-адаптер добавляется позже без изменения ядра.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..ui import Keyboard


class MessengerAdapter(ABC):
    @abstractmethod
    async def run(self) -> None:
        ...

    @abstractmethod
    async def download_voice(self, file_ref: str) -> bytes:
        ...
