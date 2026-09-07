#!/usr/bin/env python3
"""Одноразовый перенос Telegram-ботов Keris на зарубежный IPv6-VPS.

RU (194.87.118.214) остаётся: keris-server, PostgreSQL, nginx, Mini App HTML.
EU (2a03:6f02::2d61): keris-bot + keris-admin-bot (long polling → api.telegram.org).

Запуск с машины, у которой есть SSH к RU (paramiko + jump на EU):
  python3 migrate_tg_to_eu.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import paramiko

RU_HOST = os.environ.get("KERIS_RU_HOST", "194.87.118.214")
RU_PASS = os.environ.get("KERIS_RU_PASSWORD", "")
EU_HOST = os.environ.get("KERIS_EU_HOST", "2a03:6f02::2d61")
EU_PASS = os.environ.get("KERIS_EU_PASSWORD", "")
RU_IPV6 = os.environ.get("KERIS_RU_IPV6", "2a03:6f00:a::2:cf84")
BOOKING_HOST = os.environ.get("KERIS_BOOKING_HOST", "194.87.118.214.sslip.io")

ROOT = Path(__file__).resolve().parent
CODE = ROOT.parent  # _код/
BOT = CODE / "keris-bot"
ADMIN = CODE / "keris-admin-bot"


def ru_connect() -> paramiko.SSHClient:
    last = None
    for i in range(6):
        c = paramiko.SSHClient()
        c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            c.connect(
                RU_HOST, username="root", password=RU_PASS, timeout=30,
                allow_agent=False, look_for_keys=False, banner_timeout=45,
            )
            return c
        except Exception as e:  # noqa: BLE001
            last = e
            print(f"  RU connect {i+1}: {e}", file=sys.stderr)
            time.sleep(2)
    raise SystemExit(f"RU unreachable: {last}")


def eu_via(ru: paramiko.SSHClient) -> paramiko.SSHClient:
    chan = ru.get_transport().open_channel(
        "direct-tcpip", (EU_HOST, 22), ("127.0.0.1", 0)
    )
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(
        EU_HOST, username="root", password=EU_PASS, sock=chan, timeout=30,
        allow_agent=False, look_for_keys=False, banner_timeout=45,
    )
    return c


def run(c: paramiko.SSHClient, cmd: str, timeout: int = 60) -> tuple[int, str, str]:
    _, stdout, stderr = c.exec_command(cmd, timeout=timeout)
    code = stdout.channel.recv_exit_status()
    return code, stdout.read().decode(), stderr.read().decode()


def sftp_put_text(sftp: paramiko.SFTPClient, remote: str, text: str) -> None:
    with sftp.open(remote, "w") as f:
        f.write(text)


def sftp_put_file(sftp: paramiko.SFTPClient, local: Path, remote: str) -> None:
    sftp.put(str(local), remote)


def main() -> int:
    if not RU_PASS or not EU_PASS:
        print("нужны KERIS_RU_PASSWORD и KERIS_EU_PASSWORD", file=sys.stderr)
        return 1
    print("== connect RU ==")
    ru = ru_connect()
    print("== connect EU via jump ==")
    eu = eu_via(ru)

    print("== read env from RU ==")
    _, bot_env, _ = run(ru, "cat /root/keris-bot/.env")
    _, admin_env, _ = run(ru, "cat /root/keris-admin-bot/.env")
    _, state, _ = run(ru, "cat /root/keris-bot/state.json 2>/dev/null || echo {}")
    if "BOT_TOKEN=" not in bot_env or "KARINA_BOT_TOKEN=" not in admin_env:
        print("env incomplete", file=sys.stderr)
        return 1

    # admin: API через IPv6 RU (hosts), не localhost
    admin_lines = []
    for line in admin_env.splitlines():
        if line.startswith("KERIS_SERVER_URL="):
            admin_lines.append(f"KERIS_SERVER_URL=https://{BOOKING_HOST}")
        elif line.startswith("STATE_FILE="):
            admin_lines.append("STATE_FILE=/root/keris-admin-bot/state.json")
        else:
            admin_lines.append(line)
    if not any(l.startswith("KERIS_SERVER_URL=") for l in admin_lines):
        admin_lines.append(f"KERIS_SERVER_URL=https://{BOOKING_HOST}")
    admin_env_new = "\n".join(admin_lines).rstrip() + "\n"

    bot_lines = []
    for line in bot_env.splitlines():
        if line.startswith("BOOKING_URL="):
            bot_lines.append(f"BOOKING_URL=https://{BOOKING_HOST}/")
        elif line.startswith("STATE_FILE="):
            bot_lines.append("STATE_FILE=/root/keris-bot/state.json")
        else:
            bot_lines.append(line)
    bot_env_new = "\n".join(bot_lines).rstrip() + "\n"

    print("== stop bots on RU (освобождаем getUpdates) ==")
    run(ru, "systemctl stop keris-bot keris-admin-bot; systemctl disable keris-bot keris-admin-bot")

    print("== provision EU ==")
    run(eu, "mkdir -p /root/keris-bot /root/keris-admin-bot")
    # sslip → RU IPv6 (у EU нет IPv4)
    code, hosts, _ = run(eu, "grep -F '194.87.118.214.sslip.io' /etc/hosts || true")
    if BOOKING_HOST not in hosts:
        run(eu, f"echo '{RU_IPV6} {BOOKING_HOST}' >> /etc/hosts")
        print(f"  /etc/hosts += {RU_IPV6} {BOOKING_HOST}")

    esftp = eu.open_sftp()
    sftp_put_file(esftp, BOT / "bot.py", "/root/keris-bot/bot.py")
    sftp_put_file(esftp, BOT / "keris-bot.service", "/etc/systemd/system/keris-bot.service")
    sftp_put_text(esftp, "/root/keris-bot/.env", bot_env_new)
    sftp_put_text(esftp, "/root/keris-bot/state.json", state if state.strip() else "{}\n")

    sftp_put_file(esftp, ADMIN / "bot.py", "/root/keris-admin-bot/bot.py")
    sftp_put_file(esftp, ADMIN / "keris-admin-bot.service", "/etc/systemd/system/keris-admin-bot.service")
    sftp_put_text(esftp, "/root/keris-admin-bot/.env", admin_env_new)
    # state админа если был
    _, admin_state, _ = run(ru, "cat /root/keris-admin-bot/state.json 2>/dev/null || echo {}")
    sftp_put_text(esftp, "/root/keris-admin-bot/state.json", admin_state if admin_state.strip() else "{}\n")
    esftp.close()

    run(eu, "chmod 600 /root/keris-bot/.env /root/keris-admin-bot/.env")
    run(eu, "systemctl daemon-reload && systemctl enable keris-bot keris-admin-bot && systemctl restart keris-bot keris-admin-bot")

    print("== deploy tg_http + notify/reminders on RU ==")
    rsftp = ru.open_sftp()
    for rel in ("app/tg_http.py", "app/notify_karina.py", "app/reminders.py"):
        sftp_put_file(rsftp, ROOT / rel, f"/root/keris-server/{rel}")
    rsftp.close()
    run(ru, "systemctl restart keris-server")

    time.sleep(3)
    print("== verify EU ==")
    code, out, err = run(eu, "systemctl is-active keris-bot keris-admin-bot; journalctl -u keris-bot -n 6 --no-pager; journalctl -u keris-admin-bot -n 6 --no-pager")
    print(out or err)

    # getMe через IPv6 с EU
    code, out, err = run(
        eu,
        "set -a; . /root/keris-bot/.env; set +a; "
        "curl -6 -sS -m 15 \"https://api.telegram.org/bot${BOT_TOKEN}/getMe\"",
    )
    print("keris-bot getMe:", out[:200] if out else err[:200])

    code, out, err = run(
        eu,
        "set -a; . /root/keris-admin-bot/.env; set +a; "
        "curl -6 -sS -m 15 \"https://api.telegram.org/bot${KARINA_BOT_TOKEN}/getMe\"",
    )
    print("admin getMe:", out[:200] if out else err[:200])

    code, out, err = run(
        eu,
        "curl -6 -sS -m 10 -o /dev/null -w '%{http_code}' "
        f"https://{BOOKING_HOST}/api/masters",
    )
    print("admin→RU API via hosts:", out or err)

    code, out, err = run(ru, "systemctl is-active keris-bot keris-admin-bot keris-server")
    print("RU services (bot/admin/server):", out.strip())

    eu.close()
    ru.close()
    print("OK: боты на EU, сервер записи на RU")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
