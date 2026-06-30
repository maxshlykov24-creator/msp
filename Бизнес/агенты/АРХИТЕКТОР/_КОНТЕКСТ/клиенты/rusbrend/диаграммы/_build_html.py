#!/usr/bin/env python3
"""Сборка HTML-диаграмм из карта.md (запуск не обязателен — HTML уже в папке)."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent
CARD = ROOT.parent / "карта.md"

DIAGRAMS = [
    (
        "01_RUSbrend_Поток_данных.html",
        "Поток данных",
        "Google Sheets ↔ Apps Script ↔ МойСклад",
        "flowchart TD",
    ),
    (
        "02_RUSbrend_Жизненный_цикл_отгрузки.html",
        "Жизненный цикл отгрузки",
        "Статусы отгрузки в МойСклад",
        "flowchart TD",
    ),
    (
        "03_RUSbrend_Процесс_приёмки.html",
        "Процесс приёмки",
        "Заказ → приёмка → расхождение",
        "flowchart TD",
    ),
]

HTML_SHELL = """<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} — RUSbrend</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      padding: 32px 24px 48px;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #fafaf9;
      color: #44403c;
    }}
    .page {{
      max-width: 1200px;
      margin: 0 auto;
      background: #fff;
      border: 1px solid #e7e5e4;
      border-radius: 12px;
      padding: 32px 28px 40px;
      box-shadow: 0 1px 3px rgba(0,0,0,.04);
    }}
    h1 {{
      margin: 0 0 6px;
      font-size: 1.5rem;
      font-weight: 600;
      letter-spacing: -0.02em;
    }}
    .meta {{
      margin: 0 0 8px;
      font-size: 0.875rem;
      color: #78716c;
    }}
    .subtitle {{
      margin: 0 0 28px;
      font-size: 0.9375rem;
      color: #57534e;
    }}
    .diagram {{
      overflow-x: auto;
      padding: 8px 0;
    }}
    .mermaid {{
      display: flex;
      justify-content: center;
    }}
    footer {{
      margin-top: 28px;
      padding-top: 16px;
      border-top: 1px solid #f5f5f4;
      font-size: 0.75rem;
      color: #a8a29e;
    }}
    @media print {{
      body {{ background: #fff; padding: 0; }}
      .page {{ box-shadow: none; border: none; max-width: none; }}
    }}
  </style>
</head>
<body>
  <div class="page">
    <h1>{title}</h1>
    <p class="meta">RUSbrend · Интеграционная карта · Вариант 1</p>
    <p class="subtitle">{subtitle}</p>
    <div class="diagram">
      <pre class="mermaid">
{mermaid}
      </pre>
    </div>
    <footer>MS Product · 2026-05-14 · {filename}</footer>
  </div>
  <script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
  <script>
    mermaid.initialize({{
      startOnLoad: true,
      securityLevel: "loose",
      theme: "base",
      themeVariables: {{
        fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif",
        fontSize: "14px",
        primaryColor: "#f3f7fb",
        primaryTextColor: "#4a5f6b",
        primaryBorderColor: "#b8cfe0",
        lineColor: "#a8a29e",
        secondaryColor: "#f4f8f4",
        tertiaryColor: "#faf6f0"
      }},
      flowchart: {{ htmlLabels: true, curve: "basis", padding: 16 }}
    }});
  </script>
</body>
</html>
"""


def extract_mermaid(md: str, heading: str) -> str:
    marker = f"## {heading}"
    start = md.index(marker)
    block = md.index("```mermaid", start)
    block = md.index("\n", block) + 1
    end = md.index("```", block)
    return md[block:end].strip()


def main() -> None:
    md = CARD.read_text(encoding="utf-8")
    headings = [
        "Диаграмма: поток данных",
        "Диаграмма: жизненный цикл отгрузки",
        "Диаграмма: процесс приёмки",
    ]
    for (filename, title, subtitle, _), heading in zip(DIAGRAMS, headings):
        mermaid = extract_mermaid(md, heading)
        html = HTML_SHELL.format(
            title=title,
            subtitle=subtitle,
            mermaid=mermaid,
            filename=filename,
        )
        (ROOT / filename).write_text(html, encoding="utf-8")
        print(f"OK {filename}")


if __name__ == "__main__":
    main()
