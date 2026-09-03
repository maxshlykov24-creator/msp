#!/usr/bin/env python3
"""Регистрация нативного вебхука Kommo на hub.

Отдельная интеграция НЕ нужна: подписка создаётся тем же долгосрочным токеном
существующей частной интеграции (scope crm). Redirect URI нужен только для OAuth,
здесь он не участвует.

    KOMMO_TOKEN=... KOMMO_WEBHOOK_SECRET=... python3 scripts/register_kommo_webhook.py
    ... --list      только показать текущие подписки
    ... --delete    снять нашу подписку

Чужие вебхуки (Teletype, Wazzup) не трогаются.
"""
from __future__ import annotations

import argparse
import os
import sys

import httpx

BASE = os.environ.get("KOMMO_BASE", "https://licensebridgeusa.kommo.com/api/v4")
HOST = os.environ.get("HUB_PUBLIC_HOST", "https://72-56-123-137.sslip.io")
EVENTS = ["add_lead", "status_lead", "add_contact", "update_contact"]


def _client() -> httpx.Client:
    token = os.environ.get("KOMMO_TOKEN", "").strip()
    if not token:
        raise SystemExit("KOMMO_TOKEN required")
    return httpx.Client(base_url=BASE, timeout=30,
                        headers={"Authorization": f"Bearer {token}",
                                 "Content-Type": "application/json"})


def list_hooks(c: httpx.Client) -> list[dict]:
    r = c.get("/webhooks")
    if r.status_code == 204:
        return []
    r.raise_for_status()
    return r.json().get("_embedded", {}).get("webhooks", [])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--delete", action="store_true")
    args = ap.parse_args()

    secret = os.environ.get("KOMMO_WEBHOOK_SECRET", "").strip()
    destination = f"{HOST.rstrip('/')}/kommo/webhook/{secret}"

    with _client() as c:
        hooks = list_hooks(c)
        print("Текущие подписки:")
        for h in hooks:
            mark = "  ← наш" if "/kommo/webhook/" in h.get("destination", "") else ""
            print(f"  [{'off' if h.get('disabled') else 'on '}] {h.get('destination')}"
                  f"\n        events: {','.join(h.get('settings', []))}{mark}")
        if args.list:
            return 0
        if not secret:
            raise SystemExit("KOMMO_WEBHOOK_SECRET required")

        if args.delete:
            r = c.request("DELETE", "/webhooks", json={"destination": destination})
            print("delete:", r.status_code, r.text[:200])
            return 0 if r.status_code in (200, 202, 204) else 1

        r = c.post("/webhooks", json={"destination": destination, "settings": EVENTS})
        print("subscribe:", r.status_code, r.text[:300])
        if r.status_code not in (200, 201):
            return 1
        print(f"\nОК: Kommo будет слать {', '.join(EVENTS)} на {HOST}/kommo/webhook/***")
        return 0


if __name__ == "__main__":
    sys.exit(main())
