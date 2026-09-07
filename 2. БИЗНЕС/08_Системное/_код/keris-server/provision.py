#!/usr/bin/env python3
"""Полное развёртывание Keris Club на чистый VPS: nginx + статика + API + бот Карины.

`deploy.py` обновляет только код API на уже настроенном хосте. Этот скрипт
поднимает стек с нуля — нужен при переезде на другой сервер (например, когда
у прежнего VPS закончилась оплата).

Что делает:
  1. ставит пакеты (nginx, python3-venv, certbot);
  2. кладёт `keris-server` в /root/keris-server и `keris-admin-bot` в /root/keris-admin-bot;
  3. пишет .env из переменных окружения запуска (секреты не хранятся в коде);
  4. кладёт статику онлайн-записи в /var/www/keris (prototype.html как index.html + legal/);
  5. настраивает nginx: статика в корне, /api/ и /webhooks/ — прокси на 127.0.0.1:8091;
  6. поднимает systemd-юниты и (опционально) HTTPS через certbot на <IP>.sslip.io.

Запуск:
    KERIS_DEPLOY_HOST=194.87.118.214 \
    KERIS_DEPLOY_PASSWORD=... \
    YCLIENTS_PARTNER_TOKEN=... YCLIENTS_USER_TOKEN=... YCLIENTS_COMPANY_ID=... \
    KARINA_BOT_TOKEN=... KARINA_TELEGRAM_IDS=... CLIENT_BOT_TOKEN=... ADMIN_API_KEY=... \
    python3 provision.py [--no-https]
"""
from __future__ import annotations

import argparse
import os
import secrets
import sys
import time
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parent
ADMIN_BOT_DIR = ROOT.parent / "keris-admin-bot"
STAFF_BOT_DIR = ROOT.parent / "keris-staff-bot"
STATIC_DIR = ROOT.parent / "keris-online-zapis"

HOST = os.environ.get("KERIS_DEPLOY_HOST", "")
USER = os.environ.get("KERIS_DEPLOY_USER", "root")
PASSWORD = os.environ.get("KERIS_DEPLOY_PASSWORD", "")

REMOTE_APP = "/root/keris-server"
REMOTE_BOT = "/root/keris-admin-bot"
REMOTE_STAFF_BOT = "/root/keris-staff-bot"
WEB_ROOT = "/var/www/keris"
PHOTOS_DIR = "/var/lib/keris/photos"

NGINX_COMMON = """
    root {web_root};
    index index.html;

    location /api/ {{
        proxy_pass http://127.0.0.1:8091/api/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 30s;
    }}

    location /webhooks/ {{
        proxy_pass http://127.0.0.1:8091/webhooks/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }}

    location /health {{
        proxy_pass http://127.0.0.1:8091/health;
    }}

    # Фото до/после: файлы лежат у нас, отдаёт их nginx напрямую (см. app/photos.py).
    location /media/ {{
        alias {photos_dir}/;
        expires 30d;
        add_header Cache-Control "public";
    }}

    # Страница записи — один файл index.html, обновляется деплоем. Без no-store
    # браузер и iframe на сайте держат прошлую версию по эвристике кеша: после
    # правки текстов клиент ещё сутки видит старый экран входа.
    location / {{
        try_files $uri $uri/ /index.html;
        add_header Cache-Control "no-store, must-revalidate";
    }}
}}
"""


def nginx_site(host: str, web_root: str, cert_dir: str = "") -> str:
    """Единственный сайт на хосте: default_server, чтобы старые конфиги не перехватывали.

    HTTPS-блок добавляется, только если сертификат уже выпущен (иначе nginx не стартует
    и certbot не сможет пройти проверку по 80 порту).
    """
    names = f"{host} {host}.sslip.io kerisclub-analytics.ru www.kerisclub-analytics.ru _"
    common = NGINX_COMMON.format(web_root=web_root, photos_dir=PHOTOS_DIR)
    if not cert_dir:
        return f"server {{\n    listen 80 default_server;\n    listen [::]:80 default_server;\n" \
               f"    server_name {names};\n{common}"
    return (
        f"server {{\n    listen 80 default_server;\n    listen [::]:80 default_server;\n"
        f"    server_name {names};\n    return 301 https://$host$request_uri;\n}}\n\n"
        f"server {{\n    listen 443 ssl default_server;\n    listen [::]:443 ssl default_server;\n"
        f"    server_name {names};\n"
        f"    ssl_certificate {cert_dir}/fullchain.pem;\n"
        f"    ssl_certificate_key {cert_dir}/privkey.pem;\n"
        f"    include /etc/letsencrypt/options-ssl-nginx.conf;\n"
        f"    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;\n{common}"
    )


def connect_with_retry(attempts: int = 8) -> paramiko.SSHClient:
    """Канал до хоста периодически рвётся на чтении SSH-баннера — пробуем несколько раз."""
    last = None
    for i in range(attempts):
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(HOST, username=USER, password=PASSWORD, timeout=60,
                           banner_timeout=90, auth_timeout=60,
                           allow_agent=False, look_for_keys=False)
            return client
        except Exception as e:  # noqa: BLE001
            last = e
            print(f"  подключение, попытка {i + 1}: {type(e).__name__}: {e}", file=sys.stderr)
            time.sleep(4)
    raise SystemExit(f"не удалось подключиться к {HOST}: {last}")


def run(client: paramiko.SSHClient, cmd: str, check: bool = False) -> tuple[int, str, str]:
    _, stdout, stderr = client.exec_command(cmd)
    code = stdout.channel.recv_exit_status()
    out, err = stdout.read().decode(), stderr.read().decode()
    print(f"$ {cmd}")
    if out.strip():
        print(out.rstrip())
    if err.strip():
        print("  stderr:", err.rstrip())
    if check and code != 0:
        raise SystemExit(f"команда завершилась с кодом {code}: {cmd}")
    return code, out, err


def put_dir(sftp: paramiko.SFTPClient, local: Path, remote: str, skip: set[str] = frozenset()) -> None:
    try:
        sftp.mkdir(remote)
    except IOError:
        pass
    for item in sorted(local.iterdir()):
        if item.name in skip or item.name.startswith(".") or item.name == "__pycache__" or item.suffix == ".pyc":
            continue
        rpath = f"{remote}/{item.name}"
        if item.is_dir():
            put_dir(sftp, item, rpath, skip)
        else:
            sftp.put(str(item), rpath)


def write_remote(sftp: paramiko.SFTPClient, remote_path: str, content: str) -> None:
    with sftp.open(remote_path, "w") as f:
        f.write(content)


def server_env(admin_key: str, database_url: str) -> str:
    lines = [
        f"DATABASE_URL={database_url}",
        f'YCLIENTS_PARTNER_TOKEN={os.environ.get("YCLIENTS_PARTNER_TOKEN", "")}',
        f'YCLIENTS_USER_TOKEN={os.environ.get("YCLIENTS_USER_TOKEN", "")}',
        f'YCLIENTS_COMPANY_ID={os.environ.get("YCLIENTS_COMPANY_ID", "")}',
        f'YCLIENTS_APPLICATION_ID={os.environ.get("YCLIENTS_APPLICATION_ID", "")}',
        f'YCLIENTS_WEBHOOK_SECRET={os.environ.get("YCLIENTS_WEBHOOK_SECRET", secrets.token_urlsafe(16))}',
        f'AMOCRM_LONG_LIVED_TOKEN={os.environ.get("AMOCRM_LONG_LIVED_TOKEN", "")}',
        # Домен аккаунта: на api-b.amocrm.ru тот же токен отвечает 401.
        f'AMOCRM_BASE_URL={os.environ.get("AMOCRM_BASE_URL", "https://kerisclub.amocrm.ru")}',
        f'AMOCRM_PIPELINE_GROOMING_ID={os.environ.get("AMOCRM_PIPELINE_GROOMING_ID", "11225138")}',
        f'KARINA_BOT_TOKEN={os.environ.get("KARINA_BOT_TOKEN", "")}',
        f'KARINA_TELEGRAM_IDS={os.environ.get("KARINA_TELEGRAM_IDS", "")}',
        f'CLIENT_BOT_TOKEN={os.environ.get("CLIENT_BOT_TOKEN", "")}',
        f'MAX_BOT_TOKEN={os.environ.get("MAX_BOT_TOKEN", "")}',
        # Куда отправляем клиента за кодом входа и напоминаниями.
        f'CLIENT_BOT_USERNAME={os.environ.get("CLIENT_BOT_USERNAME", "kerisclubbot")}',
        f'MAX_BOT_USERNAME={os.environ.get("MAX_BOT_USERNAME", "id775149185013_bot")}',
        # Бот фото-отчётов администратора и его whitelist.
        f'STAFF_BOT_TOKEN={os.environ.get("STAFF_BOT_TOKEN", "")}',
        f'STAFF_TELEGRAM_IDS={os.environ.get("STAFF_TELEGRAM_IDS", "")}',
        f"PHOTOS_DIR={PHOTOS_DIR}",
        "MEDIA_BASE_URL=/media",
        # Абсолютные ссылки на фото — для карточки сделки в amoCRM.
        f'PUBLIC_BASE_URL={os.environ.get("PUBLIC_BASE_URL", "")}',
        f'REACTIVATION_DAYS={os.environ.get("REACTIVATION_DAYS", "60")}',
        # SMS клиентам (targetsms.ru): без токена канал молчит, но строки
        # должны быть в .env, иначе повторный provision потихоньку его выключит.
        f'TARGETSMS_ENABLED={os.environ.get("TARGETSMS_ENABLED", "false")}',
        f'TARGETSMS_TOKEN={os.environ.get("TARGETSMS_TOKEN", "")}',
        f'TARGETSMS_SENDER={os.environ.get("TARGETSMS_SENDER", "KerisClub")}',
        # Свой секрет подписи кодов входа: не зависит от ключа SMS-провайдера.
        f'OTP_SECRET={os.environ.get("OTP_SECRET", secrets.token_urlsafe(24))}',
        f"ADMIN_API_KEY={admin_key}",
        "SALON_TZ=Europe/Moscow",
        "HOST=127.0.0.1",
        "PORT=8091",
    ]
    return "\n".join(lines) + "\n"


def bot_env(admin_key: str) -> str:
    return "\n".join([
        f'KARINA_BOT_TOKEN={os.environ.get("KARINA_BOT_TOKEN", "")}',
        f'KARINA_TELEGRAM_IDS={os.environ.get("KARINA_TELEGRAM_IDS", "")}',
        "KERIS_SERVER_URL=http://127.0.0.1:8091",
        f"ADMIN_API_KEY={admin_key}",
    ]) + "\n"


def staff_bot_env(admin_key: str) -> str:
    """Бот фото-отчётов: свой токен и свой whitelist, отдельно от бота Карины."""
    return "\n".join([
        f'STAFF_BOT_TOKEN={os.environ.get("STAFF_BOT_TOKEN", "")}',
        f'STAFF_TELEGRAM_IDS={os.environ.get("STAFF_TELEGRAM_IDS", "")}',
        "KERIS_SERVER_URL=http://127.0.0.1:8091",
        f"ADMIN_API_KEY={admin_key}",
        f"STATE_FILE={REMOTE_STAFF_BOT}/state.json",
    ]) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-https", action="store_true", help="не запускать certbot")
    parser.add_argument("--keep-env", action="store_true", help="не перезаписывать существующие .env")
    args = parser.parse_args()

    if not HOST or not PASSWORD:
        print("нужны KERIS_DEPLOY_HOST и KERIS_DEPLOY_PASSWORD", file=sys.stderr)
        return 1

    client = connect_with_retry()
    sftp = client.open_sftp()

    print("== пакеты ==")
    run(client, "apt-get update -y && apt-get install -y nginx python3-venv python3-pip certbot "
                "python3-certbot-nginx tzdata postgresql")
    # слоты и лид-тайм считаются в местном времени салона
    run(client, "timedatectl set-timezone Europe/Moscow; date")

    print("== PostgreSQL ==")
    run(client, "systemctl enable --now postgresql")
    # При --keep-env берём пароль из существующего .env: если сменить его в БД,
    # а в .env оставить старый, сервис отвалится при следующем переподключении.
    database_url = ""
    if args.keep_env:
        _, out, _ = run(client, f"grep -h '^DATABASE_URL=' {REMOTE_APP}/.env 2>/dev/null | head -1")
        if out.strip().startswith("DATABASE_URL=postgresql"):
            database_url = out.strip().split("=", 1)[1]
            print("  пароль БД взят из существующего .env")
    if not database_url:
        db_password = os.environ.get("KERIS_DB_PASSWORD") or secrets.token_urlsafe(18)
        run(client, "sudo -u postgres psql -tc \"SELECT 1 FROM pg_roles WHERE rolname='keris'\" | grep -q 1 || "
                    f"sudo -u postgres psql -c \"CREATE ROLE keris LOGIN PASSWORD '{db_password}'\"")
        run(client, f"sudo -u postgres psql -c \"ALTER ROLE keris PASSWORD '{db_password}'\"")
        database_url = f"postgresql+psycopg://keris:{db_password}@127.0.0.1:5432/keris_grooming"
    run(client, "sudo -u postgres psql -tc \"SELECT 1 FROM pg_database WHERE datname='keris_grooming'\" | grep -q 1 || "
                "sudo -u postgres createdb -O keris keris_grooming")

    print("== код API ==")
    run(client, f"mkdir -p {REMOTE_APP} {WEB_ROOT}/legal")
    # Фото до/после переживают переустановку кода: хранилище вне каталога приложения.
    run(client, f"mkdir -p {PHOTOS_DIR} && chmod 755 /var/lib/keris {PHOTOS_DIR}")
    sftp.put(str(ROOT / "requirements.txt"), f"{REMOTE_APP}/requirements.txt")
    put_dir(sftp, ROOT / "app", f"{REMOTE_APP}/app", skip={"keris_dev.db"})
    sftp.put(str(ROOT / "keris-server.service"), "/etc/systemd/system/keris-server.service")

    print("== код бота Карины ==")
    run(client, f"mkdir -p {REMOTE_BOT}")
    sftp.put(str(ADMIN_BOT_DIR / "bot.py"), f"{REMOTE_BOT}/bot.py")
    sftp.put(str(ADMIN_BOT_DIR / "keris-admin-bot.service"), "/etc/systemd/system/keris-admin-bot.service")

    print("== код бота фото (администратор) ==")
    run(client, f"mkdir -p {REMOTE_STAFF_BOT}")
    sftp.put(str(STAFF_BOT_DIR / "bot.py"), f"{REMOTE_STAFF_BOT}/bot.py")
    sftp.put(str(STAFF_BOT_DIR / "keris-staff-bot.service"), "/etc/systemd/system/keris-staff-bot.service")

    print("== .env ==")
    # один и тот же ADMIN_API_KEY у API и бота — иначе бот получит 401
    admin_key = os.environ.get("ADMIN_API_KEY") or secrets.token_urlsafe(24)
    print(f"ADMIN_API_KEY (записать в ДОСТУПЫ.md): {admin_key}")
    for path, content in ((f"{REMOTE_APP}/.env", server_env(admin_key, database_url)),
                          (f"{REMOTE_BOT}/.env", bot_env(admin_key)),
                          (f"{REMOTE_STAFF_BOT}/.env", staff_bot_env(admin_key))):
        code, _, _ = run(client, f"test -f {path}")
        if code == 0 and args.keep_env:
            print(f"  {path} уже есть — оставляю (--keep-env)")
            continue
        write_remote(sftp, path, content)
        run(client, f"chmod 600 {path}")

    print("== статика онлайн-записи ==")
    sftp.put(str(STATIC_DIR / "prototype.html"), f"{WEB_ROOT}/index.html")
    for extra in ("telegram-web-app.js", "tilda-embed.html"):
        local = STATIC_DIR / extra
        if local.exists():
            sftp.put(str(local), f"{WEB_ROOT}/{extra}")
    for page in sorted((STATIC_DIR / "legal").glob("*.html")):
        sftp.put(str(page), f"{WEB_ROOT}/legal/{page.name}")

    print("== nginx ==")
    cert_dir = f"/etc/letsencrypt/live/{HOST}.sslip.io"
    has_cert, _, _ = run(client, f"test -f {cert_dir}/fullchain.pem")
    write_remote(sftp, "/etc/nginx/sites-available/keris",
                 nginx_site(HOST, WEB_ROOT, cert_dir if has_cert == 0 else ""))
    run(client, "ln -sf /etc/nginx/sites-available/keris /etc/nginx/sites-enabled/keris")
    # прежние конфиги (Этап 1 и дефолт) перехватывали default_server и отдавали старую статику
    run(client, "rm -f /etc/nginx/sites-enabled/default /etc/nginx/sites-enabled/keris-booking")
    run(client, "nginx -t && systemctl reload nginx", check=True)

    print("== venv и сервисы ==")
    run(client, f"cd {REMOTE_APP} && python3 -m venv venv", check=True)
    run(client, f"{REMOTE_APP}/venv/bin/pip install --upgrade pip -q")
    run(client, f"{REMOTE_APP}/venv/bin/pip install -r {REMOTE_APP}/requirements.txt", check=True)
    # бот запускается системным python3 (см. keris-admin-bot.service)
    run(client, "python3 -m pip install --break-system-packages -q requests python-dotenv || python3 -m pip install -q requests python-dotenv")
    run(client, "systemctl daemon-reload")
    run(client, "systemctl enable --now keris-server keris-admin-bot")
    # Бот фото стартует только с токеном: без него сервис падал бы в цикле рестартов.
    if os.environ.get("STAFF_BOT_TOKEN", "").strip():
        run(client, "systemctl enable --now keris-staff-bot")
    else:
        run(client, "systemctl enable keris-staff-bot; systemctl stop keris-staff-bot 2>/dev/null; true")
        print("  STAFF_BOT_TOKEN не передан — бот фото установлен, но не запущен")
    run(client, "sleep 3; systemctl is-active keris-server keris-admin-bot; curl -s http://127.0.0.1:8091/health")

    if not args.no_https and has_cert != 0:
        print("== HTTPS ==")
        email = os.environ.get("CERTBOT_EMAIL", "hello@keris-club.ru")
        run(client, f"certbot certonly --webroot -w {WEB_ROOT} -d {HOST}.sslip.io "
                    f"--non-interactive --agree-tos -m {email}")
        write_remote(sftp, "/etc/nginx/sites-available/keris", nginx_site(HOST, WEB_ROOT, cert_dir))
        run(client, "nginx -t && systemctl reload nginx", check=True)

    print("== проверка снаружи ==")
    run(client, "curl -sk -o /dev/null -w 'api: %{http_code}\\n' https://127.0.0.1/api/masters -H 'Host: "
                f"{HOST}.sslip.io'")
    run(client, "curl -sk -H 'Host: " + f"{HOST}.sslip.io' https://127.0.0.1/api/rules")

    sftp.close()
    client.close()
    print(f"\nГотово. Проверьте: https://{HOST}.sslip.io/  и  https://{HOST}.sslip.io/api/masters")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
