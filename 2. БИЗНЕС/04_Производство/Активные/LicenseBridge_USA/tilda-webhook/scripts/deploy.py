#!/usr/bin/env python3
"""Выкладка хаба LicenseBridge на VPS 72.56.123.137.

    python3 scripts/deploy.py            — синхронизировать код, пересобрать, дождаться health
    python3 scripts/deploy.py --dry-run  — показать, что уйдёт на сервер, ничего не менять

`.env` живёт только на сервере и никогда не перезаписывается: там флаги, токены и
номера линий, которые правятся на живом сервисе. Прошлая версия этого скрипта
писала `.env` заново из пяти переменных — так терялись ENABLE_*, TELEPHONY_* и
блокеры, восстанавливать приходилось из `.env.bak-*`.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

HOST = "licensebridge-hub"  # алиас из ~/.ssh/config, ключ licensebridge_hub_deploy
REMOTE = "/opt/licensebridge-tilda-webhook"
ROOT = Path(__file__).resolve().parents[1]
ITEMS = ["app", "migrations", "scripts", "tests", "Dockerfile",
         "docker-compose.yml", "Caddyfile", "requirements.txt", ".env.example"]


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    print("»", " ".join(cmd))
    return subprocess.run(cmd, check=True, **kw)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    missing = [name for name in ITEMS if not (ROOT / name).exists()]
    if missing:
        print(f"нет файлов для выкладки: {', '.join(missing)}", file=sys.stderr)
        print("код хаба живёт на сервере; снимите его перед деплоем "
              "(см. RUNBOOK.md, раздел «Снять код с сервера»)", file=sys.stderr)
        return 1

    rsync = ["rsync", "-az", "--delete",
             "--exclude", ".env", "--exclude", ".env.bak*",
             "--exclude", "__pycache__", "--exclude", "*.pyc",
             "--exclude", ".pytest_cache"]
    if args.dry_run:
        rsync += ["--dry-run", "-v"]
    rsync += [str(ROOT / name) for name in ITEMS]
    rsync += [f"{HOST}:{REMOTE}/"]
    run(rsync)
    if args.dry_run:
        print("dry-run: сервер не тронут")
        return 0

    # без .env контейнеры поднимутся без токенов и молча начнут игнорировать Kommo
    run(["ssh", HOST, f"test -f {REMOTE}/.env"])
    run(["ssh", HOST, f"cd {REMOTE} && docker compose up -d --build"])

    for attempt in range(30):
        probe = subprocess.run(
            ["ssh", HOST, "docker exec lb-hub-api curl -fsS http://127.0.0.1:8080/health"],
            capture_output=True, text=True,
        )
        if probe.returncode == 0:
            print(f"health ok: {probe.stdout.strip()}")
            break
        time.sleep(5)
    else:
        print("health не поднялся за 150 секунд", file=sys.stderr)
        run(["ssh", HOST, f"cd {REMOTE} && docker compose logs --tail=40"])
        return 3

    run(["ssh", HOST, f"cd {REMOTE} && docker compose ps"])
    print("готово")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
