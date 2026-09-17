#!/usr/bin/env python3
"""Прямой клиент amoBridge MCP: OAuth + tools/list + get_chat.

Cursor не отдаёт инструменты: tools/list у сервера дольше 60 с.
Токен пишем в _data/amobridge_token.json (gitignore).
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "_data"
CTX = ssl.create_default_context()
REDIRECT = "http://localhost:8787/callback"
TOKEN_PATH = DATA / "amobridge_token.json"


def load_env() -> None:
    env = ROOT / ".env"
    if not env.exists():
        sys.exit("нет .env")
    for line in env.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def creds() -> tuple[str, str, str]:
    load_env()
    url = os.environ.get("AMOBRIDGE_URL", "https://mcp.mkrck.ru/mcp").rstrip("/")
    cid = os.environ.get("AMOBRIDGE_CLIENT_ID", "").strip()
    secret = os.environ.get("AMOBRIDGE_CLIENT_SECRET", "").strip()
    if not cid or not secret:
        sys.exit("нет AMOBRIDGE_CLIENT_ID / SECRET в .env")
    return url, cid, secret


def b64url(raw: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def save_token(obj: dict) -> None:
    DATA.mkdir(exist_ok=True)
    TOKEN_PATH.write_text(json.dumps(obj, indent=2), encoding="utf-8")
    os.chmod(TOKEN_PATH, 0o600)


def load_token() -> dict:
    if not TOKEN_PATH.exists():
        sys.exit("нет токена — сначала: python3 amobridge_mcp.py oauth")
    return json.loads(TOKEN_PATH.read_text(encoding="utf-8"))


def refresh_if_needed() -> str:
    tok = load_token()
    exp = float(tok.get("expires_at") or 0)
    if exp and exp - time.time() > 60:
        return tok["access_token"]
    refresh = tok.get("refresh_token")
    if not refresh:
        sys.exit("токен истёк, refresh нет — python3 amobridge_mcp.py oauth")
    _, cid, secret = creds()
    body = urllib.parse.urlencode(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh,
            "client_id": cid,
            "client_secret": secret,
        }
    ).encode()
    req = urllib.request.Request(
        "https://mcp.mkrck.ru/oauth/token",
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, context=CTX, timeout=30) as resp:
        data = json.loads(resp.read())
    data["expires_at"] = time.time() + int(data.get("expires_in") or 3600)
    if "refresh_token" not in data:
        data["refresh_token"] = refresh
    save_token(data)
    return data["access_token"]


def cmd_oauth() -> None:
    _, cid, secret = creds()
    verifier = b64url(secrets.token_bytes(32))
    challenge = b64url(hashlib.sha256(verifier.encode()).digest())
    state = secrets.token_urlsafe(16)
    holder: dict = {}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *_a, **_k):
            return

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            if u.path != "/callback":
                self.send_response(404)
                self.end_headers()
                return
            q = urllib.parse.parse_qs(u.query)
            holder["code"] = (q.get("code") or [None])[0]
            holder["state"] = (q.get("state") or [None])[0]
            holder["error"] = (q.get("error") or [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("amoBridge: можно закрыть вкладку.".encode())

    httpd = HTTPServer(("127.0.0.1", 8787), H)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    params = {
        "response_type": "code",
        "client_id": cid,
        "redirect_uri": REDIRECT,
        "scope": "amocrm:read",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "resource": "https://mcp.mkrck.ru/mcp",
    }
    url = "https://mcp.mkrck.ru/oauth/authorize?" + urllib.parse.urlencode(params)
    print("Открой в браузере и подтверди доступ:")
    print(url)
    webbrowser.open(url)
    for _ in range(180):
        if holder.get("code") or holder.get("error"):
            break
        time.sleep(1)
    httpd.shutdown()
    if holder.get("error"):
        sys.exit(f"oauth error: {holder['error']}")
    if not holder.get("code"):
        sys.exit("oauth timeout: код не пришёл за 3 минуты")
    if holder.get("state") != state:
        sys.exit("oauth state mismatch")
    body = urllib.parse.urlencode(
        {
            "grant_type": "authorization_code",
            "code": holder["code"],
            "redirect_uri": REDIRECT,
            "client_id": cid,
            "client_secret": secret,
            "code_verifier": verifier,
            "resource": "https://mcp.mkrck.ru/mcp",
        }
    ).encode()
    req = urllib.request.Request(
        "https://mcp.mkrck.ru/oauth/token",
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=30) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        sys.exit(f"token {exc.code}: {exc.read()[:400]}")
    data["expires_at"] = time.time() + int(data.get("expires_in") or 3600)
    save_token(data)
    print("токен сохранён в _data/amobridge_token.json")


def _read_sse(resp) -> dict:
    ctype = (resp.headers.get("Content-Type") or "").lower()
    raw = resp.read()
    if "text/event-stream" in ctype:
        data_lines = []
        for line in raw.decode("utf-8", errors="replace").splitlines():
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        if not data_lines:
            return {}
        return json.loads(data_lines[-1])
    if not raw:
        return {}
    return json.loads(raw)


def mcp_rpc(method: str, params: dict | None, timeout: int = 180, session: str | None = None) -> tuple[dict, str | None]:
    mcp_url, _, _ = creds()
    token = refresh_if_needed()
    payload = {"jsonrpc": "2.0", "id": int(time.time() * 1000) % 10_000_000, "method": method}
    if params is not None:
        payload["params"] = params
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "MCP-Protocol-Version": "2025-03-26",
    }
    if session:
        headers["Mcp-Session-Id"] = session
    req = urllib.request.Request(
        mcp_url,
        data=json.dumps(payload).encode(),
        method="POST",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=timeout) as resp:
            sid = resp.headers.get("Mcp-Session-Id") or session
            return _read_sse(resp), sid
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"mcp {method} {exc.code}: {exc.read()[:500]}") from exc


def session_start() -> str:
    body, sid = mcp_rpc(
        "initialize",
        {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "keris-amobridge", "version": "1"},
        },
        timeout=60,
    )
    if body.get("error"):
        sys.exit(f"initialize: {body['error']}")
    mcp_rpc("notifications/initialized", None, timeout=30, session=sid)
    if not sid:
        sys.exit("нет Mcp-Session-Id")
    return sid


def cmd_tools() -> None:
    sid = session_start()
    print("session", sid[:12], "tools/list …")
    body, _ = mcp_rpc("tools/list", {}, timeout=180, session=sid)
    tools = ((body.get("result") or {}).get("tools")) or []
    print("tools", len(tools))
    for t in tools:
        print("-", t.get("name"), "|", (t.get("description") or "")[:80])
    DATA.mkdir(exist_ok=True)
    (DATA / "amobridge_tools.json").write_text(json.dumps(tools, ensure_ascii=False, indent=2), encoding="utf-8")


def cmd_call(name: str, args: dict, timeout: int = 180, session: str | None = None) -> dict:
    sid = session or session_start()
    body, _ = mcp_rpc(
        "tools/call",
        {"name": name, "arguments": args},
        timeout=timeout,
        session=sid,
    )
    return body


def extract_text(body: dict) -> str:
    result = body.get("result") or body
    content = result.get("content") if isinstance(result, dict) else result
    if isinstance(content, list):
        return "\n".join(c.get("text", "") for c in content if isinstance(c, dict))
    if isinstance(content, str):
        return content
    return json.dumps(result, ensure_ascii=False)


def parse_chat(text: str) -> list[dict]:
    import re

    rows = []
    for line in text.splitlines():
        line = line.strip()
        m = re.match(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}),([^,]+),(.*)$", line)
        if not m:
            continue
        when, who, rest = m.group(1), m.group(2).strip().lower(), m.group(3).strip()
        if rest.startswith('"'):
            if rest.endswith('"') and len(rest) >= 2:
                try:
                    rest = json.loads(rest)
                except json.JSONDecodeError:
                    rest = rest[1:]
            else:
                rest = rest[1:]
        outgoing = who not in ("client", "contact", "customer")
        rows.append(
            {
                "dateTime": when,
                "text": rest,
                "author": who,
                "isEcho": outgoing,
                "_source": "amobridge",
            }
        )
    return rows


def cmd_chat(lead_id: int) -> None:
    body = cmd_call("get_chat", {"lead": lead_id, "limit": 50})
    out = DATA / f"chat_{lead_id}.json"
    DATA.mkdir(exist_ok=True)
    out.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    text = extract_text(body)
    print(f"lead {lead_id} chars={len(text)}")
    print(text[:1500])


def cmd_dump_chats(limit: int, refresh_capped: bool = True) -> None:
    leads = json.loads((DATA / "amo_sales_leads.json").read_text(encoding="utf-8"))
    ids = [int(l["id"]) for l in leads]
    if limit < len(ids):
        ids = ids[:limit]
    path = DATA / "amobridge_chats.json"
    store = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    sid = session_start()
    ok = 0
    empty = 0
    skipped = 0
    for i, lid in enumerate(ids, 1):
        prev = store.get(str(lid))
        nprev = len(prev) if isinstance(prev, list) else 0
        # короткие диалоги уже полные; ровно 20 — старый потолок, переснимаем с limit=50
        if prev is not None and 0 < nprev < 20 and not refresh_capped:
            skipped += 1
            ok += 1
            continue
        if prev is not None and 0 < nprev < 20:
            skipped += 1
            ok += 1
            continue
        if prev is not None and nprev > 20:
            skipped += 1
            ok += 1
            continue
        try:
            body = cmd_call("get_chat", {"lead": lid, "limit": 50}, session=sid)
        except SystemExit as exc:
            print(f"  {lid} fail {exc}")
            sid = session_start()
            continue
        text = extract_text(body)
        rows = parse_chat(text)
        footer = text.splitlines()[-1] if text.splitlines() else ""
        store[str(lid)] = rows
        store[str(lid) + "_meta"] = {"footer": footer[:120], "limit": 50, "n": len(rows)}
        if rows:
            ok += 1
        else:
            empty += 1
        n_in = sum(1 for r in rows if not r["isEcho"])
        print(f"  [{i}/{len(ids)}] {lid} msgs={len(rows)} in={n_in} {footer[:40]}")
        if i % 15 == 0:
            path.write_text(json.dumps(store, ensure_ascii=False), encoding="utf-8")
        time.sleep(0.12)
    # не класть _meta в join: join uses str(lid) list only
    path.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"готово: {ok} с текстом, skip_short={skipped}, empty={empty}, файл {path}")


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("usage: amobridge_mcp.py oauth|tools|chat <lead_id>|dump-chats [limit]")
    cmd = sys.argv[1]
    if cmd == "oauth":
        cmd_oauth()
    elif cmd == "tools":
        cmd_tools()
    elif cmd == "chat":
        cmd_chat(int(sys.argv[2]))
    elif cmd == "dump-chats":
        cmd_dump_chats(int(sys.argv[2]) if len(sys.argv) > 2 else 10000)
    else:
        sys.exit("unknown command")


if __name__ == "__main__":
    main()
