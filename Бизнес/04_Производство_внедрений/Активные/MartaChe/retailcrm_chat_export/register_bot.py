#!/usr/bin/env python3
"""
Регистрирует (или переиспользует) служебный bot-модуль MG в аккаунте RetailCRM,
чтобы получить токен MG Bot API для чтения истории чатов.

Метод: POST {RETAILCRM_API_URL}/api/v5/integration-modules/{code}/edit
Документация: https://help.retailcrm.pro/Developers/mgBot

Нужен обычный API-ключ v5 (Настройки -> Администрирование -> Пользователи -> API-ключи)
с правом на интеграционные модули.

После успешной регистрации токен и endpoint дописываются в .env.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv, set_key

ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(ENV_PATH)


def _require(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        raise SystemExit(f"Заполните {name} в .env (см. .env.example)")
    return val


def main() -> None:
    api_url = _require("RETAILCRM_API_URL").rstrip("/")
    api_key = _require("RETAILCRM_API_KEY")
    code = os.environ.get("BOT_MODULE_CODE", "martache-chat-export")
    name = os.environ.get("BOT_MODULE_NAME", "MartaChe Chat Export")
    client_id = os.environ.get("BOT_CLIENT_ID", "martache-export-001")
    base_url = os.environ.get("BOT_BASE_URL", "https://export.martache.local")

    integration_module = {
        "code": code,
        "integrationCode": code,
        "active": True,
        "name": name,
        "clientId": client_id,
        "baseUrl": base_url,
        "integrations": {"mgBot": {}},
    }

    url = f"{api_url}/api/v5/integration-modules/{code}/edit"
    resp = requests.post(
        url,
        data={
            "apiKey": api_key,
            "integrationModule": json.dumps(integration_module, ensure_ascii=False),
        },
        timeout=30,
    )

    if resp.status_code != 200:
        print(f"Ошибка регистрации: HTTP {resp.status_code}", file=sys.stderr)
        print(resp.text, file=sys.stderr)
        raise SystemExit(1)

    payload = resp.json()
    if not payload.get("success"):
        print("RetailCRM вернул success=false:", file=sys.stderr)
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
        raise SystemExit(1)

    mg_bot = payload.get("info", {}).get("mgBot", {})
    token = mg_bot.get("token")
    endpoint = mg_bot.get("endpointUrl")

    if not token or not endpoint:
        print("В ответе нет token/endpointUrl — проверьте вручную:", file=sys.stderr)
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
        raise SystemExit(1)

    set_key(str(ENV_PATH), "RETAILCRM_MG_BOT_TOKEN", token)
    set_key(str(ENV_PATH), "RETAILCRM_MG_BOT_ENDPOINT", endpoint)

    print("Готово. Bot-модуль зарегистрирован:")
    print(f"  code:     {code}")
    print(f"  endpoint: {endpoint}")
    print(f"  token:    {token[:8]}...{token[-4:]}")
    print()
    print("Токен и endpoint сохранены в .env. Теперь проверьте в RetailCRM:")
    print("  Настройки -> Интеграции / Чат-центр -> Боты")
    print("  — что бот появился и (если потребуется) включён / имеет доступ к чатам.")
    print()
    print("Далее запустите: python export_chats.py")


if __name__ == "__main__":
    main()
