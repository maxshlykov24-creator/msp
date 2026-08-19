#!/usr/bin/env python3
"""Добавить одного VLESS-клиента в первый inbound 3x-ui на VPS (SQLite + SSH).

Пример:
  DEPLOY_SSH_PASSWORD='…' CLIENT_LABEL=LizaVL python3 emmanuel-bot/scripts/xui_add_vless_client_ssh.py
"""

from __future__ import annotations

import json
import os
import sys
from urllib.parse import quote

try:
    import paramiko  # type: ignore
except ImportError:
    sys.exit("pip install paramiko")

VPS = os.environ.get("VPS_HOST", "85.192.38.49")
USER = "root"
PUBLIC_HOST = os.environ.get("PUBLIC_HOST", VPS).strip() or VPS
CLIENT_LABEL = (os.environ.get("CLIENT_LABEL") or "").strip()
DEFAULT_PBK = "K-LcIG9WZkyV7K_5RtQoY1aSyIRagCoavJ3U9jyvrFc"

REMOTE_PY = r'''
import json, os, re, sqlite3, subprocess, sys, uuid

DB = "/etc/x-ui/x-ui.db"
LABEL = os.environ["CLIENT_LABEL"]
EMAIL = LABEL if "@" in LABEL else f"{LABEL}@device"

conn = sqlite3.connect(DB)
row = conn.execute(
    "SELECT id, port, stream_settings, settings FROM inbounds ORDER BY id ASC LIMIT 1"
).fetchone()
if not row:
    print("NO_INBOUNDS", file=sys.stderr)
    sys.exit(2)

in_id, port, stream_raw, settings_raw = row
st = json.loads(settings_raw)
clients = list(st.get("clients") or [])
if not clients:
    print("NO_EXISTING_CLIENT", file=sys.stderr)
    sys.exit(3)

for c in clients:
    if (c.get("email") or "") == EMAIL:
        print(json.dumps({
            "inboundId": in_id, "port": port, "existing": True,
            "id": c.get("id"), "email": EMAIL,
        }, ensure_ascii=False), flush=True)
        sys.exit(0)

template = dict(clients[0])
new_id = str(uuid.uuid4())
new_client = dict(template)
new_client["id"] = new_id
new_client["email"] = EMAIL
if "password" in new_client:
    new_client["password"] = new_id
clients.append(new_client)
st["clients"] = clients
conn.execute("UPDATE inbounds SET settings = ? WHERE id = ?", (json.dumps(st, ensure_ascii=False), in_id))
conn.commit()
conn.close()

stream = json.loads(stream_raw)
rs = stream.get("realitySettings") or {}
sni = (rs.get("serverNames") or ["yahoo.com"])[0]
sid_list = rs.get("shortIds") or rs.get("shortId") or []
sid = sid_list[0] if isinstance(sid_list, list) and sid_list else (sid_list or "a59423a016bf45ed")

pbk = ""
priv = rs.get("privateKey") or ""
if priv:
    try:
        out = subprocess.check_output(["/usr/local/x-ui/bin/xray", "x25519", "-i", priv], text=True, timeout=15)
        m = re.search(r"Public key:\s*(\S+)", out)
        if m:
            pbk = m.group(1).strip()
    except Exception:
        pass

subprocess.Popen(
    ["systemctl", "restart", "x-ui"],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    start_new_session=True,
)

print(json.dumps({
    "inboundId": in_id, "port": port, "pbk": pbk, "sni": sni, "sid": sid,
    "added": [{"id": new_id, "email": EMAIL, "label": LABEL}],
}, ensure_ascii=False), flush=True)
'''


def connect() -> paramiko.SSHClient:
    password = os.environ.get("DEPLOY_SSH_PASSWORD", "").strip()
    key_path = os.environ.get("DEPLOY_SSH_KEY", "").strip()
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    if key_path:
        c.connect(VPS, username=USER, key_filename=key_path, timeout=20)
    elif password:
        c.connect(VPS, username=USER, password=password, timeout=20)
    else:
        sys.exit("Задайте DEPLOY_SSH_PASSWORD или DEPLOY_SSH_KEY")
    return c


def run(c: paramiko.SSHClient, bash: str) -> tuple[int, str]:
    _, o, e = c.exec_command(bash, get_pty=True, timeout=30)
    o.channel.settimeout(120.0)
    e.channel.settimeout(120.0)
    out = o.read().decode(errors="replace")
    err = e.read().decode(errors="replace")
    return o.channel.recv_exit_status(), out + err


def vless_uri(uid: str, port: int, pbk: str, sid: str, sni: str, label: str) -> str:
    frag = quote(label)
    return (
        f"vless://{uid}@{PUBLIC_HOST}:{port}"
        "?encryption=none&security=reality"
        f"&sni={quote(sni)}&fp=chrome"
        f"&pbk={quote(pbk, safe='-_')}&sid={sid}&type=tcp&flow=xtls-rprx-vision"
        f"#{frag}"
    )


def main() -> int:
    if not CLIENT_LABEL:
        print("Задайте CLIENT_LABEL, например LizaVL", file=sys.stderr)
        return 1

    c = connect()
    try:
        remote_b64 = __import__("base64").b64encode(REMOTE_PY.encode()).decode()
        cmd = (
            f"export CLIENT_LABEL={json.dumps(CLIENT_LABEL)}; "
            f"python3 - <<'PY'\nimport base64; exec(base64.b64decode('{remote_b64}').decode())\nPY"
        )
        code, out = run(c, cmd)
        meta = None
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    meta = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue
        if meta is None:
            sys.stderr.write(out)
            return code or 9

        port = int(meta["port"])
        pbk = (meta.get("pbk") or "").strip() or DEFAULT_PBK
        sid = meta.get("sid") or "a59423a016bf45ed"
        sni = meta.get("sni") or "yahoo.com"

        if meta.get("existing"):
            uid = meta["id"]
            print(f"Клиент уже есть: {meta.get('email')}")
        else:
            added = meta.get("added") or []
            if not added:
                sys.stderr.write(out)
                return 9
            uid = added[0]["id"]
            print(f"Добавлен клиент: {added[0].get('email')}")

        print(vless_uri(uid, port, pbk, sid, sni, CLIENT_LABEL))
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
