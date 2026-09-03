#!/usr/bin/env python3
"""Развёртывание @kerisclubbot на VPS: код, .env, systemd.

Также прописывает CLIENT_BOT_TOKEN в keris-server (напоминания клиентам)
и обновляет статику онлайн-записи в /var/www/keris.

Запуск:
    KERIS_DEPLOY_HOST=194.87.118.214 KERIS_DEPLOY_PASSWORD=... \
    BOT_TOKEN=... python3 deploy.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import paramiko

HOST = os.environ.get("KERIS_DEPLOY_HOST", "194.87.118.214")
USER = os.environ.get("KERIS_DEPLOY_USER", "root")
PASSWORD = os.environ.get("KERIS_DEPLOY_PASSWORD", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
BOOKING_URL = os.environ.get("BOOKING_URL", f"https://{HOST}.sslip.io/").strip().rstrip("/") + "/"

ROOT = Path(__file__).resolve().parent
REMOTE_DIR = "/root/keris-bot"
WEB_ROOT = "/var/www/keris"
PROTO = ROOT.parent / "keris-online-zapis"


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


def main() -> int:
    if not PASSWORD or not BOT_TOKEN:
        print("нужны KERIS_DEPLOY_PASSWORD и BOT_TOKEN", file=sys.stderr)
        return 1

    client = connect()
    sftp = client.open_sftp()

    print("== keris-bot ==")
    run(client, f"mkdir -p {REMOTE_DIR}")
    sftp.put(str(ROOT / "bot.py"), f"{REMOTE_DIR}/bot.py")
    sftp.put(str(ROOT / "keris-bot.service"), "/etc/systemd/system/keris-bot.service")
    env = "\n".join([
        f"BOT_TOKEN={BOT_TOKEN}",
        "MANAGER_USERNAME=keris_chat",
        f"STATE_FILE={REMOTE_DIR}/state.json",
        f"BOOKING_URL={BOOKING_URL}",
    ]) + "\n"
    with sftp.open(f"{REMOTE_DIR}/.env", "w") as f:
        f.write(env)
    run(client, f"chmod 600 {REMOTE_DIR}/.env")
    run(client, f"test -f {REMOTE_DIR}/state.json || echo '{{}}' > {REMOTE_DIR}/state.json")

    print("== статика онлайн-записи ==")
    run(client, f"mkdir -p {WEB_ROOT}/legal")
    sftp.put(str(PROTO / "prototype.html"), f"{WEB_ROOT}/index.html")
    for name in ("telegram-web-app.js", "tilda-embed.html"):
        local = PROTO / name
        if local.exists():
            sftp.put(str(local), f"{WEB_ROOT}/{name}")
    legal = PROTO / "legal"
    if legal.is_dir():
        for page in sorted(legal.glob("*.html")):
            sftp.put(str(page), f"{WEB_ROOT}/legal/{page.name}")

    print("== CLIENT_BOT_TOKEN в keris-server (напоминания) ==")
    run(client, "sed -i '/^CLIENT_BOT_TOKEN=/d' /root/keris-server/.env 2>/dev/null; "
                f"echo 'CLIENT_BOT_TOKEN={BOT_TOKEN}' >> /root/keris-server/.env")

    # один токен = один long polling. Старый тестовый booking-bot останавливаем.
    run(client, "systemctl stop keris-booking-bot 2>/dev/null; systemctl disable keris-booking-bot 2>/dev/null; true")
    run(client, "systemctl daemon-reload")
    run(client, "systemctl enable --now keris-bot")
    run(client, "systemctl restart keris-server")
    run(client, "sleep 3; systemctl is-active keris-bot keris-server; "
                "journalctl -u keris-bot -n 6 --no-pager")

    sftp.close()
    client.close()
    print(f"\nOK @kerisclubbot · запись: {BOOKING_URL}")
    print(f"Deep-link: https://t.me/kerisclubbot?start=booking")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
