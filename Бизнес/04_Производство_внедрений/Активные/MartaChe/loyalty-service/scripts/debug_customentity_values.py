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
    base = env["API_BASE"].rstrip("/")
    headers = {
        "Authorization": "Bearer " + env["MS_TOKEN"],
        "Accept": "application/json;charset=utf-8",
        "Accept-Encoding": "gzip",
    }
    ids = [
        "294b0f14-42d9-11f1-0a80-04e30042a759",
        "604ac308-42d9-11f1-0a80-0f9700432c4b",
    ]
    for entity_id in ids:
        for url in (
            f"{base}/context/companysettings/metadata/customEntities/{entity_id}",
            f"{base}/entity/customentity/{entity_id}",
        ):
            r = requests.get(url, headers=headers, timeout=60)
            print(url, "HTTP", r.status_code)
            try:
                data = r.json()
            except Exception:
                print(r.text[:200])
                continue
            print("keys", list(data.keys()))
            print("rows", [(x.get("name"), x.get("id"), x.get("externalCode")) for x in (data.get("rows") or [])[:20]])
            print(json.dumps(data, ensure_ascii=False)[:500])


if __name__ == "__main__":
    main()
