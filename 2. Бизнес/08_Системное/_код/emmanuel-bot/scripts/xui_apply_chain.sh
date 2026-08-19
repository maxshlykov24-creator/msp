#!/bin/bash
set -e
/usr/bin/python3 /usr/local/bin/xui-patch-ru-chain.py
python3 - <<'PY'
import json
import os

p = "/usr/local/x-ui/bin/config.json"
with open(p, encoding="utf-8") as f:
    c = json.load(f)
c.setdefault("log", {})
c["log"]["access"] = "/var/log/xray/access.log"
c["log"]["error"] = "/var/log/xray/error.log"
c["log"]["loglevel"] = "warning"
with open(p, "w", encoding="utf-8") as f:
    json.dump(c, f, indent=2)
os.makedirs("/var/log/xray", exist_ok=True)
open("/var/log/xray/access.log", "a").close()
open("/var/log/xray/error.log", "a").close()
PY
killall -9 xray-linux-amd64 2>/dev/null || true
sleep 2
