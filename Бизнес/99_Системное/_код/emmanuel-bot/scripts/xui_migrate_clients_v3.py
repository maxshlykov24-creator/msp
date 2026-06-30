#!/usr/bin/env python3
"""Migrate embedded inbound clients to x-ui 3.x clients + client_inbounds tables."""
import json
import sqlite3
import subprocess
import time

PORT = 8443

conn = sqlite3.connect("/etc/x-ui/x-ui.db")
cols = [r[1] for r in conn.execute("pragma table_info(clients)").fetchall()]
inb_id, settings_raw = conn.execute(
    "select id, settings from inbounds where port=?", (PORT,)
).fetchone()
clients = json.loads(settings_raw).get("clients") or []
now = int(time.time() * 1000)
linked = 0
for c in clients:
    uid = c.get("id")
    email = c.get("email") or uid
    row = conn.execute("select id from clients where uuid=?", (uid,)).fetchone()
    if row:
        cid = row[0]
    else:
        data = {k: None for k in cols}
        data.update(
            {
                "email": email,
                "uuid": uid,
                "password": "",
                "auth": "",
                "flow": c.get("flow") or "",
                "security": c.get("security") or "",
                "reverse": "",
                "limit_ip": c.get("limitIp", 0),
                "total_gb": c.get("totalGB", 0),
                "expiry_time": c.get("expiryTime", 0),
                "enable": 1 if c.get("enable", True) else 0,
                "tg_id": c.get("tgId", 0),
                "comment": c.get("comment") or "",
                "reset": c.get("reset", 0),
                "sub_id": c.get("subId") or "",
                "created_at": c.get("created_at") or now,
                "updated_at": c.get("updated_at") or now,
            }
        )
        keys = [k for k in data if k != "id" and k in cols]
        conn.execute(
            f"insert into clients ({','.join(keys)}) values ({','.join(['?'] * len(keys))})",
            [data[k] for k in keys],
        )
        cid = conn.execute("select id from clients where uuid=?", (uid,)).fetchone()[0]
    if not conn.execute(
        "select 1 from client_inbounds where client_id=? and inbound_id=?", (cid, inb_id)
    ).fetchone():
        conn.execute(
            "insert into client_inbounds (client_id,inbound_id,flow_override,created_at) values (?,?,?,?)",
            (cid, inb_id, c.get("flow") or "", now),
        )
        linked += 1
conn.commit()
subprocess.run(["x-ui", "restart"], check=False)
print(json.dumps({"linked": linked, "total": len(clients)}))
