#!/usr/bin/env python3
"""Rebuild LicenseBridge_дашборд_прототип.html (self-contained) from .src.html + data.js + logos."""
from __future__ import annotations

import base64
import re
import sys
from pathlib import Path

DIR = Path(__file__).resolve().parent
SRC = DIR / "LicenseBridge_дашборд_прототип.src.html"
OUT = DIR / "LicenseBridge_дашборд_прототип.html"
DATA = DIR / "data.js"
ASSETS = DIR / "assets"


def data_uri(path: Path, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def pick_lb_logo() -> tuple[Path, str]:
    for name, mime in (
        ("logo-licensebridge.png", "image/png"),
        ("logo-licensebridge-transparent.png", "image/png"),
        ("logo-licensebridge.svg", "image/svg+xml"),
    ):
        p = ASSETS / name
        if p.exists():
            return p, mime
    raise SystemExit("No LicenseBridge logo in assets/")


def inline_data(html: str, data_js: str) -> str:
    html2, n = re.subn(
        r'<script\s+src=["\']data\.js["\']\s*>\s*</script>',
        "<script>\n" + data_js + "\n</script>",
        html,
        count=1,
    )
    if n == 1:
        return html2
    html2, n = re.subn(
        r'<script\s+src=["\']data\.js["\']\s*>\s*',
        "<script>\n" + data_js + "\n",
        html,
        count=1,
    )
    if n == 1:
        return html2
    # Already self-contained: replace window.LB_DATA = {...};
    html2, n = re.subn(
        r"<script>\s*window\.LB_DATA\s*=\s*\{.*?\};\s*</script>",
        "<script>\n" + data_js.rstrip() + "\n</script>",
        html,
        count=1,
        flags=re.S,
    )
    if n == 1:
        return html2
    raise SystemExit(f"Failed to inline/replace data.js (matches={n})")


def embed_logos(html: str) -> str:
    lb_path, lb_mime = pick_lb_logo()
    lb_uri = data_uri(lb_path, lb_mime)
    ms = ASSETS / "logo-ms-transparent.png"
    if not ms.exists():
        raise SystemExit(f"Missing MS logo: {ms}")
    ms_uri = data_uri(ms, "image/png")

    html2, n_lb = re.subn(
        r'(src=["\'])assets/logo-licensebridge\.[a-zA-Z0-9]+(["\'])',
        r"\1" + lb_uri + r"\2",
        html,
    )
    if n_lb < 1:
        # already embedded
        if "data:image" not in html:
            raise SystemExit("Failed to embed LicenseBridge logo")
        html2 = html
    html2, n_ms = re.subn(
        r'(src=["\'])assets/logo-ms-transparent\.png(["\'])',
        r"\1" + ms_uri + r"\2",
        html2,
    )
    if n_ms < 1 and "data:image" not in html2:
        raise SystemExit("Failed to embed MS logo")
    return html2


def build() -> Path:
    src = SRC if SRC.exists() else OUT
    if not src.exists():
        raise SystemExit(f"Missing SRC: {src}")
    if not DATA.exists():
        raise SystemExit(f"Missing DATA: {DATA}")

    data_js = DATA.read_text(encoding="utf-8")
    if not data_js.strip().startswith("window.LB_DATA"):
        data_js = "window.LB_DATA = " + data_js.strip().rstrip(";") + ";\n"
    html = src.read_text(encoding="utf-8")
    html2 = inline_data(html, data_js)
    html2 = embed_logos(html2)
    OUT.write_text(html2, encoding="utf-8")
    return OUT


def verify(path: Path) -> list[tuple[str, bool]]:
    text = path.read_text(encoding="utf-8")
    return [
        ('no src="assets/"', 'src="assets/' not in text and "src='assets/" not in text),
        ('no src="data.js"', 'src="data.js"' not in text and "src='data.js'" not in text),
        ("contains window.LB_DATA", "window.LB_DATA" in text),
        ("contains data:image", "data:image" in text),
        ('contains "wins"', '"wins"' in text or "'wins'" in text),
        ("contains viewSwitch", "viewSwitch" in text),
        ('contains data-view="mkt"', 'data-view="mkt"' in text),
        ("contains переход в «Сборку»", "переход в «Сборку»" in text),
        ("NO primary closed-won Pipeline KPI", "closed-won в Pipeline" not in text),
    ]


def main() -> int:
    out = build()
    size = out.stat().st_size
    mb = size / 1024 / 1024
    print(f"OUT: {out}")
    print(f"Size: {mb:.3f} MB")
    ok = True
    for name, passed in verify(out):
        print(f"  [{'OK' if passed else 'FAIL'}] {name}")
        ok = ok and passed
    print("OVERALL:", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
