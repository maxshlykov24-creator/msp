#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

import requests


def read_env() -> dict[str, str]:
    env: dict[str, str] = {}
    path = Path(__file__).resolve().parent.parent / ".env"
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            env[key] = value
    return env


def main() -> None:
    env = read_env()
    base = env.get("API_BASE", "https://api.moysklad.ru/api/remap/1.2").rstrip("/")
    headers = {
        "Authorization": "Bearer " + env["MS_TOKEN"],
        "Accept": "application/json;charset=utf-8",
        "Accept-Encoding": "gzip",
    }
    paths = [
        "/context/employee",
        "/entity/organization?limit=3",
        "/entity/webhook?limit=20",
        "/entity/customerorder?limit=1",
        "/entity/counterparty?limit=1",
        "/entity/counterparty/metadata/attributes",
        "/entity/customerorder/metadata/attributes",
    ]
    for path in paths:
        r = requests.get(base + path, headers=headers, timeout=60)
        print(f"\n{path} HTTP {r.status_code}")
        try:
            data = r.json()
        except Exception:
            print(r.text[:200])
            continue
        if path == "/context/employee":
            print({k: data.get(k) for k in ("id", "name", "email", "uid")})
            continue
        rows = data.get("rows") or []
        print("rows", len(rows))
        print([(x.get("name"), x.get("id"), x.get("action") or x.get("entityType") or x.get("type")) for x in rows[:10]])


if __name__ == "__main__":
    main()
