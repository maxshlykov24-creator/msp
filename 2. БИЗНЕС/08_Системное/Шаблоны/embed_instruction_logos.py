#!/usr/bin/env python3
"""Сборка HTML-инструкции в один файл для передачи клиенту (Telegram, почта).

- PNG/JPEG из instruction/assets/ → data URI
- без @import / внешних URL (офлайн на телефоне)
- для MS: logo-ms-transparent.png (без чёрного фона); logo-ms.png — fallback
- client: logo-*-transparent.png или logo-client.png (class="client")
- Регламент шапки: 08_Системное/Шаблоны/ШАБЛОН_NAV_BRAND.html
"""
from __future__ import annotations

import base64
import re
import sys
from pathlib import Path

SYSTEM_FONT = '-apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif'
MS_SRC_ORDER = ("logo-ms-transparent.png", "logo-ms.png")


def _mime(path: Path) -> str:
    head = path.read_bytes()[:12]
    if head[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if path.suffix.lower() in {".jpg", ".jpeg"}:
        return "image/jpeg"
    return "image/png"


def _data_uri(path: Path) -> str:
    return f"data:{_mime(path)};base64," + base64.b64encode(path.read_bytes()).decode()


def _strip_external_fonts(html: str) -> str:
    html = re.sub(
        r'@import\s+url\s*\(\s*["\']?https?://[^"\')]+["\']?\s*\)\s*;?\s*\n?',
        "",
        html,
        flags=re.IGNORECASE,
    )
    html = re.sub(
        r'font-family:\s*Inter\s*,[^;]+;',
        f"font-family: {SYSTEM_FONT};",
        html,
        count=1,
    )
    return html


def _replace_img_src(html: str, class_name: str, uri: str) -> tuple[str, bool]:
    pattern = rf'(<img\s+class="{class_name}"\s+src=")([^"]*)(")'
    if not re.search(pattern, html):
        return html, False
    html = re.sub(pattern, rf"\1{uri}\3", html, count=1)
    return html, True


def embed(html_path: Path, *, telegram: bool = True) -> int:
    if not html_path.is_file():
        print(f"нет файла: {html_path}", file=sys.stderr)
        return 1

    assets = html_path.parent / "assets"
    html = html_path.read_text(encoding="utf-8")
    n = 0

    if assets.is_dir():
        for img_path in sorted(assets.glob("*")):
            if img_path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
                continue
            rel = f"assets/{img_path.name}"
            if rel not in html:
                continue
            uri = _data_uri(img_path)
            html = html.replace(f'src="{rel}"', f'src="{uri}"')
            n += 1
            print(f"  + {img_path.name} ({img_path.stat().st_size // 1024} KB)")

    if telegram:
        html = _strip_external_fonts(html)
        if assets.is_dir():
            for name in MS_SRC_ORDER:
                ms_path = assets / name
                if ms_path.is_file():
                    html, ok = _replace_img_src(html, "ms", _data_uri(ms_path))
                    if ok:
                        print(f"  ms footer ← {name} ({ms_path.stat().st_size // 1024} KB)")
                    break
            for name in ("logo-keris.png", "logo.png", "logo-client.png"):
                keris_path = assets / name
                if keris_path.is_file():
                    html, ok = _replace_img_src(html, "keris", _data_uri(keris_path))
                    if ok:
                        print(f"  keris header ← {name} ({keris_path.stat().st_size // 1024} KB)")
                    break

    if n == 0 and "data:image/" not in html:
        print('не найдено src="assets/…" — положите PNG в assets/ или файл уже собран', file=sys.stderr)
        return 1

    html_path.write_text(html, encoding="utf-8")
    size_kb = html_path.stat().st_size // 1024
    print(f"OK: {html_path} ({size_kb} KB, logos embedded: {n})")
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        print(
            "Usage: embed_instruction_logos.py path/to/инструкция/*.html [--no-telegram]",
            file=sys.stderr,
        )
        return 2
    telegram = "--no-telegram" not in sys.argv
    paths = [Path(a) for a in sys.argv[1:] if not a.startswith("--")]
    code = 0
    for path in paths:
        if embed(path, telegram=telegram) != 0:
            code = 1
    return code


if __name__ == "__main__":
    raise SystemExit(main())
