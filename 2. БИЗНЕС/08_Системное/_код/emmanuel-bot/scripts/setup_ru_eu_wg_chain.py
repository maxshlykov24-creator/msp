#!/usr/bin/env python3
"""Setup WireGuard EU<->RU and fix x-ui 3.x clients on RU."""
from __future__ import annotations

import pathlib
import time

import paramiko

EU = "138.124.54.49"
RU = "5.42.104.112"
EU_PW = "ddyS1P7eB5CK"
RU_PW = "vV?FzUz#4GM5fQ"
WG_PORT = 51820
BASE = pathlib.Path(__file__).resolve().parent


def ssh(host: str, password: str) -> paramiko.SSHClient:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, username="root", password=password, timeout=30, banner_timeout=30)
    return c


def upload(c: paramiko.SSHClient, local: pathlib.Path, remote: str) -> None:
    sftp = c.open_sftp()
    with sftp.open(remote, "w") as f:
        f.write(local.read_text())
    sftp.close()


def run(c: paramiko.SSHClient, cmd: str, timeout: float = 120) -> str:
    _, o, e = c.exec_command(cmd, timeout=timeout)
    out = o.read().decode()
    err = e.read().decode()
    if err.strip():
        out += "\nERR: " + err
    return out


def main() -> None:
    # WG keys on RU
    ru = ssh(RU, RU_PW)
    run(
        ru,
        "apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq wireguard iproute2 >/dev/null",
        300,
    )
    run(
        ru,
        """
mkdir -p /etc/wireguard
if [ ! -f /etc/wireguard/ru.private ]; then
  wg genkey | tee /etc/wireguard/ru.private | wg pubkey > /etc/wireguard/ru.public
fi
cat /etc/wireguard/ru.public
""",
    )
    ru_pub = run(ru, "cat /etc/wireguard/ru.public").strip()
    print("RU pubkey", ru_pub)

    eu = ssh(EU, EU_PW)
    run(
        eu,
        "apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq wireguard iproute2 >/dev/null",
        300,
    )
    run(
        eu,
        f"""
mkdir -p /etc/wireguard
if [ ! -f /etc/wireguard/eu.private ]; then
  wg genkey | tee /etc/wireguard/eu.private | wg pubkey > /etc/wireguard/eu.public
fi
echo '{ru_pub}' > /etc/wireguard/ru.public
cat > /etc/wireguard/wg0.conf <<EOF
[Interface]
Address = 10.66.66.1/24
ListenPort = {WG_PORT}
PrivateKey = $(cat /etc/wireguard/eu.private)
PostUp = sysctl -w net.ipv4.ip_forward=1; iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE; iptables -t nat -A POSTROUTING -o ens3 -j MASQUERADE 2>/dev/null || true
PostDown = iptables -t nat -D POSTROUTING -o eth0 -j MASQUERADE; iptables -t nat -D POSTROUTING -o ens3 -j MASQUERADE 2>/dev/null || true

[Peer]
PublicKey = {ru_pub}
AllowedIPs = 10.66.66.2/32
EOF
chmod 600 /etc/wireguard/wg0.conf
systemctl enable wg-quick@wg0
systemctl restart wg-quick@wg0
cat /etc/wireguard/eu.public
""",
        120,
    )
    eu_pub = run(eu, "cat /etc/wireguard/eu.public").strip()
    print("EU pubkey", eu_pub)

    run(
        ru,
        f"""
echo '{eu_pub}' > /etc/wireguard/eu.public
cat > /etc/wireguard/wg0.conf <<EOF
[Interface]
Address = 10.66.66.2/32
PrivateKey = $(cat /etc/wireguard/ru.private)
Table = off

[Peer]
PublicKey = {eu_pub}
Endpoint = {EU}:{WG_PORT}
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25
EOF
chmod 600 /etc/wireguard/wg0.conf
systemctl enable wg-quick@wg0
systemctl restart wg-quick@wg0
wg show
""",
        60,
    )

    upload(ru, BASE / "xui_migrate_clients_v3.py", "/usr/local/bin/xui-migrate-clients-v3.py")
    upload(ru, BASE / "xui_patch_ru_wg_exit.py", "/usr/local/bin/xui-patch-ru-wg-exit.py")
    upload(
        ru,
        BASE / "xui_apply_chain.sh",
        "/usr/local/bin/xui-apply-wg-exit.sh",
    )
    # replace apply script content
    apply_sh = """#!/bin/bash
set -e
sleep 1
/usr/bin/python3 /usr/local/bin/xui-patch-ru-wg-exit.py
killall -9 xray-linux-amd64 2>/dev/null || true
sleep 3
/usr/bin/python3 /usr/local/bin/xui-patch-ru-wg-exit.py
"""
    sftp = ru.open_sftp()
    with sftp.open("/usr/local/bin/xui-apply-wg-exit.sh", "w") as f:
        f.write(apply_sh)
    sftp.close()
    run(ru, "chmod +x /usr/local/bin/xui-apply-wg-exit.sh", 10)

    print(run(ru, "python3 /usr/local/bin/xui-migrate-clients-v3.py", 90))
    time.sleep(5)
    print(run(ru, "/usr/local/bin/xui-apply-wg-exit.sh", 30))
    time.sleep(3)
    run(
        ru,
        'printf "%s\\n" "*/1 * * * * root /usr/local/bin/xui-apply-wg-exit.sh >/dev/null 2>&1" > /etc/cron.d/xui-wg-exit',
        10,
    )

    verify = run(
        ru,
        """
python3 -c "import json;c=json.load(open('/usr/local/x-ui/bin/config.json')); ib=[x for x in c['inbounds'] if x.get('port')==8443][0]; print('clients', (ib.get('settings') or {}).get('clients')); print('wg_exit', any(o.get('tag')=='eu-wg-exit' for o in c.get('outbounds',[])))"
curl -sS --max-time 10 --interface wg0 https://api.ipify.org || echo wg-curl-fail
ping -c 2 -I wg0 10.66.66.1 || true
""",
        30,
    )
    print("VERIFY RU:", verify)
    ru.close()
    eu.close()


if __name__ == "__main__":
    main()
