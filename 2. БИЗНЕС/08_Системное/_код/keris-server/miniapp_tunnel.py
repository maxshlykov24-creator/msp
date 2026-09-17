#!/usr/bin/env python3
"""Cloudflare-туннель только для Telegram Mini App.

Публичный sslip.io не меняет. Как только cloudflared выдаёт HTTPS-URL,
пишет его в /var/www/keris/miniapp-origin.txt и ставит кнопку «Запись»
у @kerisclubbot (токен из keris-server .env, в лог не печатает).
"""
from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import urllib.error
import urllib.request

_real_getaddrinfo = socket.getaddrinfo


def _telegram_ipv6_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):  # noqa: A002
    name = str(host or "").rstrip(".").lower()
    if name == "api.telegram.org" or name.endswith(".telegram.org"):
        return _real_getaddrinfo(host, port, socket.AF_INET6, type, proto, flags)
    return _real_getaddrinfo(host, port, family, type, proto, flags)


socket.getaddrinfo = _telegram_ipv6_getaddrinfo

URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
ORIGIN_FILE = "/var/www/keris/miniapp-origin.txt"
ENV_FILE = "/root/keris-server/.env"
CLOUDFLARED = os.environ.get("CLOUDFLARED_BIN", "/usr/local/bin/cloudflared")
TUNNEL_TARGET = os.environ.get("MINIAPP_TUNNEL_TARGET", "http://127.0.0.1:8088")


def _env(name: str) -> str:
    if not os.path.isfile(ENV_FILE):
        return ""
    prefix = name + "="
    with open(ENV_FILE, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith(prefix):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _slash(url: str) -> str:
    return url.rstrip("/") + "/"


def publish(url: str) -> None:
    public = _slash(url)
    tmp = ORIGIN_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(public + "\n")
    os.replace(tmp, ORIGIN_FILE)
    os.chmod(ORIGIN_FILE, 0o644)
    token = _env("CLIENT_BOT_TOKEN")
    if not token:
        print("miniapp-tunnel: URL записан, CLIENT_BOT_TOKEN нет — кнопку бота не ставлю",
              flush=True)
        return
    body = (
        '{"menu_button":{"type":"web_app","text":"Запись",'
        '"web_app":{"url":"%s"}}}' % public
    ).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/setChatMenuButton",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8", "replace")
        ok = '"ok":true' in raw.replace(" ", "")
        print(f"miniapp-tunnel: menu_button {'ok' if ok else 'fail'} url={public}", flush=True)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"miniapp-tunnel: menu_button error {type(exc).__name__}", flush=True)


def main() -> int:
    bin_path = CLOUDFLARED
    if not os.path.isfile(bin_path):
        for candidate in ("/usr/bin/cloudflared", "/usr/local/bin/cloudflared"):
            if os.path.isfile(candidate):
                bin_path = candidate
                break
    cmd = [bin_path, "tunnel", "--no-autoupdate", "--url", TUNNEL_TARGET]
    print("miniapp-tunnel: start", TUNNEL_TARGET, flush=True)
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
    )
    current = ""
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                print(line, flush=True)
            found = URL_RE.findall(line)
            if found:
                url = found[-1]
                if url != current:
                    current = url
                    publish(url)
        return proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        return 0


if __name__ == "__main__":
    sys.exit(main())
