#!/usr/bin/env python3
"""Дискавери ID для автоматизации «Требует касания».

Печатает всё, что нужно подставить в .env воркера повторного касания:
- pipeline_id воронок «Продажи» и «Повторные продажи»;
- status_id «Успешно реализовано» и нового этапа «Требует касания»;
- field_id контактных полей «Действующий» и «Подписан на бот» (TG/Max);
- user_id сотрудников (чтобы найти служебных, напр. «Максим Дейкало»).

Запуск внутри контейнера api (там есть AMO_* в окружении):

  cat scripts/discover_repeat_ids.py | \
    ssh -i ~/.ssh/dkacademy_marta_ed25519 root@213.176.65.241 \
    'docker compose -f /root/dkacademy-bot/docker-compose.yml exec -T api python3 -'
"""
from __future__ import annotations

import os
import sys
from typing import Any

import httpx

AMO_SUBDOMAIN = os.environ.get("AMO_SUBDOMAIN", "").strip()
AMO_BASE_DOMAIN = os.environ.get("AMO_BASE_DOMAIN", "amocrm.ru").strip()
AMO_TOKEN = os.environ.get("AMO_LONG_LIVED_TOKEN", "").strip()

# Подстроки для подсветки искомых сущностей (регистр игнорируется).
SALES_PIPELINE = "продажи"
REPEAT_PIPELINE = "повторные продажи"
WON_STATUS = "успешно реализовано"
TOUCH_STATUS = "требует касания"
ACTIVE_FIELD = "действующий"
SUBSCRIBED_FIELD = "подписан на бот"


def _base() -> str:
    return f"https://{AMO_SUBDOMAIN}.{AMO_BASE_DOMAIN}"


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {AMO_TOKEN}",
        "Accept": "application/hal+json",
    }


def _get(client: httpx.Client, path: str, params: list[tuple[str, Any]] | None = None) -> dict:
    r = client.get(f"{_base()}{path}", params=params or [], headers=_headers(), timeout=30)
    if r.status_code >= 400:
        print(f"  [ERR] GET {path} -> {r.status_code}: {r.text[:300]}", file=sys.stderr)
        return {}
    return r.json() or {}


def _hit(name: str, needle: str) -> str:
    return "  <<< " + needle.upper() if needle in (name or "").lower() else ""


def main() -> None:
    if not AMO_SUBDOMAIN or not AMO_TOKEN:
        sys.exit("AMO_SUBDOMAIN / AMO_LONG_LIVED_TOKEN не заданы — запускайте внутри контейнера api")

    with httpx.Client() as client:
        print("=" * 70)
        print("ВОРОНКИ И ЭТАПЫ")
        print("=" * 70)
        data = _get(client, "/api/v4/leads/pipelines", [("limit", "250")])
        for p in (data.get("_embedded") or {}).get("pipelines") or []:
            pname = (p.get("name") or "").strip()
            mark = _hit(pname, SALES_PIPELINE) or _hit(pname, REPEAT_PIPELINE)
            print(f"\nВоронка [{p.get('id')}] «{pname}»{mark}")
            for st in (p.get("_embedded") or {}).get("statuses") or []:
                sname = (st.get("name") or "").strip()
                smark = _hit(sname, WON_STATUS) or _hit(sname, TOUCH_STATUS)
                print(f"    этап [{st.get('id')}] «{sname}»{smark}")

        print("\n" + "=" * 70)
        print("КАСТОМНЫЕ ПОЛЯ КОНТАКТА")
        print("=" * 70)
        cf = _get(client, "/api/v4/contacts/custom_fields", [("limit", "250")])
        for f in (cf.get("_embedded") or {}).get("custom_fields") or []:
            fname = (f.get("name") or "").strip()
            mark = _hit(fname, ACTIVE_FIELD) or _hit(fname, SUBSCRIBED_FIELD)
            print(f"  поле [{f.get('id')}] «{fname}» ({f.get('type')}){mark}")

        print("\n" + "=" * 70)
        print("ПОЛЬЗОВАТЕЛИ (для EXCLUDED_USER_IDS аналитики)")
        print("=" * 70)
        us = _get(client, "/api/v4/users", [("limit", "250")])
        for u in (us.get("_embedded") or {}).get("users") or []:
            print(f"  user [{u.get('id')}] «{(u.get('name') or '').strip()}»")

    print("\nГотово. Подставьте найденные id в .env (см. README, раздел «Повторное касание»).")


if __name__ == "__main__":
    main()
