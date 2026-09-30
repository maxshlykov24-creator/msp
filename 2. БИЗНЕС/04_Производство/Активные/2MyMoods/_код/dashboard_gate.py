#!/usr/bin/env python3
"""Страница входа дашборда 2MY. Пароль читается из окружения, в ответ не попадает."""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

ROOT = Path(os.environ.get("DASHBOARD_ROOT", "/var/www/2my-dashboard"))
HOST = os.environ.get("DASHBOARD_BIND", "127.0.0.1")
PORT = int(os.environ.get("DASHBOARD_PORT", "19121"))
COOKIE = "2my_session"
MAX_AGE = 14 * 24 * 3600


def env(name: str) -> str:
    return os.environ.get(name, "").strip()


def sign(user: str, exp: int) -> str:
    secret = env("DASHBOARD_COOKIE_SECRET").encode()
    body = f"{user}|{exp}".encode()
    mac = hmac.new(secret, body, hashlib.sha256).hexdigest()
    return f"{user}|{exp}|{mac}"


def valid_cookie(raw: str | None) -> bool:
    if not raw or not env("DASHBOARD_COOKIE_SECRET"):
        return False
    user, _, mac = raw.partition("|")
    rest, _, got = raw.rpartition("|")
    exp_s = rest.split("|", 1)[-1] if "|" in rest else ""
    try:
        exp = int(exp_s)
    except ValueError:
        return False
    if exp < int(time.time()) or user != env("DASHBOARD_BASIC_USER"):
        return False
    expected = sign(user, exp).rpartition("|")[-1]
    if len(expected) != len(got):
        return False
    return hmac.compare_digest(expected, got)


def cookie_header(header: str | None) -> str | None:
    if not header:
        return None
    for part in header.split(";"):
        name, _, value = part.strip().partition("=")
        if name == COOKIE:
            return value
    return None


def check_password(login: str, password: str) -> bool:
    user = env("DASHBOARD_BASIC_USER")
    secret = env("DASHBOARD_BASIC_PASSWORD")
    if not user or not secret or len(login) != len(user) or len(password) != len(secret):
        return False
    return hmac.compare_digest(login, user) and hmac.compare_digest(password, secret)


class Handler(BaseHTTPRequestHandler):
    server_version = "2my-dashboard"

    def log_message(self, fmt: str, *args) -> None:
        return

    def _send(self, code: int, body: bytes, content_type: str, extra: list[tuple[str, str]] | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in extra or []:
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location: str, cookie: str | None = None) -> None:
        extra = [("Location", location)]
        if cookie is not None:
            extra.append((
                "Set-Cookie",
                f"{COOKIE}={cookie}; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age={MAX_AGE}",
            ))
        self._send(302, b"", "text/plain; charset=utf-8", extra)

    def _authed(self) -> bool:
        return valid_cookie(cookie_header(self.headers.get("Cookie")))

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/login":
            if self._authed():
                self._redirect("/")
                return
            self._send(200, (ROOT / "login.html").read_bytes(), "text/html; charset=utf-8")
            return
        if path == "/assets/logo-2my.png":
            logo = ROOT / "assets" / "logo-2my.png"
            self._send(200, logo.read_bytes(), "image/png")
            return
        if path == "/logout":
            self._send(302, b"", "text/plain; charset=utf-8", [
                ("Location", "/login"),
                ("Set-Cookie", f"{COOKIE}=; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=0"),
            ])
            return
        if not self._authed():
            self._redirect("/login")
            return
        if path in ("/", "/index.html"):
            self._send(200, (ROOT / "index.html").read_bytes(), "text/html; charset=utf-8")
            return
        if path == "/snapshot.local.json":
            self._send(200, (ROOT / "snapshot.local.json").read_bytes(), "application/json; charset=utf-8")
            return
        self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self) -> None:
        if self.path.split("?", 1)[0] != "/login":
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length > 4000:
            self._redirect("/login?e=1")
            return
        form = parse_qs(self.rfile.read(length).decode("utf-8", "replace"))
        login = (form.get("login") or [""])[0]
        password = (form.get("password") or [""])[0]
        if not check_password(login, password):
            self._redirect("/login?e=1")
            return
        exp = int(time.time()) + MAX_AGE
        self._redirect("/", sign(login, exp))


def main() -> None:
    if not env("DASHBOARD_BASIC_PASSWORD") or not env("DASHBOARD_COOKIE_SECRET"):
        raise SystemExit("Нет пароля или секрета сессии")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
