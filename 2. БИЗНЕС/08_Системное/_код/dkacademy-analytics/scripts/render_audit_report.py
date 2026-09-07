#!/usr/bin/env python3
"""Собрать самодостаточный клиентский HTML-протокол из audit JSON."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LABELS = {
    "new_leads": ("Новые обращения", "created_at", "Продажи", "уникальные lead_id"),
    "cohort_won": ("Успех когорты", "created_at → текущий Успех", "Продажи", "lead_id когорты в статусе Успех"),
    "conv_lead_deal": ("Конверсия лид → Успех", "когорта created_at", "Продажи", "успех когорты / новые обращения"),
    "paid": ("Оплачено сделок", "closed_at", "Продажи", "уникальные lead_id в Успехе"),
    "revenue": ("Приход", "closed_at + Успех", "Продажи", "сумма price оплаченных"),
    "avg_check": ("Средний чек", "closed_at + Успех", "Продажи", "приход / оплачено"),
    "invoice_count": ("Выставлено счетов", "событие входа в этап", "Продажи", "первый переход на этап / lead_id"),
    "invoice_sum": ("Сумма счетов", "событие + бюджет сделки", "Продажи", "сумма price по первым переходам"),
    "calls_out": ("Исходящие звонки", "note.created_at", "Все лиды", "уникальные заметки call_out"),
    "call_minutes": ("Минуты разговоров", "note.created_at", "Все лиды", "сумма duration / 60"),
    "messages_in": ("Входящие сообщения", "event.created_at", "Все лиды", "уникальные incoming event id"),
    "messages_out": ("Исходящие сообщения", "event.created_at", "Все лиды", "уникальные outgoing event id"),
    "dialogs": ("Диалоги", "talk_created", "Все лиды", "уникальные talk event id"),
    "missed": ("Пропущенные", "talk_missed_event", "Все лиды", "уникальные missed event id"),
    "rt_avg": ("Среднее время ответа", "пары событий", "Все лиды", "сумма минут / число пар"),
    "first_rt_avg": ("Первый ответ в диалоге", "первая доступная пара событий", "Все лиды", "операционный средний показатель"),
}


def fmt(value: object, metric: str) -> str:
    if value is None:
        return "—"
    if metric in {"revenue", "avg_check", "invoice_sum"}:
        return f"{float(value):,.0f} ₽".replace(",", " ")
    if metric in {"conv_lead_deal"}:
        return f"{float(value):.2f}%"
    if metric in {"call_minutes", "rt_avg", "first_rt_avg"}:
        return f"{float(value):.2f}"
    return f"{int(value):,}".replace(",", " ")


def metric_rows(period: dict) -> str:
    audit = period.get("metrics") or {}
    dashboard = period.get("dashboard") or {}
    differences = period.get("dashboard_minus_audit") or {}
    operational = set(period.get("operational_metrics") or [])
    rows = []
    for metric, (label, axis, pipeline, formula) in LABELS.items():
        diff = differences.get(metric)
        status = (
            "note" if metric in operational
            else "ok" if diff is None or abs(float(diff)) <= 0.01
            else "bad"
        )
        suffix = " · операционный, вне строгой сверки" if metric in operational else ""
        rows.append(
            "<tr>"
            f"<td><strong>{html.escape(label)}</strong><small>{html.escape(axis)} · {html.escape(pipeline)} · {html.escape(formula + suffix)}</small></td>"
            f"<td>{fmt(dashboard.get(metric), metric)}</td>"
            f"<td>{fmt(audit.get(metric), metric)}</td>"
            f"<td class='{status}'>{fmt(diff, metric) if diff is not None else 'не подключено'}</td>"
            "</tr>"
        )
    return "".join(rows)


def detail_block(period: dict) -> str:
    details = period.get("details") or {}
    parts = []
    for key in (
        "new_lead_ids", "cohort_won_ids", "paid_ids", "invoice_rows",
        "call_note_ids", "chat_event_ids", "talk_event_ids", "missed_event_ids",
    ):
        value = details.get(key) or []
        parts.append(
            f"<details><summary>{html.escape(key)} <b>{len(value)}</b></summary>"
            f"<pre>{html.escape(json.dumps(value, ensure_ascii=False, indent=2))}</pre></details>"
        )
    return "".join(parts)


def quality_block(period: dict) -> str:
    quality = period.get("data_quality") or {}
    counts = quality.get("counts") or {}
    labels = {
        "zero_price": "Нулевая сумма",
        "no_contact": "Без контакта",
        "no_source": "Без источника",
        "no_responsible": "Без ответственного",
        "invalid_closed_at": "Некорректная дата закрытия",
    }
    return (
        f"<p>Проверено успешных сделок периода: <b>{int(quality.get('checked_won') or 0)}</b></p>"
        "<div class='quality'>"
        + "".join(
            f"<span><b>{int(counts.get(key) or 0)}</b>{html.escape(label)}</span>"
            for key, label in labels.items()
        )
        + "</div>"
    )


def render(payload: dict) -> str:
    periods = payload.get("periods") or []
    operational = {
        "invoice_sum", "messages_in", "messages_out", "dialogs", "missed",
        "rt_avg", "first_rt_avg",
    }
    checked_metrics = (len(LABELS) - len(operational)) * len(periods)
    total_diffs = sum(
        1 for period in periods
        for metric, value in (period.get("dashboard_minus_audit") or {}).items()
        if metric not in operational and abs(float(value)) > 0.01
    )
    overall = bool(payload.get("ok"))
    sections = []
    for index, period in enumerate(periods, 1):
        dates = period.get("period") or {}
        sections.append(
            f"<section id='period-{index}'><div class='section-head'><span>0{index}</span>"
            f"<div><h2>{html.escape(dates.get('from', ''))} — {html.escape(dates.get('to', ''))}</h2>"
            f"<p>{'Все инварианты выполнены' if period.get('ok') else 'Есть расхождения — см. строки ниже'}</p></div></div>"
            "<div class='table-wrap'><table><thead><tr><th>Метрика / паспорт</th><th>Дашборд</th>"
            "<th>Прямой расчёт amoCRM</th><th>Разница</th></tr></thead><tbody>"
            f"{metric_rows(period)}</tbody></table></div>"
            f"<div class='invariants'><h3>Автоматические инварианты</h3>"
            + "".join(
                f"<span class='{'ok' if value else 'bad'}'>{'✓' if value else '×'} {html.escape(key)}</span>"
                for key, value in (period.get("invariants") or {}).items()
            )
            + f"</div><h3>Качество исходных данных CRM</h3>{quality_block(period)}"
            + "<h3>Раскрытие до исходных сущностей</h3>"
            f"{detail_block(period)}</section>"
        )
    generated = payload.get("generated_at") or datetime.now().isoformat()
    checksum = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>DKAcademy — Протокол сверки аналитики</title>
<style>
:root{{--orange:#f5822b;--ink:#151515;--muted:#717171;--line:#dedbd7;--paper:#f7f5f2;--ok:#18763c;--bad:#b42318}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;background:linear-gradient(135deg,#fff 0,#f4f1ed 55%,#ece7e1 100%);color:var(--ink);font:16px/1.5 "Avenir Next","Gill Sans",sans-serif}}
.hero{{min-height:92vh;display:flex;flex-direction:column;justify-content:space-between;padding:40px clamp(24px,6vw,88px);position:relative;overflow:hidden}}
.hero:after{{content:"";position:absolute;right:-8vw;bottom:-22vw;width:58vw;height:58vw;border:clamp(34px,7vw,110px) solid var(--orange);border-radius:50%;opacity:.94;animation:rise 1s ease-out both}}
.brand{{font-weight:900;font-size:clamp(28px,4vw,62px);letter-spacing:-.06em;z-index:1}}.brand i{{color:var(--orange);font-style:normal}}
.hero-main{{max-width:850px;z-index:1}}.eyebrow{{text-transform:uppercase;letter-spacing:.18em;font-size:12px;font-weight:800;color:var(--muted)}}
h1{{font-size:clamp(44px,7vw,104px);line-height:.92;letter-spacing:-.07em;margin:18px 0 24px;max-width:900px}}
.lead{{font-size:clamp(18px,2vw,27px);max-width:680px;color:#444}}
.verdict{{z-index:1;display:flex;gap:26px;align-items:end;flex-wrap:wrap}}.verdict strong{{font-size:clamp(48px,8vw,110px);line-height:.8;color:{'var(--ok)' if overall else 'var(--bad)'}}}
.verdict p{{margin:0;max-width:460px}}main{{background:#fff;padding:80px clamp(20px,6vw,88px)}}section{{max-width:1280px;margin:0 auto 100px}}
.section-head{{display:flex;gap:22px;align-items:start;border-top:3px solid var(--ink);padding-top:18px;margin-bottom:26px}}.section-head>span{{color:var(--orange);font-weight:900;font-size:25px}}
h2{{font-size:clamp(28px,4vw,54px);line-height:1;margin:0 0 8px;letter-spacing:-.04em}}h3{{margin-top:34px}}.section-head p{{margin:0;color:var(--muted)}}
.table-wrap{{overflow:auto}}table{{border-collapse:collapse;width:100%;min-width:760px}}th,td{{padding:14px 12px;border-bottom:1px solid var(--line);text-align:right}}th:first-child,td:first-child{{text-align:left}}th{{font-size:11px;text-transform:uppercase;letter-spacing:.1em;color:var(--muted)}}td small{{display:block;color:var(--muted)}}.ok{{color:var(--ok);font-weight:800}}.bad{{color:var(--bad);font-weight:800}}.note{{color:var(--muted);font-weight:700}}
.invariants{{display:flex;gap:10px 24px;flex-wrap:wrap;align-items:center}}.invariants h3{{width:100%}}details{{border-top:1px solid var(--line);padding:13px 0}}summary{{cursor:pointer}}pre{{white-space:pre-wrap;word-break:break-word;background:var(--paper);padding:16px;max-height:360px;overflow:auto}}
.quality{{display:flex;gap:14px 28px;flex-wrap:wrap}}.quality span{{display:grid;color:var(--muted)}}.quality b{{font-size:28px;color:var(--ink)}}
footer{{padding:40px clamp(20px,6vw,88px);background:var(--ink);color:#fff;font-size:12px;word-break:break-all}}footer strong{{color:var(--orange)}}
@keyframes rise{{from{{transform:translateY(15%);opacity:0}}to{{transform:none;opacity:.94}}}}@media(max-width:700px){{.hero{{min-height:820px;padding-top:24px}}.hero:after{{width:100vw;height:100vw;right:-45vw;bottom:-30vw}}main{{padding-top:54px}}}}
@media(prefers-reduced-motion:reduce){{*{{animation:none!important;scroll-behavior:auto!important}}}}
</style></head><body>
<header class="hero"><div class="brand">DK<i>Academy</i></div><div class="hero-main"><div class="eyebrow">Протокол сверки аналитики</div>
<h1>Цифры, которые можно повторить.</h1><p class="lead">Три независимых периода. Каждая метрика раскрывается до сделки или события amoCRM.</p></div>
<div class="verdict"><strong>{'0' if overall else total_diffs}</strong><p><b>{'необъяснённых расхождений' if overall else 'расхождений требуют разбора'}</b><br>{checked_metrics} проверок метрик · утверждённая версия формул</p></div></header>
<main>{''.join(sections)}
<section><div class="section-head"><span>04</span><div><h2>Границы гарантии</h2><p>Что именно подтверждает этот протокол</p></div></div>
<p><b>Гарантируется:</b> строгие бизнес-метрики воспроизводимы, диапазоны считаются по МСК, итог раскрывается до lead/event id, расхождения не скрываются.</p>
<p><b>Операционный контур:</b> сумма счетов, сообщения, диалоги и SLA показываются справочно. Событие счёта не хранит исторический бюджет, а коммуникации требуют тяжёлого чтения истории amoCRM.</p>
<p><b>Не гарантируется:</b> корректность заполнения карточек amoCRM. Нулевая сумма, отсутствующий источник или ответственный отражаются как дефект исходных данных, а не как ошибка дашборда.</p></section></main>
<footer><strong>Контрольная сумма SHA-256</strong><br>{checksum}</footer>
</body></html>"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "reports" / "Протокол_сверки_DKAcademy.html")
    args = parser.parse_args()
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(payload), encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
