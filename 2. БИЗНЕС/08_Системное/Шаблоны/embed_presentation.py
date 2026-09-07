#!/usr/bin/env python3
"""Сборка HTML-презентации в один файл для передачи клиенту.

Вшивает:
- PNG/JPEG из папки рядом с HTML (assets/ и корень)
- Inter + JetBrains Mono (кириллица) как data:font/woff2

После прогона файл открывается офлайн: логотипы, скриншоты и шрифты на месте.
Регламент: 08_Системное/Шаблоны/ПРЕЗЕНТАЦИЯ_ПРОДАЖИ_MS/README.md
"""
from __future__ import annotations

import base64
import re
import sys
import urllib.request
from pathlib import Path

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
FONT_CSS_URL = (
    "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900"
    "&family=JetBrains+Mono:wght@500;600&display=swap"
)
IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def _mime_img(path: Path) -> str:
    head = path.read_bytes()[:12]
    if head[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    ext = path.suffix.lower()
    return {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif"}.get(
        ext, "image/png"
    )


def _data_uri(path: Path) -> str:
    return f"data:{_mime_img(path)};base64,{base64.b64encode(path.read_bytes()).decode()}"


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def _embed_google_fonts(html: str) -> str:
    try:
        css = _fetch(FONT_CSS_URL).decode("utf-8")
    except Exception as e:
        print(f"  шрифты Google не скачались ({e}) — оставлю ссылку", file=sys.stderr)
        return html
    urls = sorted(set(re.findall(r"url\(([^)]+)\)", css)))
    for raw in urls:
        url = raw.strip("'\"")
        try:
            data = _fetch(url)
        except Exception as e:
            print(f"  пропуск шрифта {url[:60]} ({e})", file=sys.stderr)
            continue
        mime = "font/woff2" if "woff2" in url else "font/woff"
        css = css.replace(url, f"data:{mime};base64,{base64.b64encode(data).decode()}")
        print(f"  + font {len(data)//1024} KB")
    block = "<style>\n" + css + "\n</style>\n"
    html2, n = re.subn(
        r'(?:<link rel="preconnect" href="https://fonts\.[^"]+"[^>]*>\s*)+'
        r'<link href="https://fonts\.googleapis\.com/css2\?[^"]+" rel="stylesheet">\s*',
        block,
        html,
        count=1,
    )
    if n:
        return html2
    html2, n = re.subn(
        r'@import\s+url\s*\(\s*["\']?https://fonts\.googleapis\.com/css2[^"\')]+["\']?\s*\)\s*;',
        "",
        html,
        count=1,
        flags=re.IGNORECASE,
    )
    if n:
        html2 = html2.replace("<style>", "<style>\n" + css, 1)
        return html2
    print("  ссылка на Google Fonts не найдена — вставляю @font-face в head")
    return html.replace("</head>", block + "</head>", 1)


def _embed_images(html: str, html_path: Path) -> int:
    n = 0
    folders = [html_path.parent / "assets", html_path.parent]
    seen: set[str] = set()
    for folder in folders:
        if not folder.is_dir():
            continue
        for img in sorted(folder.iterdir()):
            if img.suffix.lower() not in IMG_EXT or not img.is_file():
                continue
            rels = [img.name]
            if folder.name == "assets":
                rels.append(f"assets/{img.name}")
            uri = _data_uri(img)
            for rel in rels:
                if rel in seen:
                    continue
                if rel not in html:
                    continue
                html_new = html.replace(f'src="{rel}"', f'src="{uri}"')
                if html_new != html:
                    html = html_new
                    seen.add(rel)
                    n += 1
                    print(f"  + {rel} ({img.stat().st_size // 1024} KB)")
    return n, html


def embed(html_path: Path) -> int:
    if not html_path.is_file():
        print(f"нет файла: {html_path}", file=sys.stderr)
        return 1
    html = html_path.read_text(encoding="utf-8")
    n, html = _embed_images(html, html_path)
    html = _embed_google_fonts(html)
    leftover = re.findall(r'src="(?!data:)([^"]+\.(?:png|jpe?g|webp|gif))"', html)
    if leftover:
        print("ещё внешние картинки:", leftover, file=sys.stderr)
    html_path.write_text(html, encoding="utf-8")
    print(f"OK: {html_path} ({html_path.stat().st_size // 1024} KB, картинок: {n})")
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: embed_presentation.py path/to/Предложение_Клиент.html", file=sys.stderr)
        return 2
    code = 0
    for a in sys.argv[1:]:
        if embed(Path(a)) != 0:
            code = 1
    return code


if __name__ == "__main__":
    raise SystemExit(main())
