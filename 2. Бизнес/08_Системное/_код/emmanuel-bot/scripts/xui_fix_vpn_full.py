#!/usr/bin/env python3
"""Restore EU WG tunnel + persistent xray wg0 exit via wrapper."""
from __future__ import annotations

import pathlib

import paramiko

EU = "138.124.54.49"
RU = "5.42.104.112"
EU_PW = "ddyS1P7eB5CK"
RU_PW = "vV?FzUz#4GM5fQ"
WG_PORT = 51820
EU_IFACE = "enp0s3"
BASE = pathlib.Path(__file__).resolve().parent

EU_WG_UP = f"""#!/bin/bash
set -e
sysctl -w net.ipv4.ip_forward=1
iptables -t nat -C POSTROUTING -o {EU_IFACE} -j MASQUERADE 2>/dev/null || iptables -t nat -A POSTROUTING -o {EU_IFACE} -j MASQUERADE
iptables -C FORWARD -i wg0 -o {EU_IFACE} -j ACCEPT 2>/dev/null || iptables -A FORWARD -i wg0 -o {EU_IFACE} -j ACCEPT
iptables -C FORWARD -i {EU_IFACE} -o wg0 -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || iptables -A FORWARD -i {EU_IFACE} -o wg0 -m state --state RELATED,ESTABLISHED -j ACCEPT
"""

EU_WG_DOWN = f"""#!/bin/bash
iptables -t nat -D POSTROUTING -o {EU_IFACE} -j MASQUERADE 2>/dev/null || true
iptables -D FORWARD -i wg0 -o {EU_IFACE} -j ACCEPT 2>/dev/null || true
iptables -D FORWARD -i {EU_IFACE} -o wg0 -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || true
"""

XRAY_WRAPPER = """#!/bin/bash
REAL=/usr/local/x-ui/bin/xray-linux-amd64.real
/usr/bin/python3 /usr/local/bin/xui-patch-ru-wg-exit.py || true
exec "$REAL" "$@"
"""


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


def upload_text(c: paramiko.SSHClient, remote: str, content: str, mode: int = 0o755) -> None:
    sftp = c.open_sftp()
    with sftp.open(remote, "w") as f:
        f.write(content)
    sftp.close()
    run(c, f"chmod {oct(mode)[2:]} {remote}", 10)


def main() -> None:
    eu = ssh(EU, EU_PW)
    ru_pub = run(eu, "cat /etc/wireguard/ru.public").strip()
    eu_priv = run(eu, "cat /etc/wireguard/eu.private").strip()
    eu_pub = run(eu, "cat /etc/wireguard/eu.public").strip()

    upload_text(eu, "/usr/local/bin/wg0-up.sh", EU_WG_UP)
    upload_text(eu, "/usr/local/bin/wg0-down.sh", EU_WG_DOWN)

    wg_conf = f"""[Interface]
Address = 10.66.66.1/24
ListenPort = {WG_PORT}
PrivateKey = {eu_priv}
PostUp = /usr/local/bin/wg0-up.sh
PostDown = /usr/local/bin/wg0-down.sh

[Peer]
PublicKey = {ru_pub}
AllowedIPs = 10.66.66.2/32
"""
    upload_text(eu, "/etc/wireguard/wg0.conf", wg_conf, mode=0o600)
    print("EU wg:", run(eu, "systemctl restart wg-quick@wg0 && wg show && curl -sS --max-time 8 https://api.ipify.org"))

    ru = ssh(RU, RU_PW)
    upload_text(ru, "/usr/local/bin/xui-patch-ru-wg-exit.py", (BASE / "xui_patch_ru_wg_exit.py").read_text(), mode=0o644)

    # patch script: remove killall when imported as module; update main block
    patch_main = run(
        ru,
        r"""python3 << 'PY'
from pathlib import Path
p = Path('/usr/local/bin/xui-patch-ru-wg-exit.py')
text = p.read_text()
text = text.replace(
    '    subprocess.run(["killall", "-9", "xray-linux-amd64"], check=False)\n    print("patched_wg")',
    '    print("patched_wg")',
)
p.write_text(text)
PY""",
        20,
    )
    print(patch_main)

    upload_text(ru, "/usr/local/bin/xray-wrapper.sh", XRAY_WRAPPER)
    run(
        ru,
        """
X=/usr/local/x-ui/bin/xray-linux-amd64
if [ ! -f /usr/local/x-ui/bin/xray-linux-amd64.real ]; then
  mv "$X" /usr/local/x-ui/bin/xray-linux-amd64.real
  ln -sf /usr/local/bin/xray-wrapper.sh "$X"
fi
rm -f /etc/cron.d/xui-wg-exit /etc/cron.d/xray-wg-route
systemctl restart x-ui
sleep 4
""",
        60,
    )

    verify = run(
        ru,
        f"""
wg show
curl -sS --max-time 12 --interface wg0 https://api.ipify.org || echo wg-curl-fail
python3 -c "import json;c=json.load(open('/usr/local/x-ui/bin/config.json')); print('outbounds', [o.get('tag') for o in c.get('outbounds',[])]); print('wg_exit', any(o.get('tag')=='eu-wg-exit' for o in c.get('outbounds',[]))); print('rules0', c.get('routing',{{}}).get('rules',[{{}}])[0])"
ls -la /usr/local/x-ui/bin/xray-linux-amd64*
""",
        40,
    )
    print("RU VERIFY:", verify)

    eu.close()
    ru.close()


if __name__ == "__main__":
    main()
