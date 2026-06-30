import json
import sqlite3
import subprocess

conn = sqlite3.connect("/etc/x-ui/x-ui.db")
rows = conn.execute("select id, port, settings from inbounds").fetchall()
updated = []
for in_id, port, settings_raw in rows:
    st = json.loads(settings_raw)
    clients = st.get("clients") or []
    changed = 0
    for c in clients:
        if c.get("limitIp", 0) != 1:
            c["limitIp"] = 1
            changed += 1
    if changed:
        conn.execute(
            "update inbounds set settings=? where id=?",
            (json.dumps(st, ensure_ascii=False), in_id),
        )
        updated.append({"inboundId": in_id, "port": port, "clients": len(clients), "changed": changed})
conn.commit()
subprocess.Popen(
    ["systemctl", "restart", "x-ui"],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    start_new_session=True,
)
print(json.dumps(updated, ensure_ascii=False))
