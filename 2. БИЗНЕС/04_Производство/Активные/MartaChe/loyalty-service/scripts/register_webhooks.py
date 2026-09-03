#!/usr/bin/env python3
"""
Регистрация вебхуков в МойСклад (JSON API 1.2).

  cd loyalty-service
  export WEBHOOK_PUBLIC_URL="https://your-domain.com/webhook/moysklad"
  python scripts/register_webhooks.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings
from app.moysklad_client import MoySkladClient

DEFAULT_PATH = "/webhook/moysklad"


def _create(client: MoySkladClient, url: str, entity: str, action: str) -> None:
    body = {
        "url": url,
        "entityType": entity,
        "method": "POST",
        "enabled": True,
        "action": action,
    }
    try:
        r = client.post("/entity/webhook", body, disable_webhook=True)
        print(f"OK {entity} {action} id={r.get('id')}")
    except Exception as e:
        print(f"SKIP или ошибка {entity} {action}: {e}", file=sys.stderr)


def main() -> None:
    s = get_settings()
    base = (os.environ.get("WEBHOOK_PUBLIC_URL") or s.webhook_public_url or "").strip().rstrip("/")
    if not base:
        print("Нужен WEBHOOK_PUBLIC_URL в .env или в окружении", file=sys.stderr)
        sys.exit(1)
    if "/webhook" not in base:
        base = base + DEFAULT_PATH
    c = MoySkladClient()
    subscriptions = (
        ("customerorder", "CREATE"),
        ("customerorder", "UPDATE"),
        ("customerorder", "DELETE"),
        ("counterparty", "UPDATE"),
        ("bonustransaction", "CREATE"),
    )
    for entity, action in subscriptions:
        _create(c, base, entity, action)
    print("Готово. Проверьте: GET /entity/webhook")


if __name__ == "__main__":
    main()
