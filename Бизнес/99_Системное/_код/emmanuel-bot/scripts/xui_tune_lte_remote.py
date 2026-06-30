#!/usr/bin/env python3
import json
import sqlite3
import subprocess

DB = "/etc/x-ui/x-ui.db"
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
src = dict(conn.execute("SELECT * FROM inbounds WHERE port=443").fetchone())
priv = json.loads(src["stream_settings"])["realitySettings"]["privateKey"]


def ms_reality():
    return {
        "show": False,
        "xver": 0,
        "dest": "www.microsoft.com:443",
        "serverNames": ["www.microsoft.com", "microsoft.com", "login.live.com"],
        "privateKey": priv,
        "minClientVer": "",
        "maxClientVer": "",
        "maxTimediff": 0,
        "shortIds": ["a59423a016bf45ed"],
        "settings": {},
    }


def tcp_stream():
    return {
        "network": "tcp",
        "security": "reality",
        "externalProxy": None,
        "realitySettings": ms_reality(),
        "tcpSettings": {"acceptProxyProtocol": False, "header": {"type": "none"}},
    }


def xhttp_stream():
    return {
        "network": "xhttp",
        "security": "reality",
        "externalProxy": None,
        "realitySettings": ms_reality(),
        "xhttpSettings": {
            "path": "/",
            "host": "",
            "mode": "auto",
            "headers": {},
            "scMaxEachPostBytes": 1000000,
            "scMaxConcurrentPosts": 100,
            "scMinPostsIntervalMs": 30,
            "xPaddingBytes": "100-1000",
            "noGRPCHeader": False,
        },
    }


conn.execute("UPDATE inbounds SET stream_settings=? WHERE port=443", (json.dumps(tcp_stream(), ensure_ascii=False),))
conn.execute("UPDATE inbounds SET stream_settings=? WHERE port=8443", (json.dumps(xhttp_stream(), ensure_ascii=False),))
if conn.execute("SELECT id FROM inbounds WHERE port=2053").fetchone():
    conn.execute("UPDATE inbounds SET stream_settings=? WHERE port=2053", (json.dumps(tcp_stream(), ensure_ascii=False),))
conn.commit()
subprocess.Popen(
    ["systemctl", "restart", "x-ui"],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    start_new_session=True,
)
print("updated")
