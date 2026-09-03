#!/usr/bin/env python3
"""Выполнить команды на сервере Keris по SSH (диагностика и мелкие правки).

Запуск:
    KERIS_DEPLOY_HOST=... KERIS_DEPLOY_PASSWORD=... python3 remote.py "команда" ["команда2" ...]
"""
from __future__ import annotations

import os
import sys
import time

import paramiko

HOST = os.environ.get("KERIS_DEPLOY_HOST", "")
USER = os.environ.get("KERIS_DEPLOY_USER", "root")
PASSWORD = os.environ.get("KERIS_DEPLOY_PASSWORD", "")


def connect(attempts: int = 8) -> paramiko.SSHClient:
    """Канал до хоста рвётся с «Error reading SSH protocol banner» — коннектимся с запасом."""
    last = None
    for i in range(attempts):
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(HOST, username=USER, password=PASSWORD, timeout=60,
                           banner_timeout=90, auth_timeout=60,
                           allow_agent=False, look_for_keys=False)
            return client
        except Exception as e:  # noqa: BLE001 — сеть до хоста нестабильна
            last = e
            print(f"  попытка {i + 1}: {type(e).__name__}: {e}", file=sys.stderr)
            time.sleep(4)
    raise SystemExit(f"не удалось подключиться: {last}")


def main() -> int:
    if not HOST or not PASSWORD or len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        return 1
    client = connect()
    code = 0
    for cmd in sys.argv[1:]:
        _, stdout, stderr = client.exec_command(cmd)
        rc = stdout.channel.recv_exit_status()
        out, err = stdout.read().decode(), stderr.read().decode()
        print(f"$ {cmd}")
        if out.strip():
            print(out.rstrip())
        if err.strip():
            print("  stderr:", err.rstrip())
        code = code or rc
    client.close()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
