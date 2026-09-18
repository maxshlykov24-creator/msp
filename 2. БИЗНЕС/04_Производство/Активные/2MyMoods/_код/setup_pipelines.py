#!/usr/bin/env python3
"""2MY — новые воронки рядом с рабочими. Идемпотентно. Живые сделки не трогает."""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / "06_Доступы" / ".env"

C_GREY = "#e6e8ea"
C_BLUE = "#c1e0ff"
C_ORANGE = "#ffce5a"
C_YELLOW = "#fffd7f"
C_MINT = "#87f2c0"
C_ROSE = "#ffdbdb"
C_PINK = "#ffc8c8"

SALES_NAME = "Продажи 2MY"
SALES_STAGES = [
    ("Новая заявка", 10, C_GREY),
    ("Взята в работу", 20, C_BLUE),
    ("Лист ожидания", 30, C_ORANGE),
    ("Запланирован визит", 40, C_YELLOW),
    ("Ждёт оплаты", 50, C_PINK),
    ("Оплачен", 60, C_MINT),
    ("В производстве", 70, C_ROSE),
    ("На сборке", 80, C_BLUE),
    ("Отправлен", 90, C_ORANGE),
]

MARKETING_NAME = "Маркетинг 2MY"
MARKETING_STAGES = [
    ("Переговоры", 10, C_BLUE),
    ("СПАМ, не требует ответа", 20, C_GREY),
    ("Сотрудничество не интересно", 30, C_ROSE),
    ("В работе", 40, C_MINT),
]


def load_env() -> dict[str, str]:
    out: dict[str, str] = {}
    if ENV.is_file():
        for raw in ENV.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip("'").strip('"')
    return out


OPENER = urllib.request.build_opener()


def req(method: str, path: str, body=None):
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, headers=HEADERS, method=method)
    try:
        with OPENER.open(r, timeout=30) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"error": raw.decode(errors="replace")}


def ok(label: str, status: int, body=None) -> bool:
    if 200 <= status < 300:
        print(f"  + {label} [{status}]")
        return True
    detail = json.dumps(body, ensure_ascii=False)[:500] if body else ""
    print(f"  ! {label} [{status}]: {detail}")
    return False


def list_pipelines() -> list[dict]:
    s, b = req("GET", "/api/v4/leads/pipelines")
    if not (200 <= s < 300):
        sys.exit(f"не прочитались воронки [{s}]: {b}")
    return (b.get("_embedded") or {}).get("pipelines") or []


def find_pipeline(name: str) -> dict | None:
    for p in list_pipelines():
        if (p.get("name") or "").strip() == name:
            return p
    return None


def create_pipeline(name: str, sort: int, stages: list[tuple[str, int, str]]) -> dict:
    print(f"\n-- Создаём «{name}» --")
    s, b = req("POST", "/api/v4/leads/pipelines", [{
        "name": name,
        "sort": sort,
        "is_main": False,
        "is_unsorted_on": False,
        "_embedded": {"statuses": [
            {"name": n, "sort": srt, "color": c} for n, srt, c in stages
        ]},
    }])
    if not ok(name, s, b):
        sys.exit("воронка не создана")
    return ((b.get("_embedded") or {}).get("pipelines") or [{}])[0]


def ensure_stages(pipeline: dict, stages: list[tuple[str, int, str]]) -> dict[str, int]:
    pid = pipeline["id"]
    existing = {
        (st.get("name") or "").strip().lower(): st
        for st in (pipeline.get("_embedded") or {}).get("statuses") or []
    }
    missing = [(n, srt, c) for n, srt, c in stages if n.lower() not in existing]
    if missing:
        print(f"\n-- Этапы в «{pipeline.get('name')}» ({len(missing)}) --")
        s, b = req(
            "POST",
            f"/api/v4/leads/pipelines/{pid}/statuses",
            [{"name": n, "sort": srt, "color": c} for n, srt, c in missing],
        )
        ok(", ".join(n for n, _, _ in missing), s, b)
        time.sleep(0.4)
    s, b = req("GET", f"/api/v4/leads/pipelines/{pid}")
    if not (200 <= s < 300):
        sys.exit(f"не перечиталась воронка [{s}]: {b}")
    result: dict[str, int] = {}
    for st in (b.get("_embedded") or {}).get("statuses") or []:
        result[(st.get("name") or "").strip()] = st["id"]
    return result


def dump_all() -> None:
    print("\n=== Сейчас в кабинете ===")
    for p in list_pipelines():
        main = " MAIN" if p.get("is_main") else ""
        print(f"\n{p['name']}  id={p['id']}{main}")
        statuses = p.get("_embedded", {}).get("statuses") or []
        statuses.sort(key=lambda x: x.get("sort", 0))
        for st in statuses:
            print(f"  {st.get('sort', 0):>4}  {st['name']}  id={st['id']}")


def dump_entity(label: str, path: str, embed_key: str, fmt) -> None:
    s, b = req("GET", path)
    print(f"\n=== {label} [{s}] ===")
    if not (200 <= s < 300):
        print(f"  {json.dumps(b, ensure_ascii=False)[:400]}")
        return
    items = (b.get("_embedded") or {}).get(embed_key) or []
    if not items:
        print("  (пусто)")
        return
    for it in items:
        print("  " + fmt(it))


def dump_context() -> None:
    dump_entity(
        "Пользователи",
        "/api/v4/users?limit=250",
        "users",
        lambda u: f"{u.get('name')}  id={u.get('id')}  email={u.get('email')}  rights={u.get('rights', {}).get('is_admin')}",
    )
    dump_entity(
        "Причины отказа",
        "/api/v4/leads/loss_reasons",
        "loss_reasons",
        lambda r: f"{r.get('name')}  id={r.get('id')}",
    )
    dump_entity(
        "Поля сделок",
        "/api/v4/leads/custom_fields?limit=250",
        "custom_fields",
        lambda f: f"{f.get('name')}  id={f.get('id')}  type={f.get('type')}  code={f.get('code')}",
    )
    dump_entity(
        "Виджеты",
        "/api/v4/widgets?limit=250",
        "widgets",
        lambda w: f"{w.get('name') or w.get('code')}  code={w.get('code')}  installed={w.get('is_installed')}",
    )


def print_stages(title: str, stages: dict[str, int]) -> None:
    print(f"\n{title}")
    for name, sid in stages.items():
        print(f"  {name:36} {sid}")


def main() -> None:
    env = load_env()
    token = (os.environ.get("AMOCRM_TOKEN") or env.get("AMOCRM_LONG_LIVED_TOKEN") or "").strip()
    base = (os.environ.get("AMOCRM_BASE_URL") or env.get("AMOCRM_BASE_URL") or "").rstrip("/")
    if not token or not base:
        sys.exit(f"Нет токена или URL. Положи .env в {ENV}")

    global BASE, HEADERS, OPENER
    BASE = base
    HEADERS = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    proxy = (os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
             or env.get("AMOCRM_HTTPS_PROXY") or env.get("HTTPS_PROXY") or "").strip()
    if proxy:
        OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({
            "http": proxy,
            "https": proxy,
        }))
        print(f"Прокси: {proxy}")

    s, b = req("GET", "/api/v4/account")
    if not (200 <= s < 300):
        sys.exit(f"amo недоступен [{s}]: {json.dumps(b, ensure_ascii=False)[:400]}")
    print(f"Аккаунт: {b.get('name')} id={b.get('id')}  {BASE}")

    dump_all()
    dump_context()

    sales = find_pipeline(SALES_NAME)
    if sales is None:
        sales = create_pipeline(SALES_NAME, 80, SALES_STAGES)
    else:
        print(f"\n= «{SALES_NAME}» уже есть id={sales['id']}")
    sales_ids = ensure_stages(sales, SALES_STAGES)

    mkt = find_pipeline(MARKETING_NAME)
    if mkt is None:
        mkt = create_pipeline(MARKETING_NAME, 90, MARKETING_STAGES)
    else:
        print(f"\n= «{MARKETING_NAME}» уже есть id={mkt['id']}")
    mkt_ids = ensure_stages(mkt, MARKETING_STAGES)

    print("\n=== Новые воронки (старые не трогали) ===")
    print(f"Продажи 2MY id={sales['id']}")
    print_stages("Этапы продаж:", sales_ids)
    print(f"Маркетинг 2MY id={mkt['id']}")
    print_stages("Этапы маркетинга:", mkt_ids)
    print("\nУспех (142) и провал (143) API не переименовывает.")
    print("Продажи: 142 руками в «Получен». Маркетинг: 142 можно оставить как есть.")


if __name__ == "__main__":
    main()
