#!/usr/bin/env python3
"""Собирает юрдокументы из ЮРДОКУМЕНТЫ.md в два места сразу.

1. `legal/*.html` — отдельные страницы для сайта (ссылки в футере Tilda).
2. Блок `const LEGAL` в prototype.html — модальные окна внутри мини-аппа
   (открываются поверх, не выкидывая клиента из Telegram).

Текст правится только в ЮРДОКУМЕНТЫ.md, дальше — эта сборка.

Запуск:  python3 build_legal.py [--check]
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = (
    HERE.parents[2]
    / "04_Производство"
    / "Активные"
    / "Keris_Club"
    / "01_Договорённости"
    / "ЮРДОКУМЕНТЫ.md"
)
PROTOTYPE = HERE / "prototype.html"
LEGAL_DIR = HERE / "legal"

LEGAL_START = "  const LEGAL="
LEGAL_END = "  function openLegal("

# номер документа в источнике -> (ключ в коде, имя файла, короткий заголовок для ссылок)
DOCS = {
    1: ("privacy", "privacy.html", "Политика конфиденциальности"),
    2: ("consent", "consent.html", "Согласие на обработку ПДн"),
    3: ("offer", "offer.html", "Публичная оферта"),
}

PAGE_TEMPLATE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="index,follow">
<title>{title} — Keris Club</title>
<style>
  :root{{--bg:#fafaf2;--text:#2a1f1b;--muted:#7c6f68;--line:#e7e0d5;--rose:#c9808f}}
  *{{box-sizing:border-box}}
  body{{margin:0;background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;line-height:1.6}}
  .wrap{{max-width:760px;margin:0 auto;padding:32px 20px 64px}}
  header{{border-bottom:1px solid var(--line);padding-bottom:18px;margin-bottom:24px}}
  .brand{{font-weight:700;letter-spacing:.14em;text-transform:uppercase;font-size:12px;color:var(--muted)}}
  h1{{font-size:24px;margin:10px 0 0}}
  main{{white-space:pre-wrap;font-size:15px}}
  nav{{margin-top:36px;padding-top:18px;border-top:1px solid var(--line);font-size:14px}}
  nav a{{color:var(--rose);margin-right:16px}}
  footer{{margin-top:28px;font-size:12px;color:var(--muted)}}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="brand">Keris Club</div>
    <h1>{title}</h1>
  </header>
  <main>{body}</main>
  <nav>{links}</nav>
  <footer>
    ИП Рыба Карина Артуровна · ИНН 775149185013 · ОГРНИП 323774600225083<br>
    143582, Московская область, г. Истра, деревня Красный Посёлок, Английский б-р, д. 497<br>
    <a href="tel:+79936189871">+7 993 618 98 71</a> · <a href="mailto:hello@keris-club.ru">hello@keris-club.ru</a>
  </footer>
</div>
</body>
</html>
"""


def parse_docs() -> dict[str, dict[str, str]]:
    text = SOURCE.read_text(encoding="utf-8")
    parts = re.split(r"^## Документ (\d+) — (.+)$", text, flags=re.MULTILINE)
    # parts: [преамбула, номер, заголовок, тело, номер, заголовок, тело, ...]
    docs: dict[str, dict[str, str]] = {}
    for i in range(1, len(parts) - 2, 3):
        number = int(parts[i])
        title = parts[i + 1].strip()
        body = parts[i + 2].strip().strip("-").strip()
        if number not in DOCS:
            raise SystemExit(f"Неизвестный номер документа: {number}")
        key, filename, short = DOCS[number]
        docs[key] = {"title": title, "body": body, "file": filename, "short": short}
    missing = set(k for k, _, _ in DOCS.values()) - set(docs)
    if missing:
        raise SystemExit(f"В источнике не найдены документы: {sorted(missing)}")
    return docs


def build_pages(docs: dict[str, dict[str, str]]) -> dict[Path, str]:
    out: dict[Path, str] = {}
    for key, doc in docs.items():
        links = " ".join(
            f'<a href="{other["file"]}">{other["short"]}</a>'
            for other_key, other in docs.items() if other_key != key
        )
        out[LEGAL_DIR / doc["file"]] = PAGE_TEMPLATE.format(
            title=html.escape(doc["title"]),
            body=html.escape(doc["body"]),
            links=links,
        )
    return out


def build_js(docs: dict[str, dict[str, str]]) -> str:
    payload = {key: {"title": doc["title"], "body": doc["body"]} for key, doc in docs.items()}
    return "  const LEGAL=" + json.dumps(payload, ensure_ascii=False, indent=2).replace("\n", "\n  ") + ";\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    docs = parse_docs()
    pages = build_pages(docs)
    js_block = build_js(docs)

    html_text = PROTOTYPE.read_text(encoding="utf-8")
    if LEGAL_START not in html_text:
        raise SystemExit("В prototype.html нет блока `const LEGAL=` — вставьте заготовку перед openLegal()")
    start = html_text.index(LEGAL_START)
    end = html_text.index(LEGAL_END, start)
    updated_html = html_text[:start] + js_block + html_text[end:]

    stale = [p.name for p, content in pages.items()
             if not p.exists() or p.read_text(encoding="utf-8") != content]
    if updated_html != html_text:
        stale.append("prototype.html")

    if args.check:
        if stale:
            print("требуют пересборки:", ", ".join(stale))
            return 1
        print("юрдокументы актуальны")
        return 0

    LEGAL_DIR.mkdir(exist_ok=True)
    for path, content in pages.items():
        path.write_text(content, encoding="utf-8")
    PROTOTYPE.write_text(updated_html, encoding="utf-8")
    print("собрано:", ", ".join(sorted(p.name for p in pages)), "+ prototype.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
