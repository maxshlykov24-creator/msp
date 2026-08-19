#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

import requests


def main() -> None:
    env: dict[str, str] = {}
    for line in (Path(__file__).resolve().parent.parent / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            env[key] = value
    headers = {
        "Authorization": "Bearer " + env["MS_TOKEN"],
        "Accept": "application/json;charset=utf-8",
        "Accept-Encoding": "gzip",
    }
    base = env["API_BASE"].rstrip("/")
    names = {"Статус", "Уровень", "Статус ПЛ", "Уровень ПЛ"}
    for path in ("/entity/counterparty/metadata/attributes", "/entity/customerorder/metadata/attributes"):
        data = requests.get(base + path, headers=headers, timeout=60).json()
        print("PATH", path)
        for attr in data.get("rows") or []:
            if attr.get("name") in names:
                print(json.dumps(attr, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
