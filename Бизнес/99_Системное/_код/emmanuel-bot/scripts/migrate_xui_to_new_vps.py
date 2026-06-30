#!/usr/bin/env python3
"""Миграция 3x-ui VLESS+REALITY на новый VPS с сохранением UUID клиентов."""

from __future__ import annotations

import json
import os
import re
import secrets
import sys
import time
from urllib.parse import quote

try:
    import paramiko  # type: ignore
except ImportError:
    sys.exit("pip install paramiko")

OLD_HOST = os.environ.get("OLD_VPS_HOST", "85.192.38.49")
NEW_HOST = os.environ.get("NEW_VPS_HOST", "138.124.54.49")
OLD_PW = os.environ.get("OLD_VPS_PASSWORD", "")
NEW_PW = os.environ.get("NEW_VPS_PASSWORD", "")
PANEL_PORT = int(os.environ.get("VPS_PANEL_PORT", "20911"))
PANEL_USER = os.environ.get("VPS_PANEL_USER", "mspadmin")
PANEL_PASS = os.environ.get("VPS_PANEL_PASS", secrets.token_urlsafe(12))


def ssh(host: str, password: str) -> paramiko.SSHClient:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, username="root", password=password, timeout=30, banner_timeout=30)
    return c


def run(c: paramiko.SSHClient, cmd: str, timeout: float = 120) -> tuple[int, str]:
    _, o, e = c.exec_command(cmd, get_pty=True, timeout=timeout)
    o.channel.settimeout(max(timeout, 30))
    e.channel.settimeout(max(timeout, 30))
    out = o.read().decode(errors="replace")
    err = e.read().decode(errors="replace")
    return o.channel.recv_exit_status(), out + err


def export_old() -> dict:
    c = ssh(OLD_HOST, OLD_PW)
    py = r'''
import json, sqlite3, subprocess, re
conn=sqlite3.connect("/etc/x-ui/x-ui.db")
row=conn.execute("select port,remark,settings,stream_settings,sniffing,listen,enable,protocol,tag from inbounds where port=443").fetchone()
st=json.loads(row[2]); stream=json.loads(row[3])
priv=stream["realitySettings"]["privateKey"]
xray="/usr/local/x-ui/bin/xray-linux-amd64"
out=subprocess.check_output([xray,"x25519","-i",priv], text=True, stderr=subprocess.STDOUT)
m=re.search(r"Password \(PublicKey\):\s*(\S+)", out) or re.search(r"Public key:\s*(\S+)", out)
print(json.dumps({
  "port": row[0], "remark": row[1], "protocol": row[7], "tag": row[8],
  "settings": st, "stream_settings": stream,
  "sniffing": json.loads(row[4]) if row[4] else {"enabled": True, "destOverride": ["http","tls"], "metadataOnly": False},
  "listen": row[5] or "", "enable": row[6], "pbk": m.group(1) if m else "",
}, ensure_ascii=False))
'''
    _, out = run(c, f"python3 - <<'PY'\n{py}\nPY", timeout=60)
    c.close()
    m = re.search(r"\{.*\}", out, re.S)
    if not m:
        raise RuntimeError(f"export failed: {out}")
    return json.loads(m.group(0))


def install_new(payload_json: str) -> dict:
    c = ssh(NEW_HOST, NEW_PW)
    _, audit = run(c, "ss -tuln | grep -E ':443|:20911' || true; docker ps --format '{{.Names}}' 2>/dev/null || true")
    if ":443" in audit:
        raise RuntimeError("port 443 already in use on new VPS")

    install_cmd = (
        "set -e\n"
        "export DEBIAN_FRONTEND=noninteractive\n"
        "if ! command -v x-ui >/dev/null 2>&1; then\n"
        "  curl -fsSL https://raw.githubusercontent.com/mhsanaei/3x-ui/master/install.sh -o /tmp/3xi.sh\n"
        f"  printf 'y\\n{PANEL_PORT}\\n4\\n\\n' | bash /tmp/3xi.sh\n"
        "fi\n"
        f"/usr/local/x-ui/x-ui setting -username {PANEL_USER} -password {PANEL_PASS}\n"
        "systemctl enable x-ui\n"
        "systemctl start x-ui\n"
        "sleep 2\n"
        "systemctl is-active x-ui\n"
    )
    code, out = run(c, install_cmd, timeout=600)
    if code != 0 or "active" not in out:
        raise RuntimeError(f"x-ui install failed: {out}")

    migrate_py = r'''
import json, sqlite3, subprocess, sys
payload = json.loads("""__PAYLOAD__""")
DB="/etc/x-ui/x-ui.db"
conn=sqlite3.connect(DB)
conn.row_factory=sqlite3.Row
rows=conn.execute("select id from inbounds").fetchall()
if rows:
    conn.execute("delete from inbounds")
    conn.commit()
sniff=json.dumps(payload["sniffing"], ensure_ascii=False)
data={
  "user_id": 1, "up": 0, "down": 0, "total": 0, "all_time": 0,
  "remark": payload["remark"], "enable": 1, "expiry_time": 0,
  "traffic_reset": "never", "last_traffic_reset_time": 0,
  "listen": payload.get("listen") or "", "port": payload["port"],
  "protocol": payload["protocol"], "settings": json.dumps(payload["settings"], ensure_ascii=False),
  "stream_settings": json.dumps(payload["stream_settings"], ensure_ascii=False),
  "tag": payload["tag"], "sniffing": sniff, "node_id": None,
}
cols=list(data.keys())
conn.execute(f"insert into inbounds ({','.join(cols)}) values ({','.join(['?']*len(cols))})", [data[k] for k in cols])
conn.commit()
subprocess.Popen(["systemctl","restart","x-ui"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
print(json.dumps({"ok": True, "clients": len(payload["settings"].get("clients", []))}))
'''.replace("__PAYLOAD__", payload_json.replace("\\", "\\\\").replace('"', '\\"'))

    sftp = c.open_sftp()
    with sftp.open("/tmp/migrate_xui.py", "w") as f:
        f.write(migrate_py)
    sftp.close()
    time.sleep(1)
    _, out2 = run(c, "python3 /tmp/migrate_xui.py", timeout=60)
    c.close()
    m = re.search(r"\{.*\}", out2, re.S)
    if not m:
        raise RuntimeError(f"migrate failed: {out2}")
    result = json.loads(m.group(0))
    result["panel"] = {"url": f"http://{NEW_HOST}:{PANEL_PORT}", "user": PANEL_USER, "pass": PANEL_PASS}
    return result


def vless(uid: str, label: str, pbk: str, sid: str, sni: str, port: int = 443) -> str:
    name = label.split("@")[0]
    return (
        f"vless://{uid}@{NEW_HOST}:{port}?encryption=none&security=reality"
        f"&sni={quote(sni)}&fp=chrome&pbk={quote(pbk, safe='-_')}&sid={sid}"
        f"&type=tcp&flow=xtls-rprx-vision#{quote(name)}"
    )


def main() -> int:
    if not OLD_PW or not NEW_PW:
        print("Нужны OLD_VPS_PASSWORD и NEW_VPS_PASSWORD", file=sys.stderr)
        return 1
    print(f"Export from {OLD_HOST}...", flush=True)
    payload = export_old()
    pbk = payload.get("pbk") or "K-LcIG9WZkyV7K_5RtQoY1aSyIRagCoavJ3U9jyvrFc"
    rs = payload["stream_settings"]["realitySettings"]
    sid = (rs.get("shortIds") or ["a59423a016bf45ed"])[0]
    sni = (rs.get("serverNames") or ["yahoo.com"])[0]
    print(f"Install + migrate to {NEW_HOST}...", flush=True)
    result = install_new(json.dumps(payload, ensure_ascii=False))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("\n=== VLESS (новый IP, те же UUID) ===")
    for cl in payload["settings"]["clients"]:
        label = cl.get("email") or cl["id"][:8]
        print(vless(cl["id"], label, pbk, sid, sni))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
