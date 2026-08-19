#!/usr/bin/env python3
"""
Авто-маппинг имён доп. полей в МойСклад → ключи .env (ATTR_*).
Опрашивает API МС с retry; когда сервис вернётся, прописывает UUID в .env, перезапускает loyalty.service
и регистрирует webhooks.

Запуск:
  cd /root/loyalty-service && . .venv/bin/activate
  python scripts/finalize_metadata.py
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# === читаем .env вручную (без bash) ===
def read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for ln in path.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        if "=" not in ln:
            continue
        k, v = ln.split("=", 1)
        out[k.strip()] = v
    return out


def write_env_keys(path: Path, updates: dict[str, str]) -> None:
    """Обновляет/добавляет ключи в .env, не трогая остальное."""
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = content.splitlines()
    keys_present: set[str] = set()
    for i, ln in enumerate(lines):
        m = re.match(r"^([A-Z0-9_]+)=", ln)
        if not m:
            continue
        k = m.group(1)
        if k in updates:
            lines[i] = f"{k}={updates[k]}"
            keys_present.add(k)
    for k, v in updates.items():
        if k not in keys_present:
            lines.append(f"{k}={v}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# === МС: имя поля → ключ в .env ===
COUNTERPARTY_NAME_TO_KEY: dict[str, str] = {
    "уровень": "ATTR_LOYALTY_TIER",
    "статус": "ATTR_LOYALTY_STATUS",
    "активные бонусы": "ATTR_ACTIVE_BONUSES",
    "ожидают активации": "ATTR_PENDING_BONUSES",
}

ORDER_NAME_TO_KEY: dict[str, str] = {
    "статус пл": "ATTR_ORDER_LOYALTY_STATUS",
    "уровень пл": "ATTR_ORDER_LOYALTY_TIER",
    "активно бонусов": "ATTR_ORDER_ACTIVE_BONUSES",
    "списано бонусов": "ATTR_ORDER_SPEND_BONUSES",
    "комментарии пл": "ATTR_ORDER_LOYALTY_COMMENT",
}


def fetch_attrs(api_base: str, token: str, path: str, *, retries: int = 60, sleep_s: int = 30) -> list[dict]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json;charset=utf-8",
        "Accept-Encoding": "gzip",
    }
    last_err = ""
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(f"{api_base.rstrip('/')}{path}", headers=headers, timeout=30)
            if r.status_code == 200:
                data = r.json()
                raw_attrs = data.get("rows") or data.get("attributes") or []
                if isinstance(raw_attrs, dict):
                    return list(raw_attrs.get("rows") or [])
                return list(raw_attrs)
            last_err = f"HTTP {r.status_code}"
        except Exception as e:
            last_err = str(e)
        print(f"[{attempt}/{retries}] {path} → {last_err}; ждём {sleep_s}s…", flush=True)
        time.sleep(sleep_s)
    raise RuntimeError(f"Не удалось получить {path}: {last_err}")


def map_attrs(attrs: list[dict], name_to_key: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for a in attrs:
        name = str(a.get("name") or "").strip().lower()
        attr_id = str(a.get("id") or "")
        if not name or not attr_id:
            continue
        if name in name_to_key:
            out[name_to_key[name]] = attr_id
    return out


def main() -> None:
    env = read_env(ENV_PATH)
    token = env.get("MS_TOKEN") or os.environ.get("MS_TOKEN") or ""
    api_base = env.get("API_BASE") or os.environ.get("API_BASE") or "https://api.moysklad.ru/api/remap/1.2"
    if not token:
        print("Нужен MS_TOKEN", file=sys.stderr)
        sys.exit(1)

    print("=== Жду доступности API МойСклад ===", flush=True)
    cp_attrs = fetch_attrs(api_base, token, "/entity/counterparty/metadata/attributes")
    print(f"Counterparty: {len(cp_attrs)} attrs", flush=True)
    co_attrs = fetch_attrs(api_base, token, "/entity/customerorder/metadata/attributes")
    print(f"CustomerOrder: {len(co_attrs)} attrs", flush=True)

    updates: dict[str, str] = {}
    updates.update(map_attrs(cp_attrs, COUNTERPARTY_NAME_TO_KEY))
    updates.update(map_attrs(co_attrs, ORDER_NAME_TO_KEY))

    expected = (
        list(COUNTERPARTY_NAME_TO_KEY.values())
        + list(ORDER_NAME_TO_KEY.values())
    )
    missing = [k for k in expected if k not in updates]

    print("=== Маппинг ===", flush=True)
    for k in expected:
        print(f"  {k} = {updates.get(k, '<НЕ НАЙДЕНО>')}")
    if missing:
        print(f"\nВНИМАНИЕ: не найдены поля по именам: {', '.join(missing)}.", flush=True)
        print("Создайте недостающие поля в МойСклад с указанными именами и перезапустите скрипт.", flush=True)

    if updates:
        write_env_keys(ENV_PATH, updates)
        print(f"\n.env обновлён ({len(updates)} ключей).", flush=True)
    else:
        print("\nНечего обновлять — поля не найдены.", file=sys.stderr)
        sys.exit(2)

    print("=== Перезапуск loyalty.service ===", flush=True)
    subprocess.run(["systemctl", "restart", "loyalty.service"], check=False)
    time.sleep(3)
    subprocess.run(["systemctl", "is-active", "loyalty.service"], check=False)

    print("=== Регистрация webhooks ===", flush=True)
    env_for_child = os.environ.copy()
    env_for_child["MS_TOKEN"] = token
    env_for_child["API_BASE"] = api_base
    env_for_child["WEBHOOK_PUBLIC_URL"] = env.get("WEBHOOK_PUBLIC_URL", "")
    subprocess.run(
        [str(ROOT / ".venv/bin/python"), str(ROOT / "scripts/register_webhooks.py")],
        cwd=str(ROOT),
        env=env_for_child,
        check=False,
    )

    print("\n=== Готово ===", flush=True)


if __name__ == "__main__":
    main()
