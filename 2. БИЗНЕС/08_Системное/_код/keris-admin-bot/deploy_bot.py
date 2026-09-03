#!/usr/bin/env python3
"""Быстрая доставка bot.py на сервер и перезапуск сервиса (без полного provision).

Опционально проставляет whitelist: KARINA_TELEGRAM_IDS переписывается в обоих .env.

Запуск:
    KERIS_DEPLOY_HOST=... KERIS_DEPLOY_PASSWORD=... [KARINA_TELEGRAM_IDS=1,2] python3 deploy_bot.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import paramiko

HOST = os.environ.get("KERIS_DEPLOY_HOST", "")
USER = os.environ.get("KERIS_DEPLOY_USER", "root")
PASSWORD = os.environ.get("KERIS_DEPLOY_PASSWORD", "")
IDS = os.environ.get("KARINA_TELEGRAM_IDS", "")

BOT_DIR = "/root/keris-admin-bot"
APP_DIR = "/root/keris-server"


def connect(attempts: int = 8) -> paramiko.SSHClient:
    last = None
    for i in range(attempts):
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(HOST, username=USER, password=PASSWORD, timeout=60,
                           banner_timeout=90, auth_timeout=60, allow_agent=False, look_for_keys=False)
            return client
        except Exception as e:  # noqa: BLE001 — канал до хоста нестабилен
            last = e
            print(f"  подключение, попытка {i + 1}: {type(e).__name__}: {e}", file=sys.stderr)
            time.sleep(4)
    raise SystemExit(f"не удалось подключиться: {last}")


def run(client: paramiko.SSHClient, cmd: str) -> str:
    _, stdout, stderr = client.exec_command(cmd)
    stdout.channel.recv_exit_status()
    out, err = stdout.read().decode(), stderr.read().decode()
    print(f"$ {cmd}")
    if out.strip():
        print(out.rstrip())
    if err.strip():
        print("  stderr:", err.rstrip())
    return out


def main() -> int:
    if not HOST or not PASSWORD:
        print(__doc__, file=sys.stderr)
        return 1
    client = connect()
    sftp = client.open_sftp()
    sftp.put(str(Path(__file__).with_name("bot.py")), f"{BOT_DIR}/bot.py")
    print("bot.py загружен")

    if IDS:
        # whitelist нужен обоим: боту (команды) и серверу (push-уведомления)
        for path in (f"{BOT_DIR}/.env", f"{APP_DIR}/.env"):
            run(client, f"sed -i '/^KARINA_TELEGRAM_IDS=/d' {path} && echo 'KARINA_TELEGRAM_IDS={IDS}' >> {path}")
        run(client, "systemctl restart keris-server")
        print(f"whitelist: {IDS}")

    run(client, "systemctl restart keris-admin-bot; sleep 2; systemctl is-active keris-admin-bot")
    run(client, "journalctl -u keris-admin-bot -n 5 --no-pager")
    sftp.close()
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
