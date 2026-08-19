#!/usr/bin/env python3
"""WireGuard EU exit + RU client; xray routes via wg0 using post-patch."""
import json
import os
import textwrap

EU = "138.124.54.49"
WG_PORT = 51820
RU_IP = "5.42.104.112"
INBOUND_TAG = "inbound-8443"

WG_SETUP = textwrap.dedent(
    f"""
    set -e
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq wireguard >/dev/null
    mkdir -p /etc/wireguard
    if [ ! -f /etc/wireguard/eu.private ]; then
      wg genkey | tee /etc/wireguard/eu.private | wg pubkey > /etc/wireguard/eu.public
    fi
    if [ ! -f /etc/wireguard/ru.public ]; then
      echo "need ru pubkey" >&2; exit 1
    fi
    cat > /etc/wireguard/wg0.conf <<EOF
[Interface]
Address = 10.66.66.1/24
ListenPort = {WG_PORT}
PrivateKey = $(cat /etc/wireguard/eu.private)
PostUp = sysctl -w net.ipv4.ip_forward=1; iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE; iptables -t nat -A POSTROUTING -o ens3 -j MASQUERADE 2>/dev/null || true
PostDown = iptables -t nat -D POSTROUTING -o eth0 -j MASQUERADE; iptables -t nat -D POSTROUTING -o ens3 -j MASQUERADE 2>/dev/null || true

[Peer]
PublicKey = $(cat /etc/wireguard/ru.public)
AllowedIPs = 10.66.66.2/32
EOF
    chmod 600 /etc/wireguard/wg0.conf
    systemctl enable wg-quick@wg0
    systemctl restart wg-quick@wg0
    """
)


def patch_xray_wg_exit(cfg_path: str) -> None:
    with open(cfg_path, encoding="utf-8") as f:
        cfg = json.load(f)
    cfg.setdefault("log", {})
    cfg["log"]["access"] = "/var/log/xray/access.log"
    cfg["log"]["error"] = "/var/log/xray/error.log"
    cfg["log"]["loglevel"] = "warning"
    os.makedirs("/var/log/xray", exist_ok=True)
    open("/var/log/xray/access.log", "a").close()
    open("/var/log/xray/error.log", "a").close()

    outbounds = [o for o in cfg.get("outbounds", []) if o.get("tag") not in ("eu-wg-exit", "eu-exit")]
    wg_out = {
        "tag": "eu-wg-exit",
        "protocol": "freedom",
        "settings": {"domainStrategy": "UseIPv4"},
        "streamSettings": {"sockopt": {"interface": "wg0", "tcpFastOpen": False}},
    }
    cfg["outbounds"] = [wg_out] + outbounds
    routing = cfg.setdefault("routing", {"domainStrategy": "AsIs", "rules": []})
    rules = [
        r
        for r in routing.get("rules", [])
        if not (r.get("outboundTag") in ("eu-wg-exit", "eu-exit") and r.get("inboundTag"))
    ]
    rules.insert(0, {"type": "field", "inboundTag": [INBOUND_TAG], "outboundTag": "eu-wg-exit"})
    routing["rules"] = rules
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


if __name__ == "__main__":
    patch_xray_wg_exit("/usr/local/x-ui/bin/config.json")
    print("patched_wg")
