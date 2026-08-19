#!/usr/bin/env python3
"""Patch x-ui config.json: route RU user inbound via EU relay."""
import json
import sys

CFG = "/usr/local/x-ui/bin/config.json"
EU = "138.124.54.49"
EU_PORT = 40000
RELAY_UUID = "c3a9f1b2-8d4e-4f1a-9b0c-2d7e6f5a4b3c"
INBOUND_TAG = "inbound-8443"


def main() -> int:
    with open(CFG, encoding="utf-8") as f:
        cfg = json.load(f)

    outbounds = [o for o in cfg.get("outbounds", []) if o.get("tag") != "eu-exit"]
    eu_out = {
        "tag": "eu-exit",
        "protocol": "vless",
        "settings": {
            "vnext": [
                {
                    "address": EU,
                    "port": EU_PORT,
                    "users": [{"id": RELAY_UUID, "encryption": "none", "flow": ""}],
                }
            ]
        },
        "streamSettings": {
            "network": "tcp",
            "security": "none",
            "tcpSettings": {"header": {"type": "none"}},
        },
    }
    cfg["outbounds"] = [eu_out] + outbounds

    routing = cfg.setdefault("routing", {"domainStrategy": "AsIs", "rules": []})
    rules = [
        r
        for r in routing.get("rules", [])
        if not (r.get("outboundTag") == "eu-exit" and r.get("inboundTag"))
    ]
    rules.insert(0, {"type": "field", "inboundTag": [INBOUND_TAG], "outboundTag": "eu-exit"})
    routing["rules"] = rules

    with open(CFG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    print("patched")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
