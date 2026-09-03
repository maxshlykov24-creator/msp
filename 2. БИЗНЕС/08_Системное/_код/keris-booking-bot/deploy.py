#!/usr/bin/env python3
"""Деплой прототипа записи + Telegram Mini App бота на VPS Keris."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import paramiko

HOST = os.environ.get("KERIS_DEPLOY_HOST", "213.165.44.164")
USER = os.environ.get("KERIS_DEPLOY_USER", "root")
PASSWORD = os.environ.get("KERIS_DEPLOY_PASSWORD", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
WEBAPP_URL = os.environ.get("WEBAPP_URL", "https://213.165.44.164.sslip.io/").strip()

ROOT = Path(__file__).resolve().parent
# .../2. БИЗНЕС/08_Системное/_код/keris-booking-bot → parents[2] = 2. БИЗНЕС
VAULT = ROOT.parents[2]
PROTO = ROOT.parent / "keris-online-zapis" / "prototype.html"
REMOTE_BOT = "/root/keris-booking-bot"
REMOTE_WWW = "/var/www/keris-booking"


def run(client: paramiko.SSHClient, cmd: str) -> tuple[int, str, str]:
    _, stdout, stderr = client.exec_command(cmd)
    code = stdout.channel.recv_exit_status()
    return code, stdout.read().decode(), stderr.read().decode()


def main() -> int:
    if not PASSWORD:
        print("KERIS_DEPLOY_PASSWORD не задан", file=sys.stderr)
        return 1
    if not BOT_TOKEN:
        print("BOT_TOKEN не задан", file=sys.stderr)
        return 1
    if not PROTO.is_file():
        print(f"нет прототипа: {PROTO}", file=sys.stderr)
        return 1

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASSWORD, timeout=20)

    run(client, f"mkdir -p {REMOTE_BOT} {REMOTE_WWW}")
    sftp = client.open_sftp()
    sftp.put(str(PROTO), f"{REMOTE_WWW}/index.html")
    sftp.put(str(ROOT / "bot.py"), f"{REMOTE_BOT}/bot.py")
    sftp.put(str(ROOT / ".env.example"), f"{REMOTE_BOT}/.env.example")
    sftp.put(str(ROOT / "keris-booking-bot.service"), "/etc/systemd/system/keris-booking-bot.service")
    sftp.close()

    env = (
        f"BOT_TOKEN={BOT_TOKEN}\n"
        f"WEBAPP_URL={WEBAPP_URL.rstrip('/')}/\n"
        "MENU_TEXT=Запись\n"
    )
    run(client, f"cat > {REMOTE_BOT}/.env << 'ENVEOF'\n{env}ENVEOF")
    run(client, f"chmod 600 {REMOTE_BOT}/.env")
    run(client, f"chmod 644 {REMOTE_WWW}/index.html")

    for cmd in (
        "systemctl daemon-reload",
        "systemctl enable keris-booking-bot",
        "systemctl restart keris-booking-bot",
        "systemctl reload nginx",
    ):
        code, out, err = run(client, cmd)
        if code != 0:
            print(cmd, err or out, file=sys.stderr)

    code, out, err = run(
        client,
        "systemctl is-active keris-booking-bot && journalctl -u keris-booking-bot -n 8 --no-pager",
    )
    print(out or err)
    code, out, err = run(client, f'curl -4 -sS -o /dev/null -w "%{{http_code}}" --max-time 10 "{WEBAPP_URL.rstrip("/")}/"')
    print("webapp HTTP", out or err)

    # menu button + sanity getMe
    code, out, err = run(
        client,
        f'curl -4 -sS --max-time 15 -X POST "https://api.telegram.org/bot{BOT_TOKEN}/setChatMenuButton" '
        f'-H "Content-Type: application/json" '
        f'-d \'{{"menu_button":{{"type":"web_app","text":"Запись","web_app":{{"url":"{WEBAPP_URL.rstrip("/")}/"}}}}}}\'',
    )
    print("setChatMenuButton:", out or err)

    client.close()
    print(f"OK Mini App: {WEBAPP_URL}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
