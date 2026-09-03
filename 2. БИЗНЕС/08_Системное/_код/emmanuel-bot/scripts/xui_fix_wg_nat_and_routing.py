#!/usr/bin/env python3
"""Fix EU WireGuard NAT + RU policy routing for xray via wg0."""
from __future__ import annotations

import textwrap

import paramiko

EU = "138.124.54.49"
RU = "5.42.104.112"
EU_PW = "ddyS1P7eB5CK"
RU_PW = "vV?FzUz#4GM5fQ"
WG_PORT = 51820
EU_IFACE = "enp0s3"
MARK = 51820


def ssh(host: str, password: str) -> paramiko.SSHClient:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, username="root", password=password, timeout=30, banner_timeout=30)
    return c


def run(c: paramiko.SSHClient, cmd: str, timeout: float = 120) -> str:
    _, o, e = c.exec_command(cmd, timeout=timeout)
    out = o.read().decode()
    err = e.read().decode()
    if err.strip():
        out += "\nERR: " + err
    return out


EU_WG_POST = textwrap.dedent(
    f"""
    sysctl -w net.ipv4.ip_forward=1
    iptables -t nat -D POSTROUTING -o eth0 -j MASQUERADE 2>/dev/null || true
    iptables -t nat -D POSTROUTING -o ens3 -j MASQUERADE 2>/dev/null || true
    iptables -t nat -C POSTROUTING -o {EU_IFACE} -j MASQUERADE 2>/dev/null || iptables -t nat -A POSTROUTING -o {EU_IFACE} -j MASQUERADE
    iptables -C FORWARD -i wg0 -o {EU_IFACE} -j ACCEPT 2>/dev/null || iptables -A FORWARD -i wg0 -o {EU_IFACE} -j ACCEPT
    iptables -C FORWARD -i {EU_IFACE} -o wg0 -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || iptables -A FORWARD -i {EU_IFACE} -o wg0 -m state --state RELATED,ESTABLISHED -j ACCEPT
    """
).strip()

RU_ROUTING_SCRIPT = textwrap.dedent(
    f"""#!/bin/bash
set -e
MARK={MARK}
TABLE=$MARK
CGROUP=/sys/fs/cgroup/net_cls/xray
CLASSID=0x00010011

modprobe cls_cgroup 2>/dev/null || true
mkdir -p "$CGROUP"
echo "$CLASSID" > "$CGROUP/net_cls.classid"

iptables -t mangle -C OUTPUT -m cgroup --cgroup $CLASSID -j MARK --set-mark $MARK 2>/dev/null || \\
  iptables -t mangle -A OUTPUT -m cgroup --cgroup $CLASSID -j MARK --set-mark $MARK

ip rule del fwmark $MARK table $TABLE 2>/dev/null || true
ip rule add fwmark $MARK table $TABLE
ip route flush table $TABLE
ip route add default dev wg0 table $TABLE

for pid in $(pgrep -x xray-linux-amd64 || true); do
  echo "$pid" > "$CGROUP/tasks" 2>/dev/null || true
done
"""
).strip()

RU_CRON = f"* * * * * root /usr/local/bin/xray-wg-route.sh >/dev/null 2>&1\n"


def main() -> None:
    eu = ssh(EU, EU_PW)
    ru_pub = run(eu, "cat /etc/wireguard/ru.public 2>/dev/null || echo MISSING").strip()
    eu_priv = run(eu, "cat /etc/wireguard/eu.private").strip()

    run(
        eu,
        f"""
cat > /etc/wireguard/wg0.conf <<'EOF'
[Interface]
Address = 10.66.66.1/24
ListenPort = {WG_PORT}
PrivateKey = {eu_priv}
PostUp = {EU_WG_POST}
PostDown = iptables -t nat -D POSTROUTING -o {EU_IFACE} -j MASQUERADE; iptables -D FORWARD -i wg0 -o {EU_IFACE} -j ACCEPT; iptables -D FORWARD -i {EU_IFACE} -o wg0 -m state --state RELATED,ESTABLISHED -j ACCEPT

[Peer]
PublicKey = {ru_pub}
AllowedIPs = 10.66.66.2/32
EOF
chmod 600 /etc/wireguard/wg0.conf
systemctl restart wg-quick@wg0
{EU_WG_POST}
curl -sS --max-time 8 https://api.ipify.org
""",
        60,
    )
    print("EU OK:", run(eu, "iptables -t nat -L POSTROUTING -n -v | grep MASQ; wg show | tail -5"))

    ru = ssh(RU, RU_PW)
    sftp = ru.open_sftp()
    with sftp.open("/usr/local/bin/xray-wg-route.sh", "w") as f:
        f.write(RU_ROUTING_SCRIPT + "\n")
    sftp.close()
    run(ru, "chmod +x /usr/local/bin/xray-wg-route.sh", 10)
    run(ru, f"printf '%s' '{RU_CRON}' > /etc/cron.d/xray-wg-route", 10)
    run(ru, "/usr/local/bin/xray-wg-route.sh", 20)

    verify = run(
        ru,
        f"""
curl -sS --max-time 12 --interface wg0 https://api.ipify.org || echo wg-curl-fail
/usr/local/bin/xray-wg-route.sh
python3 -c "import json;c=json.load(open('/usr/local/x-ui/bin/config.json')); print('clients', len(( [x for x in c['inbounds'] if x.get('port')==8443][0].get('settings') or {{}}).get('clients') or [])); print('outbounds', [o.get('tag') for o in c.get('outbounds',[])])"
curl -sS --max-time 12 --interface eth0 https://api.ipify.org || true
""",
        40,
    )
    print("RU VERIFY:", verify)

    # Remove broken cron that kills xray
    run(ru, "rm -f /etc/cron.d/xui-wg-exit", 10)

    eu.close()
    ru.close()


if __name__ == "__main__":
    main()
