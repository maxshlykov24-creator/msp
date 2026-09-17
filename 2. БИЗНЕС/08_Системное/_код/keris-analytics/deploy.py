#!/usr/bin/env python3
"""Выкладка пульса на RU VPS рядом с keris-server.

Корень kerisclub-analytics.ru занят онлайн-записью — сервис слушает 127.0.0.1:8092,
nginx отдаёт его с /pulse/.

    KERIS_DEPLOY_HOST=194.87.118.214 KERIS_DEPLOY_PASSWORD=... python3 deploy.py
"""
from __future__ import annotations

import os
import secrets
import sys
import time
from pathlib import Path

import paramiko

HOST = os.environ.get("KERIS_DEPLOY_HOST", "194.87.118.214")
USER = os.environ.get("KERIS_DEPLOY_USER", "root")
PASSWORD = os.environ.get("KERIS_DEPLOY_PASSWORD", "")
ROOT = Path(__file__).resolve().parent
REMOTE = "/root/keris-analytics"


def _password_from_dostupy() -> str:
    """Пароль VPS в ДОСТУПЫ.md, в чат и логи не пишем."""
    path = (
        ROOT.parents[2]
        / "04_Производство"
        / "Активные"
        / "Keris_Club"
        / "06_Доступы"
        / "ДОСТУПЫ.md"
    )
    if not path.exists():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if "194.87.118.214" in line and "root /" in line:
            marker = "root / `"
            i = line.find(marker)
            if i < 0:
                continue
            rest = line[i + len(marker):]
            return rest.split("`")[0]
    return ""

NGINX_SNIPPET = """
    # Пульс собственника (keris-analytics). Корень домена уже занят онлайн-записью.
    location /pulse/ {
        proxy_pass http://127.0.0.1:8092/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 60s;
    }
"""


def connect(attempts: int = 8) -> paramiko.SSHClient:
    last = None
    for i in range(attempts):
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                HOST, username=USER, password=PASSWORD, timeout=60,
                banner_timeout=90, auth_timeout=60, allow_agent=False, look_for_keys=False,
            )
            return client
        except Exception as e:  # noqa: BLE001
            last = e
            print(f"  попытка {i + 1}: {type(e).__name__}: {e}", file=sys.stderr)
            time.sleep(4)
    raise SystemExit(f"не удалось подключиться: {last}")


def run(client: paramiko.SSHClient, cmd: str, check: bool = False) -> tuple[int, str, str]:
    _, stdout, stderr = client.exec_command(cmd)
    code = stdout.channel.recv_exit_status()
    out, err = stdout.read().decode(), stderr.read().decode()
    print(f"$ {cmd}")
    if out.strip():
        print(out.rstrip())
    if err.strip():
        print("  stderr:", err.rstrip())
    if check and code:
        raise SystemExit(f"команда упала с {code}: {cmd}")
    return code, out, err


def upload_dir(sftp: paramiko.SFTPClient, local: Path, remote: str) -> None:
    try:
        sftp.mkdir(remote)
    except OSError:
        pass
    for item in local.iterdir():
        if item.name in {"__pycache__", ".venv", "data"} or item.name.endswith(".pyc"):
            continue
        rpath = f"{remote}/{item.name}"
        if item.is_dir():
            upload_dir(sftp, item, rpath)
        else:
            sftp.put(str(item), rpath)


def main() -> int:
    global PASSWORD
    PASSWORD = PASSWORD or _password_from_dostupy()
    if not PASSWORD:
        print("KERIS_DEPLOY_PASSWORD не задан", file=sys.stderr)
        return 1

    client = connect()
    run(client, f"mkdir -p {REMOTE}/data {REMOTE}/web/assets")
    sftp = client.open_sftp()
    sftp.put(str(ROOT / "requirements.txt"), f"{REMOTE}/requirements.txt")
    sftp.put(str(ROOT / "keris-analytics.service"), "/etc/systemd/system/keris-analytics.service")
    upload_dir(sftp, ROOT / "app", f"{REMOTE}/app")
    upload_dir(sftp, ROOT / "web", f"{REMOTE}/web")
    sftp.close()

    run(client, f"cd {REMOTE} && python3 -m venv venv")
    run(client, f"{REMOTE}/venv/bin/pip install --upgrade pip -q")
    run(client, f"{REMOTE}/venv/bin/pip install -r {REMOTE}/requirements.txt", check=True)

    code, _, _ = run(client, f"test -f {REMOTE}/.env")
    if code != 0:
        plain = secrets.token_urlsafe(10)
        _, hash_out, _ = run(
            client,
            f"{REMOTE}/venv/bin/python -c \"import bcrypt,sys; print(bcrypt.hashpw(sys.argv[1].encode(), bcrypt.gensalt()).decode())\" {plain}",
        )
        pwd_hash = hash_out.strip().splitlines()[-1]
        session = secrets.token_urlsafe(48)
        db_pass = secrets.token_urlsafe(18)
        run(client, (
            "sudo -u postgres psql -tc \"SELECT 1 FROM pg_roles WHERE rolname='keris_analytics'\" | grep -q 1 || "
            f"sudo -u postgres psql -c \"CREATE ROLE keris_analytics LOGIN PASSWORD '{db_pass}'\""
        ))
        run(client, "sudo -u postgres psql -d keris_grooming -c "
            "\"GRANT CONNECT ON DATABASE keris_grooming TO keris_analytics; "
            "GRANT USAGE ON SCHEMA public TO keris_analytics; "
            "GRANT SELECT ON ALL TABLES IN SCHEMA public TO keris_analytics; "
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO keris_analytics;\"")
        env = "\n".join([
            "AMOCRM_BASE=https://kerisclub.amocrm.ru",
            "AMOCRM_TOKEN=$(grep -E '^AMOCRM_LONG_LIVED_TOKEN=' /root/keris-server/.env | cut -d= -f2-)",
            "AMOCRM_ACCOUNT_ID=33117150",
            f"DATABASE_URL=postgresql+psycopg://keris_analytics:{db_pass}@127.0.0.1:5432/keris_grooming",
            "AUTH_LOGIN=karina",
            f"AUTH_PASSWORD_HASH={pwd_hash}",
            f"SESSION_SECRET={session}",
            "AUTH_COOKIE_SECURE=true",
            "AUTH_COOKIE_PATH=/pulse",
            "TZ=Europe/Moscow",
            "COLLECT_INTERVAL_MIN=15",
            "STALE_AFTER_HOURS=2",
            f"DATA_DIR={REMOTE}/data",
            "RUN_SCHEDULER=true",
            "",
        ])
        # токен amoCRM подставляем на сервере из уже лежащего .env записи
        run(client, f"cat > {REMOTE}/.env <<'EOF'\n{env}\nEOF")
        run(client, (
            "TOKEN=$(grep -E '^AMOCRM_LONG_LIVED_TOKEN=' /root/keris-server/.env | cut -d= -f2-); "
            f"sed -i \"s|^AMOCRM_TOKEN=.*|AMOCRM_TOKEN=$TOKEN|\" {REMOTE}/.env"
        ))
        run(client, f"chmod 600 {REMOTE}/.env")
        print("ПАРОЛЬ_КАРИНЫ_ЛОГИН=karina")
        print(f"ПАРОЛЬ_КАРИНЫ={plain}")
    else:
        print(".env уже есть — не перезаписываю")

    run(client, (
        "python3 - <<'PY'\n"
        "from pathlib import Path\n"
        "p = Path('/etc/nginx/sites-available/keris')\n"
        "t = p.read_text()\n"
        "snip = '''" + NGINX_SNIPPET + "'''\n"
        "if 'location /pulse/' in t:\n"
        "    print('nginx /pulse/ уже есть')\n"
        "else:\n"
        "    needle = '    location /health {'\n"
        "    if needle not in t:\n"
        "        raise SystemExit('не нашёл location /health')\n"
        "    t = t.replace(needle, snip + '\\n' + needle, 1)\n"
        "    # в HTTPS-блоке тоже, если health встречается дважды\n"
        "    if t.count('location /pulse/') < t.count('location /health'):\n"
        "        t = t.replace(needle, snip + '\\n' + needle, 1)\n"
        "    p.write_text(t)\n"
        "    print('nginx: добавил /pulse/')\n"
        "PY"
    ))
    run(client, "nginx -t && systemctl reload nginx", check=True)
    run(client, "systemctl daemon-reload")
    run(client, "systemctl enable keris-analytics")
    run(client, "systemctl restart keris-analytics")
    run(client, "sleep 4 && systemctl is-active keris-analytics")
    run(client, (
        f"cd {REMOTE} && . venv/bin/activate && "
        "python -c 'from app.collector import run_collection; print(run_collection())'"
    ))
    run(client, "sleep 1 && curl -sS http://127.0.0.1:8092/health")
    run(client, "curl -sS -o /dev/null -w '%{http_code}' https://127.0.0.1/pulse/health "
        "-H 'Host: kerisclub-analytics.ru' -k")
    client.close()
    print("готово: https://kerisclub-analytics.ru/pulse/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
