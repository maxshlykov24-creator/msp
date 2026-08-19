#!/bin/bash
# Apply EU relay inbound + iptables allow only RU IP
set -euo pipefail
python3 /usr/local/bin/xui-setup-eu-relay.py
python3 /usr/local/bin/xui-patch-ru-chain.py
# reload xray only
pkill -f 'bin/xray-linux-amd64 run -c bin/config.json' || true
sleep 1
