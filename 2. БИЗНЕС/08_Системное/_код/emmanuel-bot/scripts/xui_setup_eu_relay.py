#!/usr/bin/env python3
"""Ensure EU internal relay inbound on port 40000 for RU chain."""
import json
import sqlite3
import subprocess

DB = "/etc/x-ui/x-ui.db"
RELAY_UUID = "c3a9f1b2-8d4e-4f1a-9b0c-2d7e6f5a4b3c"
RELAY_PORT = 40000
RU_IP = "5.42.104.112"


def main() -> None:
    conn = sqlite3.connect(DB)
    cols = [r[1] for r in conn.execute("pragma table_info(inbounds)").fetchall()]
    row = conn.execute("select id from inbounds where port=?", (RELAY_PORT,)).fetchone()
    settings = {
        "clients": [
            {
                "id": RELAY_UUID,
                "flow": "",
                "email": "ru-relay@internal",
                "limitIp": 1,
                "totalGB": 0,
                "expiryTime": 0,
                "enable": True,
                "tgId": 0,
                "subId": "",
                "comment": "RU chain relay",
                "reset": 0,
            }
        ],
        "decryption": "none",
        "fallbacks": [],
    }
    stream = {
        "network": "tcp",
        "security": "none",
        "externalProxy": None,
        "tcpSettings": {"acceptProxyProtocol": False, "header": {"type": "none"}},
    }
    sniff = {"enabled": False, "destOverride": ["http", "tls"], "metadataOnly": False}
    data = {
        "user_id": 1,
        "up": 0,
        "down": 0,
        "total": 0,
        "remark": "RU_relay_internal",
        "enable": 1,
        "expiry_time": 0,
        "traffic_reset": "never",
        "last_traffic_reset_time": 0,
        "listen": "",
        "port": RELAY_PORT,
        "protocol": "vless",
        "settings": json.dumps(settings, ensure_ascii=False),
        "stream_settings": json.dumps(stream, ensure_ascii=False),
        "tag": f"inbound-{RELAY_PORT}",
        "sniffing": json.dumps(sniff, ensure_ascii=False),
    }
    allowed = {k: v for k, v in data.items() if k in cols}
    keys = list(allowed.keys())
    if row:
        conn.execute(
            f"update inbounds set {', '.join(f'{k}=?' for k in keys if k != 'port')} where port=?",
            [allowed[k] for k in keys if k != "port"] + [RELAY_PORT],
        )
    else:
        conn.execute(
            f"insert into inbounds ({','.join(keys)}) values ({','.join(['?']*len(keys))})",
            [allowed[k] for k in keys],
        )
    conn.commit()
    conn.close()

    # firewall: allow relay only from RU IP
    def ipt(*args: str, check: bool = False) -> subprocess.CompletedProcess:
        return subprocess.run(["iptables", *args], capture_output=True, check=check)

    accept = ["-A", "INPUT", "-p", "tcp", "-s", RU_IP, "--dport", str(RELAY_PORT), "-j", "ACCEPT"]
    drop = ["-A", "INPUT", "-p", "tcp", "--dport", str(RELAY_PORT), "-j", "DROP"]
    for rule in (accept, drop):
        chk = ["-C", *rule[1:]]
        if ipt(*chk).returncode != 0:
            ipt(*rule, check=True)
    print("relay_ok")


if __name__ == "__main__":
    main()
