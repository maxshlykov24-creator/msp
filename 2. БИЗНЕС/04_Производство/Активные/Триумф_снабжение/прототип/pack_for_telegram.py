#!/usr/bin/env python3
"""Сборка самодостаточных HTML прототипа для передачи в Telegram.

Источник остаётся с assets/ и Google Fonts.
В папку для_Андрея/ пишутся копии: логотип и шрифты зашиты (data URI),
интернет не нужен, вид как у вас на машине.
"""
from __future__ import annotations

import base64
import io
import re
import sys
import urllib.request
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    Image = None  # type: ignore

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "для_Андрея"
LOGO = ROOT / "assets" / "logo-ms-transparent.png"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

FILES = [
    (
        "Триумф_система_прототип.html",
        "Триумф_система_прототип.html",
        "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@500;600;700&display=swap",
    ),
    (
        "Триумф_система_снабжения_прототип.html",
        "Триумф_снабжение_логика.html",
        "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@500;600&display=swap",
    ),
]

README = """Как открыть
===========

1. Скачайте файл на компьютер (не открывайте превью в Telegram).
2. Двойной клик → откроется в браузере (Chrome / Safari / Edge / Яндекс).
3. Интернет не нужен: логотип и шрифты уже внутри файла.

Два файла
---------
• Триумф_снабжение_логика.html — проход по логике за 8 шагов (сначала).
• Триумф_система_прототип.html — рабочее окно системы (потом).

Можно кликать всё: фильтры, роли, подпись, сравнение, деньги.
"""


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def logo_data_uri() -> str:
    if not LOGO.is_file():
        raise SystemExit(f"нет логотипа: {LOGO}")
    raw = LOGO.read_bytes()
    # Для экрана логотип ~26px; 3× retina хватает ~156px по высоте — файл легче.
    if Image is not None:
        im = Image.open(io.BytesIO(raw)).convert("RGBA")
        h = 156
        w = max(1, int(im.width * h / im.height))
        im = im.resize((w, h), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="PNG", optimize=True)
        raw = buf.getvalue()
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:image/png;base64,{b64}"


def fonts_css(css_url: str) -> str:
    css = _fetch(css_url).decode("utf-8")
    urls = sorted(set(re.findall(r"url\((https://fonts\.gstatic\.com/[^)]+)\)", css)))
    if not urls:
        raise SystemExit(f"не нашли woff2 в CSS: {css_url}")
    for u in urls:
        font = _fetch(u)
        uri = "data:font/woff2;base64," + base64.b64encode(font).decode("ascii")
        css = css.replace(u, uri)
        print(f"    font {u.rsplit('/', 1)[-1][:40]}… {len(font) // 1024} KB")
    return css


def pack_one(src_name: str, dst_name: str, css_url: str, logo_uri: str) -> Path:
    src = ROOT / src_name
    if not src.is_file():
        raise SystemExit(f"нет файла: {src}")
    html = src.read_text(encoding="utf-8")

    html = re.sub(
        r'<link rel="preconnect" href="https://fonts\.googleapis\.com">\s*',
        "",
        html,
    )
    html = re.sub(
        r'<link rel="preconnect" href="https://fonts\.gstatic\.com"[^>]*>\s*',
        "",
        html,
    )
    html = re.sub(
        r'<link href="https://fonts\.googleapis\.com/css2\?[^"]+" rel="stylesheet">\s*',
        "",
        html,
        count=1,
    )

    css = fonts_css(css_url)
    html = html.replace("<style>", "<style>\n/* вшитые шрифты (офлайн) */\n" + css + "\n", 1)

    html = html.replace('src="assets/logo-ms-transparent.png"', f'src="{logo_uri}"')
    if "assets/logo-ms-transparent.png" in html:
        raise SystemExit(f"остались внешние пути к логотипу в {src_name}")
    if "fonts.googleapis.com" in html or "fonts.gstatic.com" in html:
        raise SystemExit(f"остались внешние шрифты в {src_name}")

    OUT.mkdir(parents=True, exist_ok=True)
    dst = OUT / dst_name
    dst.write_text(html, encoding="utf-8")
    print(f"OK {dst.name} · {dst.stat().st_size // 1024} KB")
    return dst


def main() -> int:
    print("Логотип…")
    logo_uri = logo_data_uri()
    print(f"  logo data-uri ~{len(logo_uri) // 1024} KB")
    for src, dst, css in FILES:
        print(f"Сборка {dst}…")
        pack_one(src, dst, css, logo_uri)
    (OUT / "КАК_ОТКРЫТЬ.txt").write_text(README, encoding="utf-8")
    print(f"\nГотово: {OUT}")
    print("В Telegram отправляйте как файл (документ), не как сжатое фото.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
