"""MAX-адаптер (заготовка следующей фазы).

Ядро (db, operations, pipeline, ui, stt) переиспользуется без изменений. Здесь
останется только специфика MAX Bot API: приём событий ``message_created``,
скачивание ``attachments[type=audio].payload.url``, отправка сообщений и
inline-кнопок ``callback``, правка через ``PATCH /messages/{id}``.

Включается через MESSENGER=max в .env (см. main.py). Пока не реализован —
подключаем после стабильной работы Telegram-версии.
"""
from __future__ import annotations

from .base import MessengerAdapter


class MaxBot(MessengerAdapter):
    def __init__(self, config, conn, matcher, nexara):
        self.config = config
        self.conn = conn
        self.matcher = matcher
        self.nexara = nexara

    async def run(self) -> None:
        raise NotImplementedError(
            "MAX-адаптер в разработке. Сейчас используйте MESSENGER=telegram."
        )

    async def download_voice(self, file_ref: str) -> bytes:
        raise NotImplementedError
