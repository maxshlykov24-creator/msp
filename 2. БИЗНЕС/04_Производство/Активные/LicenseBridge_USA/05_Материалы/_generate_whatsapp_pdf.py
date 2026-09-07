#!/usr/bin/env python3
"""Generate PDF from Подключение_официальной_интеграции_WhatsApp.md"""
import re
from pathlib import Path

import markdown
from xhtml2pdf import pisa

BASE = Path(__file__).parent
MD_FILE = BASE / "Подключение_официальной_интеграции_WhatsApp.md"
PDF_FILE = BASE / "Подключение_официальной_интеграции_WhatsApp.pdf"
FONT = "/System/Library/Fonts/Supplemental/Arial Unicode.ttf"

CSS = """
@page {
    size: A4;
    margin: 2cm 1.8cm;
}
@font-face {
    font-family: DocFont;
    src: url("file://""" + FONT.replace("\\", "/") + """");
}
body {
    font-family: DocFont, sans-serif;
    font-size: 10pt;
    line-height: 1.45;
    color: #1a1a1a;
}
h1 { font-size: 18pt; margin-top: 0; color: #111; border-bottom: 2px solid #2563eb; padding-bottom: 6px; }
h2 { font-size: 13pt; margin-top: 18px; color: #1e40af; page-break-after: avoid; }
h3 { font-size: 11pt; margin-top: 14px; color: #1e3a8a; page-break-after: avoid; }
p, li { orphans: 3; widows: 3; }
table { width: 100%; border-collapse: collapse; margin: 10px 0; font-size: 9pt; }
th, td { border: 1px solid #cbd5e1; padding: 6px 8px; text-align: left; vertical-align: top; }
th { background: #eff6ff; font-weight: bold; }
blockquote { margin: 10px 0; padding: 8px 12px; background: #f8fafc; border-left: 4px solid #2563eb; font-size: 9.5pt; }
code { font-family: DocFont, monospace; background: #f1f5f9; padding: 1px 4px; font-size: 9pt; }
hr { border: none; border-top: 1px solid #e2e8f0; margin: 16px 0; }
ul, ol { padding-left: 18px; }
li { margin-bottom: 4px; }
strong { color: #0f172a; }
"""


def md_to_html(text: str) -> str:
    html = markdown.markdown(
        text,
        extensions=["tables", "fenced_code", "nl2br", "sane_lists"],
    )
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>{CSS}</style></head>
<body>{html}</body></html>"""


def main() -> None:
    md_text = MD_FILE.read_text(encoding="utf-8")
    html = md_to_html(md_text)
    with PDF_FILE.open("wb") as out:
        status = pisa.CreatePDF(html, dest=out, encoding="utf-8")
    if status.err:
        raise SystemExit(f"PDF generation failed with {status.err} errors")
    print(f"OK: {PDF_FILE}")


if __name__ == "__main__":
    main()
