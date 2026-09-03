"""Минимальный AMI-клиент Asterisk: логин, одна команда Originate, выход.

Полноценная библиотека здесь избыточна — хабу нужна ровно одна команда, зато
без лишней зависимости и фонового соединения. AMI на Asterisk слушает только для
IP хаба (см. asterisk/manager.conf), пароль — в `.env`.

Почему Originate именно так:
`Channel: Local/<номер>@lb-ai-outbound` набирает клиента с нашего DID, а когда он
берёт трубку, Asterisk выполняет `Context/Exten` — там мы соединяем его с номером
голосового агента Pleep. Обратный порядок (сначала поднять Pleep) заставил бы
агента говорить в тишину, пока идут гудки.
"""
from __future__ import annotations

import logging
import socket
import time
import uuid
from typing import Any

from app.config import settings

log = logging.getLogger("ami")


class AmiError(Exception):
    """AMI недоступен или отклонил команду."""


def _pack(fields: list[tuple[str, str]]) -> bytes:
    body = "".join(f"{k}: {v}\r\n" for k, v in fields) + "\r\n"
    return body.encode("utf-8")


def _read_block(sock: socket.socket, deadline: float) -> str:
    """Читает до пустой строки — один пакет ответа AMI."""
    buf = b""
    while b"\r\n\r\n" not in buf:
        if time.monotonic() > deadline:
            raise AmiError("timeout while reading AMI response")
        try:
            chunk = sock.recv(4096)
        except socket.timeout as exc:
            raise AmiError("timeout while reading AMI response") from exc
        if not chunk:
            raise AmiError("AMI closed connection")
        buf += chunk
    return buf.decode("utf-8", "replace")


def _response_ok(block: str) -> bool:
    for line in block.splitlines():
        if line.lower().startswith("response:"):
            return line.split(":", 1)[1].strip().lower() == "success"
    return False


def originate(phone: str, variables: dict[str, Any] | None = None,
              caller_id: str = "", ring_sec: int | None = None) -> str:
    """Инициирует звонок клиенту с мостом на Pleep. Возвращает ActionID.

    Async: true — AMI отвечает сразу, не держа соединение все 45 секунд дозвона.
    Итог звонка приходит обычным путём: hangup-handler → `/internal/call/finished`.
    """
    if not settings.ami_secret:
        raise AmiError("AMI_SECRET не задан")
    action_id = f"lb-ai-{uuid.uuid4().hex[:12]}"
    timeout_ms = int((ring_sec or settings.ai_call_ring_sec) * 1000)
    fields: list[tuple[str, str]] = [
        ("Action", "Originate"),
        ("ActionID", action_id),
        ("Channel", f"Local/{phone}@{settings.ami_outbound_context}"),
        ("Context", settings.ami_connect_context),
        ("Exten", "s"),
        ("Priority", "1"),
        ("Timeout", str(timeout_ms)),
        ("Async", "true"),
    ]
    if caller_id:
        fields.append(("CallerID", caller_id))
    # «__» — наследуемая переменная: Local-канал отдаёт её обеим половинам, иначе
    # диалплан на стороне дозвона клиента не увидел бы ни сделку, ни номер агента
    for key, value in (variables or {}).items():
        fields.append(("Variable", f"__{key}={value}"))

    deadline = time.monotonic() + settings.ami_timeout
    try:
        sock = socket.create_connection((settings.ami_host, settings.ami_port),
                                        timeout=settings.ami_timeout)
    except OSError as exc:
        raise AmiError(f"нет связи с AMI {settings.ami_host}:{settings.ami_port}: {exc}") from exc
    try:
        sock.settimeout(settings.ami_timeout)
        _read_block(sock, deadline)  # приветствие «Asterisk Call Manager/…»
        sock.sendall(_pack([("Action", "Login"),
                            ("Username", settings.ami_user),
                            ("Secret", settings.ami_secret)]))
        if not _response_ok(_read_block(sock, deadline)):
            raise AmiError("AMI отклонил логин (пользователь/пароль)")
        sock.sendall(_pack(fields))
        block = _read_block(sock, deadline)
        if not _response_ok(block):
            raise AmiError(f"Originate отклонён: {block.strip()[:200]}")
        try:
            sock.sendall(_pack([("Action", "Logoff")]))
        except OSError:
            pass
    finally:
        sock.close()
    log.info("ami originate %s -> %s (%s)", phone, settings.pleep_number, action_id)
    return action_id
