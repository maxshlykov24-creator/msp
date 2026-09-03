#!/usr/bin/env python3
"""Развёртывание бота фото-отчётов (keris-staff-bot) на VPS.

Отдельный сервис от бота Карины: у неё управление и уведомления, здесь — только
фото до/после. Токен и whitelist берутся из окружения, в git не попадают.

    KERIS_DEPLOY_PASSWORD=... STAFF_BOT_TOKEN=... STAFF_TELEGRAM_IDS=... \
    ADMIN_API_KEY=... python3 deploy.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import paramiko

HOST = os.environ.get("KERIS_DEPLOY_HOST", "213.165.44.164")
USER = os.environ.get("KERIS_DEPLOY_USER", "root")
PASSWORD = os.environ.get("KERIS_DEPLOY_PASSWORD", "")
STAFF_BOT_TOKEN = os.environ.get("STAFF_BOT_TOKEN", "").strip()
STAFF_TELEGRAM_IDS = os.environ.get("STAFF_TELEGRAM_IDS", "").strip()
ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY", "").strip()

ROOT = Path(__file__).resolve().parent
REMOTE_DIR = "/root/keris-staff-bot"


def run(client: paramiko.SSHClient, cmd: str) -> tuple[int, str, str]:
    _, stdout, stderr = client.exec_command(cmd)
    code = stdout.channel.recv_exit_status()
    return code, stdout.read().decode(), stderr.read().decode()


def main() -> int:
    if not PASSWORD:
        print("KERIS_DEPLOY_PASSWORD не задан", file=sys.stderr)
        return 1

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASSWORD, timeout=20)

    run(client, f"mkdir -p {REMOTE_DIR}")
    sftp = client.open_sftp()
    sftp.put(str(ROOT / "bot.py"), f"{REMOTE_DIR}/bot.py")
    sftp.put(str(ROOT / ".env.example"), f"{REMOTE_DIR}/.env.example")
    sftp.put(str(ROOT / "keris-staff-bot.service"), "/etc/systemd/system/keris-staff-bot.service")
    sftp.close()

    env_body = "\n".join([
        f"STAFF_BOT_TOKEN={STAFF_BOT_TOKEN}",
        f"STAFF_TELEGRAM_IDS={STAFF_TELEGRAM_IDS}",
        "KERIS_SERVER_URL=http://127.0.0.1:8091",
        f"ADMIN_API_KEY={ADMIN_API_KEY}",
        f"STATE_FILE={REMOTE_DIR}/state.json",
    ]) + "\n"
    run(client, f"cat > {REMOTE_DIR}/.env << 'ENVEOF'\n{env_body}ENVEOF")
    run(client, f"chmod 600 {REMOTE_DIR}/.env")
    run(client, f"test -f {REMOTE_DIR}/state.json || echo '{{}}' > {REMOTE_DIR}/state.json")

    for cmd in ("systemctl daemon-reload", "systemctl enable keris-staff-bot"):
        code, out, err = run(client, cmd)
        if code != 0:
            print(err or out, file=sys.stderr)

    if STAFF_BOT_TOKEN:
        code, out, err = run(
            client,
            f'curl -s "https://api.telegram.org/bot{STAFF_BOT_TOKEN}/deleteWebhook?drop_pending_updates=true"',
        )
        print("deleteWebhook:", out or err)
        run(client, "systemctl restart keris-staff-bot")
        code, out, err = run(client, "systemctl is-active keris-staff-bot && journalctl -u keris-staff-bot -n 5 --no-pager")
        print(out or err)
    else:
        run(client, "systemctl stop keris-staff-bot 2>/dev/null; true")
        print("STAFF_BOT_TOKEN не передан — сервис установлен, .env без токена."
              " Добавьте токен и: systemctl restart keris-staff-bot")

    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
