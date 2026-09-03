#!/usr/bin/env python3
"""Развёртывание зеркала Keris Club в MAX на RU-VPS (рядом с keris-server).

Telegram-бот остаётся на EU IPv6. MAX API российский, polling идёт с RU.

Запуск:
    KERIS_DEPLOY_PASSWORD=... MAX_BOT_TOKEN=... python3 deploy_max.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import paramiko

HOST = os.environ.get("KERIS_DEPLOY_HOST", "194.87.118.214")
USER = os.environ.get("KERIS_DEPLOY_USER", "root")
PASSWORD = os.environ.get("KERIS_DEPLOY_PASSWORD", "") or os.environ.get("KERIS_RU_PASSWORD", "")
MAX_BOT_TOKEN = os.environ.get("MAX_BOT_TOKEN", "").strip()
BOOKING_URL = os.environ.get("BOOKING_URL", f"https://{HOST}.sslip.io/").strip().rstrip("/") + "/"
MANAGER_MAX_URL = os.environ.get("MANAGER_MAX_URL", "").strip()

ROOT = Path(__file__).resolve().parent
REMOTE_DIR = "/root/keris-max-bot"


def connect(attempts: int = 8) -> paramiko.SSHClient:
    last = None
    for i in range(attempts):
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(HOST, username=USER, password=PASSWORD, timeout=60,
                           banner_timeout=90, auth_timeout=60,
                           allow_agent=False, look_for_keys=False)
            return client
        except Exception as e:  # noqa: BLE001
            last = e
            print(f"  подключение, попытка {i + 1}: {type(e).__name__}: {e}", file=sys.stderr)
            time.sleep(4)
    raise SystemExit(f"не удалось подключиться: {last}")


def run(client: paramiko.SSHClient, cmd: str) -> tuple[int, str, str]:
    _, stdout, stderr = client.exec_command(cmd)
    code = stdout.channel.recv_exit_status()
    out, err = stdout.read().decode(), stderr.read().decode()
    print(f"$ {cmd}")
    if out.strip():
        print(out.rstrip())
    if err.strip():
        print("  stderr:", err.rstrip())
    return code, out, err


def put_dir_file(sftp, local: Path, remote: str) -> None:
    sftp.put(str(local), remote)


def main() -> int:
    if not PASSWORD or not MAX_BOT_TOKEN:
        print("нужны KERIS_DEPLOY_PASSWORD и MAX_BOT_TOKEN", file=sys.stderr)
        return 1

    client = connect()
    sftp = client.open_sftp()

    print("== keris-max-bot ==")
    run(client, f"mkdir -p {REMOTE_DIR}")
    for name in ("max_bot.py", "phone.py"):
        put_dir_file(sftp, ROOT / name, f"{REMOTE_DIR}/{name}")
    sftp.put(str(ROOT / "keris-max-bot.service"), "/etc/systemd/system/keris-max-bot.service")
    env_lines = [
        f"MAX_BOT_TOKEN={MAX_BOT_TOKEN}",
        f"STATE_FILE={REMOTE_DIR}/state.json",
        f"BOOKING_URL={BOOKING_URL}",
        f"KERIS_SERVER_URL={BOOKING_URL.rstrip('/')}",
        f"SITE_URL=https://kerisclub.ru",
    ]
    if MANAGER_MAX_URL:
        env_lines.append(f"MANAGER_MAX_URL={MANAGER_MAX_URL}")
    with sftp.open(f"{REMOTE_DIR}/.env", "w") as f:
        f.write("\n".join(env_lines) + "\n")
    run(client, f"chmod 600 {REMOTE_DIR}/.env")
    run(client, f"test -f {REMOTE_DIR}/state.json || echo '{{}}' > {REMOTE_DIR}/state.json")

    print("== MAX_BOT_TOKEN в keris-server (напоминания) ==")
    run(client, "sed -i '/^MAX_BOT_TOKEN=/d' /root/keris-server/.env 2>/dev/null; "
                f"echo 'MAX_BOT_TOKEN={MAX_BOT_TOKEN}' >> /root/keris-server/.env")

    print("== код keris-server (bind + напоминания MAX) ==")
    server_local = ROOT.parent / "keris-server"
    run(client, "mkdir -p /root/keris-server/app")
    for rel in (
        "app/booking_logic.py",
        "app/models.py",
        "app/migrate.py",
        "app/config.py",
        "app/main.py",
        "app/sync.py",
        "app/reminders.py",
        "app/telegram_bind.py",
        "app/max_bind.py",
        "app/max_http.py",
    ):
        put_dir_file(sftp, server_local / rel, f"/root/keris-server/{rel}")

    run(client, "systemctl daemon-reload")
    run(client, "systemctl enable --now keris-max-bot")
    run(client, "systemctl restart keris-max-bot keris-server")
    run(client, "sleep 3; systemctl is-active keris-max-bot keris-server; "
                "journalctl -u keris-max-bot -n 12 --no-pager")

    sftp.close()
    client.close()
    print(f"\nOK MAX-бот · запись: {BOOKING_URL}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
