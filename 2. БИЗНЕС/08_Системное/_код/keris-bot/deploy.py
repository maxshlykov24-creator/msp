#!/usr/bin/env python3
"""Развёртывание keris-bot на VPS (stdlib + systemd). Токен — только в .env на сервере."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import paramiko

HOST = os.environ.get("KERIS_DEPLOY_HOST", "213.165.44.164")
USER = os.environ.get("KERIS_DEPLOY_USER", "root")
PASSWORD = os.environ.get("KERIS_DEPLOY_PASSWORD", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()

ROOT = Path(__file__).resolve().parent
REMOTE_DIR = "/root/keris-bot"


def run(client: paramiko.SSHClient, cmd: str) -> tuple[int, str, str]:
    _, stdout, stderr = client.exec_command(cmd)
    code = stdout.channel.recv_exit_status()
    return code, stdout.read().decode(), stderr.read().decode()


def upload_sftp(sftp: paramiko.SFTPClient, local: Path, remote: str) -> None:
    sftp.put(str(local), remote)


def main() -> int:
    if not PASSWORD:
        print("KERIS_DEPLOY_PASSWORD не задан", file=sys.stderr)
        return 1

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASSWORD, timeout=20)

    run(client, f"mkdir -p {REMOTE_DIR}")
    sftp = client.open_sftp()
    upload_sftp(sftp, ROOT / "bot.py", f"{REMOTE_DIR}/bot.py")
    upload_sftp(sftp, ROOT / ".env.example", f"{REMOTE_DIR}/.env.example")
    upload_sftp(sftp, ROOT / "keris-bot.service", "/etc/systemd/system/keris-bot.service")
    sftp.close()

    env_lines = [
        f"BOT_TOKEN={BOT_TOKEN}",
        "MANAGER_USERNAME=keris_chat",
        f"STATE_FILE={REMOTE_DIR}/state.json",
    ]
    env_body = "\n".join(env_lines) + "\n"
    run(client, f"cat > {REMOTE_DIR}/.env << 'ENVEOF'\n{env_body}ENVEOF")
    run(client, f"chmod 600 {REMOTE_DIR}/.env")
    run(client, f"test -f {REMOTE_DIR}/state.json || echo '{{}}' > {REMOTE_DIR}/state.json")

    for cmd in (
        "systemctl daemon-reload",
        "systemctl enable keris-bot",
    ):
        code, out, err = run(client, cmd)
        if code != 0:
            print(err or out, file=sys.stderr)

    if BOT_TOKEN:
        code, out, err = run(
            client,
            f'curl -s "https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook?drop_pending_updates=true"',
        )
        print("deleteWebhook:", out or err)
        run(client, "systemctl restart keris-bot")
        code, out, err = run(client, "systemctl is-active keris-bot && journalctl -u keris-bot -n 5 --no-pager")
        print(out or err)
    else:
        run(client, "systemctl stop keris-bot 2>/dev/null; true")
        print("BOT_TOKEN не передан — сервис установлен, .env без токена. Добавьте токен и: systemctl restart keris-bot")

    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
