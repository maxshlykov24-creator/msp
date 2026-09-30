"""Настройки из .env. Секреты в код не пишем."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load_env():
    path = ROOT / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


load_env()


def flag(name):
    return os.environ.get(name, "").strip() == "1"


def sync_enabled():
    return flag("SYNC_ENABLED")


def confirm_disable_native():
    return flag("CONFIRM_DISABLE_NATIVE")


def cutover_date():
    return os.environ.get("CUTOVER_DATE", "").strip()
