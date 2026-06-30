#!/usr/bin/env python3
import sqlite3
import subprocess
import time

RELAY = "c3a9f1b2-8d4e-4f1a-9b0c-2d7e6f5a4b3c"

conn = sqlite3.connect("/etc/x-ui/x-ui.db")
cols = [r[1] for r in conn.execute("pragma table_info(clients)").fetchall()]
inb = conn.execute("select id from inbounds where port=40000").fetchone()
if not inb:
    raise SystemExit("no relay inbound")
inb_id = inb[0]
row = conn.execute("select id from clients where uuid=?", (RELAY,)).fetchone()
now = int(time.time() * 1000)
if row:
    cid = row[0]
else:
    data = {k: None for k in cols}
    data.update(
        {
            "email": "ru-relay@internal",
            "uuid": RELAY,
            "password": "",
            "auth": "",
            "flow": "",
            "security": "",
            "reverse": "",
            "limit_ip": 1,
            "total_gb": 0,
            "expiry_time": 0,
            "enable": 1,
            "tg_id": 0,
            "comment": "RU relay",
            "reset": 0,
            "sub_id": "",
            "created_at": now,
            "updated_at": now,
        }
    )
    keys = [k for k in data if k != "id" and k in cols]
    conn.execute(
        f"insert into clients ({','.join(keys)}) values ({','.join(['?'] * len(keys))})",
        [data[k] for k in keys],
    )
    cid = conn.execute("select id from clients where uuid=?", (RELAY,)).fetchone()[0]
if not conn.execute(
    "select 1 from client_inbounds where client_id=? and inbound_id=?", (cid, inb_id)
).fetchone():
    conn.execute(
        "insert into client_inbounds (client_id,inbound_id,flow_override,created_at) values (?,?,?,?)",
        (cid, inb_id, "", now),
    )
conn.commit()
subprocess.run(["x-ui", "restart"], check=False)
print("ok", cid, inb_id)
