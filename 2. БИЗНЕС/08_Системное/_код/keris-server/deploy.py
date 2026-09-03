#!/usr/bin/env python3
"""Развёртывание keris-server на VPS: venv, зависимости, systemd.

Секреты — только через переменные окружения на машине, запускающей деплой,
и .env на сервере (не коммитятся). Использует пароль (paramiko), как
keris-bot/deploy.py и keris-booking-bot/deploy.py. Если у вас уже настроен
SSH-ключ на сервер, можно просто rsync + ssh вручную (см. README.md).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import paramiko

HOST = os.environ.get("KERIS_DEPLOY_HOST", "213.165.44.164")
USER = os.environ.get("KERIS_DEPLOY_USER", "root")
PASSWORD = os.environ.get("KERIS_DEPLOY_PASSWORD", "")

ROOT = Path(__file__).resolve().parent
REMOTE_DIR = "/root/keris-server"


def run(client: paramiko.SSHClient, cmd: str) -> tuple[int, str, str]:
    _, stdout, stderr = client.exec_command(cmd)
    code = stdout.channel.recv_exit_status()
    return code, stdout.read().decode(), stderr.read().decode()


def upload_dir(sftp: paramiko.SFTPClient, local: Path, remote: str) -> None:
    try:
        sftp.mkdir(remote)
    except IOError:
        pass
    for item in local.iterdir():
        if item.name in {"__pycache__", "keris_dev.db"} or item.name.endswith(".pyc"):
            continue
        rpath = f"{remote}/{item.name}"
        if item.is_dir():
            upload_dir(sftp, item, rpath)
        else:
            sftp.put(str(item), rpath)


def main() -> int:
    if not PASSWORD:
        print("KERIS_DEPLOY_PASSWORD не задан", file=sys.stderr)
        return 1

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASSWORD, timeout=20)

    run(client, f"mkdir -p {REMOTE_DIR}")
    sftp = client.open_sftp()
    sftp.put(str(ROOT / "requirements.txt"), f"{REMOTE_DIR}/requirements.txt")
    upload_dir(sftp, ROOT / "app", f"{REMOTE_DIR}/app")
    sftp.put(str(ROOT / "keris-server.service"), "/etc/systemd/system/keris-server.service")
    sftp.close()

    print("Установка venv + зависимостей…")
    for cmd in (
        "apt-get update -y && apt-get install -y python3-venv python3-pip postgresql",
        f"cd {REMOTE_DIR} && python3 -m venv venv",
        f"{REMOTE_DIR}/venv/bin/pip install --upgrade pip",
        f"{REMOTE_DIR}/venv/bin/pip install -r {REMOTE_DIR}/requirements.txt",
        f"test -f {REMOTE_DIR}/.env || cp {REMOTE_DIR}/../keris-server.env.example {REMOTE_DIR}/.env 2>/dev/null; true",
        "systemctl daemon-reload",
        "systemctl enable keris-server",
        "systemctl restart keris-server",
    ):
        code, out, err = run(client, cmd)
        print(f"$ {cmd}\n{out}{err}")

    code, out, err = run(client, "sleep 2 && systemctl is-active keris-server && curl -s http://127.0.0.1:8091/health")
    print(out or err)

    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
