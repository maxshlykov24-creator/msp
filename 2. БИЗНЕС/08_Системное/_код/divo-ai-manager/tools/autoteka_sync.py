#!/usr/bin/env python3
"""Отчёты автотеки -> кэш фактов по VIN.

Зачем: бот кидал клиенту ссылку на отчёт и при этом отвечал «лицензии такси
нет», хотя разрешение в отчёте есть. Ссылку видит клиент, содержимое - нет.
Теперь содержимое читает и бот.

Источник: тот же JSON, который тянет страница отчёта:
    https://api.autoteka.ru/v2/report/uuid/<uuid>.json?csAppCode=webDesktop
Ссылки на отчёты лежат в листах «Данные» и «Склад», колонка «Автотека».

Отсюда достаём только проверяемые факты: такси, каршеринг, лизинг, залог,
ограничения, розыск, ДТП, владельцы, регион первичной регистрации. Никаких
интерпретаций: чего в отчёте нет, того нет и в кэше.

Кэш: workspace/state/autoteka.json, VIN -> факты плюс отметка времени.
Запуск: tools/autoteka_sync.py [--quiet] [--force] [--vin VIN]
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.config import settings  # noqa: E402
from tools.stock_sync import (  # noqa: E402
    HEADER,
    read_cars,
    read_warehouse,
    sheets_api,
)

MSK = timezone(timedelta(hours=3))
API = "https://api.autoteka.ru/v2/report/uuid/%s.json?csAppCode=webDesktop"
UUID_RE = re.compile(r"uuid/([0-9a-f-]{36})", re.I)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": "https://autoteka.ru/",
}
TTL_DAYS = 7
CACHE = "autoteka.json"
PAUSE_SEC = 2.0  # 34 отчёта подряд без паузы упираются в 403
RETRY_SEC = 30.0

# «Не найдено» и «не проверено» в кэш не кладём: модель немедленно превращает
# это в «по базам чисто», а отсутствие записи в реестре ничего не доказывает.
EMPTY_MARKS = ("не найден", "не обнаруж", "не проверен", "нет сведени")

# id карточки в отчёте -> как это назвать в карточке машины
RISK_CARDS = {
    "pledge": "залог",
    "registrationRestrictions": "ограничения на регистрацию",
    "stealing": "розыск",
    "arbitrationCases": "арбитражные дела",
    "fines": "неоплаченные штрафы",
    "leasingContracts": "лизинг",
}


def cache_path() -> Path:
    return settings.state_dir / CACHE


def load_cache() -> dict[str, Any]:
    path = cache_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print("autoteka_sync: кэш не прочитан, начинаем заново: %s" % exc, file=sys.stderr)
        return {}
    return data if isinstance(data, dict) else {}


def save_cache(data: dict[str, Any]) -> None:
    path = cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def fetch(uuid: str) -> dict[str, Any] | None:
    """JSON отчёта. С VPS напрямую 403, поэтому идём тем же прокси, что LLM."""
    import httpx

    kwargs: dict[str, Any] = {"timeout": 30.0, "headers": HEADERS}
    if settings.llm_proxy:
        kwargs["proxy"] = settings.llm_proxy
    resp = None
    for attempt in (1, 2):
        try:
            with httpx.Client(**kwargs) as client:
                resp = client.get(API % uuid)
        except httpx.HTTPError as exc:
            print("autoteka_sync: %s не получен: %s" % (uuid, exc), file=sys.stderr)
            return None
        if resp.status_code != 429 and resp.status_code != 403:
            break
        if attempt == 1:
            time.sleep(RETRY_SEC)  # частые запросы ловят 403, помогает выждать
    if resp is None or resp.status_code != 200:
        print(
            "autoteka_sync: %s -> HTTP %s" % (uuid, resp.status_code if resp else "?"),
            file=sys.stderr,
        )
        return None
    try:
        data = resp.json()
    except ValueError:
        print("autoteka_sync: %s -> не JSON" % uuid, file=sys.stderr)
        return None
    return data if isinstance(data, dict) else None


def cards_of(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for block in report.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        for card in block.get("cards") or []:
            if isinstance(card, dict) and card.get("id"):
                out.setdefault(str(card["id"]), card)
    return out


def block_of(report: dict[str, Any], name: str) -> dict[str, Any]:
    for block in report.get("blocks") or []:
        if isinstance(block, dict) and block.get("name") == name:
            return block
    return {}


def _items(card: dict[str, Any]) -> list[dict[str, Any]]:
    """Плоский список пар ключ-значение из вложенных lists карточки."""
    out: list[dict[str, Any]] = []
    add = (card.get("additional") or {}) if isinstance(card.get("additional"), dict) else {}
    for lst in add.get("lists") or []:
        if not isinstance(lst, dict):
            continue
        for item in lst.get("items") or []:
            if not isinstance(item, dict):
                continue
            if item.get("title"):
                out.append({"key": "заголовок", "value": item["title"]})
            for pair in item.get("items") or []:
                if isinstance(pair, dict) and pair.get("key"):
                    out.append({"key": pair["key"], "value": pair.get("value")})
    return out


def taxi_of(cards: dict[str, Any]) -> str:
    """Только найденное. «Не найдено» в кэш не кладём.

    Отсутствие записи в реестре не значит, что машина не таксовала, а модель
    из «не найдено» немедленно делает «по базам чисто» - это ложь клиенту.
    """
    card = cards.get("taxiData")
    if not card or card.get("status") == "ok":
        return ""
    bits: list[str] = []
    for pair in _items(card):
        if pair["key"] in ("заголовок", "Регион выдачи", "Статус разрешения") and pair["value"]:
            bits.append(str(pair["value"]))
    tail = ", ".join(bits[:3])
    return "есть разрешение на работу в такси%s" % (" (%s)" % tail if tail else "")


def carsharing_of(cards: dict[str, Any]) -> str:
    """Каршеринг в отчёт почти никогда не попадает, поэтому «не найдено» молчит."""
    card = cards.get("carsharingData")
    if not card or card.get("status") == "ok":
        return ""
    return "по отчёту есть сведения об использовании в каршеринге"


def is_empty_phrase(text: str) -> bool:
    low = (text or "").lower()
    return any(mark in low for mark in EMPTY_MARKS)


def risks_of(cards: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for card_id, label in RISK_CARDS.items():
        card = cards.get(card_id)
        if not card or card.get("status") == "ok":
            continue
        title = str(card.get("title") or "").strip()
        if is_empty_phrase(title):
            continue  # «ограничения не проверены» это не риск и не гарантия
        out.append(title or label)
    return out


def damage_of(report: dict[str, Any]) -> str:
    block = block_of(report, "incidentEventsGroup")
    if not block:
        return ""
    title = str(block.get("title") or "").strip()
    sub = str(block.get("subTitle") or "").strip()
    return "; ".join(x for x in (title, sub) if x)


def owners_of(report: dict[str, Any]) -> str:
    block = block_of(report, "ownersHistory")
    if not block:
        return ""
    title = str(block.get("title") or "").strip()
    alerts = [
        str(a.get("text") or "").strip()
        for a in (block.get("alerts") or [])
        if isinstance(a, dict)
    ]
    return "; ".join(x for x in [title] + alerts if x)


def region_of(report: dict[str, Any]) -> str:
    """Регион первичной регистрации: клиенты спрашивают «где эксплуатировалась»."""
    block = block_of(report, "exploitationHistory")
    for card in block.get("cards") or []:
        add = (card.get("additional") or {}) if isinstance(card, dict) else {}
        for group in add.get("exploitationHistory") or []:
            for event in (group or {}).get("events") or []:
                label = str((event or {}).get("label") or "")
                loc = str((event or {}).get("location") or "").strip()
                if loc and "регистрац" in label.lower():
                    return loc
    return ""


def facts_of(report: dict[str, Any]) -> dict[str, Any]:
    cards = cards_of(report)
    head = report.get("head") or {}
    facts = {
        "такси": taxi_of(cards),
        "каршеринг": carsharing_of(cards),
        "риски": risks_of(cards),
        "повреждения": damage_of(report),
        "владельцы": owners_of(report),
        "регион регистрации": region_of(report),
        "vin отчёта": str(head.get("vin") or "").strip().upper(),
    }
    return {k: v for k, v in facts.items() if v}


def links_from_sheets() -> dict[str, str]:
    """VIN -> ссылка на отчёт из листов «Данные» и «Склад»."""
    svc = sheets_api()
    rows = read_cars(svc, settings.data_sheet) + read_warehouse(svc)
    out: dict[str, str] = {}
    for row in rows:
        d = dict(zip(HEADER, row))
        vin = (d["VIN"] or "").strip().upper()
        url = (d.get("Автотека") or "").strip()
        if vin and url.startswith("http"):
            out.setdefault(vin, url)
    return out


def fresh(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    stamp = entry.get("обновлено")
    if not stamp:
        return False
    try:
        when = datetime.fromisoformat(stamp)
    except ValueError:
        return False
    return datetime.now(MSK) - when < timedelta(days=TTL_DAYS)


def main() -> int:
    quiet = "--quiet" in sys.argv
    force = "--force" in sys.argv
    only = ""
    if "--vin" in sys.argv:
        idx = sys.argv.index("--vin")
        only = sys.argv[idx + 1].strip().upper() if idx + 1 < len(sys.argv) else ""

    try:
        links = links_from_sheets()
    except Exception as exc:  # noqa: BLE001 — автотека не должна ронять бота
        print("autoteka_sync: листы не прочитаны: %s" % exc, file=sys.stderr)
        return 1

    cache = load_cache()
    got = skipped = failed = 0
    for vin, url in sorted(links.items()):
        if only and vin != only:
            continue
        if not force and fresh(cache.get(vin)):
            skipped += 1
            continue
        m = UUID_RE.search(url)
        if not m:
            failed += 1
            continue
        report = fetch(m.group(1))
        if not report:
            failed += 1
            continue
        facts = facts_of(report)
        if not facts:
            failed += 1
            continue
        cache[vin] = {
            "обновлено": datetime.now(MSK).isoformat(timespec="seconds"),
            "факты": facts,
        }
        got += 1

    # машины уехали со склада - их отчёты в кэше только мешают
    for vin in list(cache):
        if vin not in links:
            cache.pop(vin, None)

    save_cache(cache)
    if not quiet:
        print(
            "autoteka_sync: %d обновлено, %d свежих, %d без отчёта, всего в кэше %d"
            % (got, skipped, failed, len(cache))
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
