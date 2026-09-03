#!/usr/bin/env python3
"""Деплой статики онлайн-записи на сайт-VPS (kerisclub-analytics)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import paramiko

HOST = os.environ.get("KERIS_SITE_HOST", "194.87.118.214")
USER = os.environ.get("KERIS_SITE_USER", "root")
PASSWORD = os.environ.get("KERIS_SITE_PASSWORD", "")
REMOTE_WWW = "/var/www/keris-booking"

ROOT = Path(__file__).resolve().parent
SITE_DIR = ROOT.parent / "keris-online-zapis"
PROTO = SITE_DIR / "prototype.html"
TG_JS = SITE_DIR / "telegram-web-app.js"


def main() -> int:
    if not PASSWORD:
        print("KERIS_SITE_PASSWORD не задан", file=sys.stderr)
        return 1
    if not PROTO.is_file():
        print(f"нет файла: {PROTO}", file=sys.stderr)
        return 1
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASSWORD, timeout=25)
    sftp = client.open_sftp()
    sftp.put(str(PROTO), f"{REMOTE_WWW}/index.html")
    if TG_JS.is_file():
        sftp.put(str(TG_JS), f"{REMOTE_WWW}/telegram-web-app.js")
    sftp.close()
    _, out, err = client.exec_command(
        f"chmod 644 {REMOTE_WWW}/index.html {REMOTE_WWW}/telegram-web-app.js 2>/dev/null; "
        f"systemctl reload nginx"
    )
    out.channel.recv_exit_status()
    print(out.read().decode() or err.read().decode() or "OK site deployed")
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
