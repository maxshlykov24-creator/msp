from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from app.call_rules import CATEGORY_NAMES, CRITERIA, STATUS_NAMES
from app.config import settings

STATE_NAMES = {
    "analysis_ready": "Ожидает анализа агента", "analyzing": "Агент анализирует",
    "analysis_ambiguous": "Анализ агента требует проверки",
    "waiting_recording": "Ожидает запись", "ready": "Готов к анализу",
    "submitting": "Отправляется в Nexara", "submit_ambiguous": "Отправка требует проверки",
    "processing": "Nexara обрабатывает", "complete": "Обработан",
    "needs_review": "Требует проверки", "recording_unavailable": "Запись недоступна",
    "missed": "Без разговора", "error": "Ошибка обработки",
}
PHONE_RE = re.compile(r"(?<!\w)(?:\+?7|8)[\s()\-]*(?:\d[\s()\-]*){10}(?!\d)")


def redact_phone(text: str) -> str:
    return PHONE_RE.sub("[телефон скрыт]", text or "")


def manager_name(call) -> str:
    if call.manager_verified and call.manager_id:
        return settings.call_manager_names.get(call.manager_id, f"Сотрудник {call.manager_id}")
    return "Менеджер не подтверждён"


def evidence_for(item: dict) -> list[dict]:
    evidence = list(item.get("evidence") or [])
    if not evidence:
        for condition in item.get("conditions") or []:
            evidence.extend(condition.get("evidence") or [])
    unique = {}
    for e in evidence:
        unique.setdefault(e.get("quote", ""), e)
    return list(unique.values())


def timestamp(seconds) -> str:
    if seconds is None:
        return ""
    seconds = int(float(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def report_marker(call, target: str) -> str:
    return f"[DIVO-CALL:{call.account_id}:{call.id}:{target}]"


def render_report(call, *, telegram: bool = False, target: str = "") -> str:
    a = call.analysis or {}
    when = datetime.fromtimestamp(call.occurred_at, ZoneInfo("Europe/Moscow")).strftime("%d.%m.%Y %H:%M")
    lines = [f"DIVO · звонок {when} МСК", manager_name(call),
             CATEGORY_NAMES.get(call.category, call.category),
             f"Длительность: {timestamp(call.duration_sec)}"]
    if call.is_calibration:
        lines.append("Калибровка: отчёт не рассылается")
    if call.is_scored:
        lines.append(f"Выполнено {call.yes_count} из {call.applicable_count} · {call.score:g}%")
    else:
        lines.append("Без общей оценки: " + ("результат требует проверки" if call.state == "needs_review"
                                             else call.category_reason or STATE_NAMES.get(call.state, call.state)))
    summary = redact_phone(a.get("summary", ""))
    if summary:
        lines += ["", summary[:400 if telegram else 1000]]
    for item in ([] if call.validation_errors else a.get("criteria") or []):
        label = CRITERIA.get(item.get("id"), ("Критерий", []))[0]
        lines.append(f"\n{item.get('id')}. {label}: {STATUS_NAMES.get(item.get('status'), 'Требует проверки')}")
        lines.append(redact_phone(item.get("explanation", ""))[:150 if telegram else 600])
        evidence = evidence_for(item)
        for e in evidence[:2 if telegram else 4]:
            ts = timestamp(e.get("start"))
            lines.append((ts + " " if ts else "") + "«" + redact_phone(e.get("quote", ""))[:130 if telegram else 500] + "»")
    o = a.get("outcome") or {}
    if o:
        meeting = {True: "согласована", False: "не согласована", None: "не выяснено"}.get(o.get("meeting_agreed"))
        trade = {"interest": "интерес", "refused": "отказ", "no_car": "нет автомобиля",
                 "not_discussed": "не выяснено"}.get(o.get("trade_in"), "не выяснено")
        lines += ["", f"Результат: встреча {meeting}. Трейд-ин: {trade}."]
        if o.get("meeting_when"):
            lines.append("Время встречи: " + redact_phone(o["meeting_when"])[:200])
        if o.get("next_contact"):
            lines.append("Следующий контакт: " + redact_phone(o["next_contact"])[:200])
    if a.get("recommendation"):
        lines.append("Приоритет: " + redact_phone(a["recommendation"])[:200 if telegram else 600])
    footer = [f"Разбор и транскрипт: {settings.calls_dashboard_url.rstrip('/')}/?call={call.id}#callQuality"]
    if len(call.lead_ids) != 1:
        footer.append("Связь со сделкой требует проверки")
    for lead_id in call.lead_ids[:3]:
        footer.append(f"Сделка: {settings.amo_base_url.rstrip('/')}/leads/detail/{int(lead_id)}")
    if target:
        footer.append(report_marker(call, target))
    footer.append(f"Правила {call.rule_version or '—'}")
    body = "\n".join(lines)
    foot = "\n".join(footer)
    limit = 3900 if telegram else 14000
    if len(body) + len(foot) + 2 > limit and telegram:
        # Все семь пунктов сохраняются даже при длинных доказательствах.
        compact = lines[:5] + [summary[:200]]
        for item in ([] if call.validation_errors else a.get("criteria") or []):
            name = CRITERIA.get(item.get("id"), ("Критерий", []))[0]
            ev = evidence_for(item)
            quote_line = " · ".join((timestamp(e.get("start")) + " «" + redact_phone(e.get("quote", ""))[:70] + "»")
                                    for e in ev[:2])
            compact += [f"{item.get('id')}. {name}: {STATUS_NAMES.get(item.get('status'), 'Требует проверки')}",
                        redact_phone(item.get("explanation", ""))[:90], quote_line]
        compact += [line for line in lines if line.startswith(("Результат:", "Время встречи:", "Следующий контакт:", "Приоритет:"))]
        body = "\n".join(compact)
    if len(body) + len(foot) + 2 > limit:
        body = body[:max(0, limit - len(foot) - 40)] + "\nПодробности по ссылке."
    return body + "\n\n" + foot
