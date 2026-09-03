"""Плановые проверки хаба: то, что система знает, но человеку не говорит.

Каждая проверка отвечает на вопрос «о чём мы узнали от клиента, а не от себя»:

- `deadletter` — событие пять раз не легло в Kommo. Для менеджера это карточка,
  которой нет; для нас — строка в таблице, которую никто не открывает.
- тишина менеджера — рабочий день идёт, а звонков с добавочного ноль. Именно так
  выглядел отвал софтфона 102 с 07.08 по 13.08.
- всплеск сделок — 11.08 Make.com за минуты налил дублей заявок; порог ловит это
  в час, а не на разборе через сутки.
- напоминание про spam-статус — метка возвращается, если вернуть поведение;
  проверять реестры надо по календарю, иначе не проверяет никто.
- тишина в чате — клиент ответил в WhatsApp, а человек нет. За август на дублях
  осело 98 сообщений клиентов, которых менеджер в рабочей карточке не видел.

Проверки только читают БД и шлют текст. Ничего не лечат: автолечение по
неполным данным один раз уже стоило нам разъезда карточек.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.alerts import notify
from app.config import settings
from app.models import CallEvent, Decision, InboxEvent

log = logging.getLogger("monitor")


def _local_now() -> datetime:
    return datetime.now(ZoneInfo(settings.tz))


def check_deadletter(s: Session) -> str:
    """События, застрявшие после всех попыток."""
    count = s.execute(
        select(func.count()).select_from(InboxEvent).where(InboxEvent.status == "deadletter")
    ).scalar_one()
    if not count:
        return ""
    row = s.execute(
        select(InboxEvent.id, InboxEvent.source, InboxEvent.event_type, InboxEvent.last_error)
        .where(InboxEvent.status == "deadletter")
        .order_by(InboxEvent.updated_at.desc())
        .limit(1)
    ).first()
    last = f"\n▪️ <b>Последняя ошибка:</b> <code>{(row[3] or '')[:100]}</code>" if row else ""
    text = (f"🔴 <b>Сбой синхронизации</b> | LicenseBridge\n\n"
            f"События не доставляются в Kommo.\n"
            f"▪️ <b>В очереди:</b> {count} шт.{last}\n\n"
            f"<i>Требуется вмешательство техподдержки.</i>")
    notify(text, key="deadletter", cooldown=6 * 3600)
    return "deadletter"


def check_manager_silence(s: Session) -> str:
    """Рабочий день, а звонков с добавочных продаж нет ни одного."""
    now = _local_now()
    if now.weekday() >= 5:
        return ""
    if not (settings.monitor_workday_start_hour + settings.monitor_silence_hours
            <= now.hour < settings.monitor_workday_end_hour):
        # раньше порога сутки ещё «молодые», позже — смена уже кончилась
        return ""
    since = datetime.now(timezone.utc) - timedelta(hours=settings.monitor_silence_hours)
    silent = []
    for ext in settings.sales_order:
        calls = s.execute(
            select(func.count()).select_from(CallEvent)
            .where(CallEvent.ext == ext, CallEvent.created_at >= since)
        ).scalar_one()
        if not calls:
            silent.append(ext)
    if not silent:
        return ""
    notify(f"🟡 <b>Отсутствие звонков</b> | LicenseBridge\n\n"
           f"За последние {settings.monitor_silence_hours} ч. не зафиксировано ни одного исходящего звонка.\n"
           f"▪️ <b>Молчащие линии:</b> {', '.join(silent)}\n\n"
           f"<i>Возможен сбой MicroSIP или отсутствие менеджера на рабочем месте.</i>",
           key="silence:" + ",".join(silent), cooldown=6 * 3600)
    return "silence"


def check_lead_burst(s: Session) -> str:
    """Всплеск входящих заявок из Kommo — почерк сорвавшейся Make.com-сценарии."""
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    count = s.execute(
        select(func.count()).select_from(InboxEvent)
        .where(InboxEvent.created_at >= since,
               InboxEvent.event_type.like("add_lead%"))
    ).scalar_one()
    if count < settings.monitor_lead_burst_per_hour:
        return ""
    notify(f"🟡 <b>Аномальный всплеск заявок</b> | LicenseBridge\n\n"
           f"Зафиксирован нетипичный объём новых лидов.\n"
           f"▪️ <b>Поступило:</b> {count} за последний час (порог {settings.monitor_lead_burst_per_hour})\n\n"
           f"<i>Возможна ошибка сценария Make.com (дублирование заявок).</i>",
           key="burst", cooldown=3 * 3600)
    return "burst"


def check_spam_status_due(s: Session) -> str:
    """Напоминание сверить spam-статус DID в Hiya и Free Caller Registry.

    Метка — про поведение, а не про разовую заявку: сняли, вернули поведение —
    получили снова. Маркер живёт в `decisions`, а не в памяти процесса, иначе
    напоминание приходило бы после каждого рестарта.
    """
    action = "alert.spam_check"
    last = s.execute(
        select(Decision.created_at).where(Decision.action == action)
        .order_by(Decision.created_at.desc()).limit(1)
    ).scalar_one_or_none()
    due = datetime.now(timezone.utc) - timedelta(days=settings.monitor_spam_check_days)
    if last is not None:
        seen = last if last.tzinfo else last.replace(tzinfo=timezone.utc)
        if seen > due:
            return ""
    dids = ", ".join(sorted(settings.did_to_ext)) or "—"
    sent = notify(
        f"📋 <b>Регулярная проверка</b> | LicenseBridge\n\n"
        f"Подошёл срок плановой сверки спам-статуса номеров.\n"
        f"▪️ <b>Линии:</b> {dids}\n\n"
        f"<i>Необходимо проверить кабинеты Hiya и Free Caller Registry.</i>",
        key="")
    # маркер ставим и когда бот не настроен: иначе после появления токена придёт
    # ворох просроченных напоминаний
    s.add(Decision(action=action, shadow=False, detail={"sent": sent}))
    return "spam_check"


def check_daily_digest(s: Session) -> str:
    """Утренняя сводка за прошедшие сутки.

    Нужна не ради цифр. Алерт приходит, когда что-то сломалось, но если умрёт сам
    хаб, он об этом не сообщит — молчание неотличимо от «всё хорошо». Сводка
    переворачивает логику: она приходит каждое утро, и её отсутствие само
    становится сигналом.

    Заодно закрывает главную находку 19.08: короткие соединения видно сразу, а не
    на разборе через неделю."""
    now = _local_now()
    if now.hour < settings.monitor_digest_hour:
        return ""
    action = "alert.daily_digest"
    last = s.execute(
        select(Decision.created_at).where(Decision.action == action)
        .order_by(Decision.created_at.desc()).limit(1)
    ).scalar_one_or_none()
    if last is not None:
        seen = last if last.tzinfo else last.replace(tzinfo=timezone.utc)
        if seen > datetime.now(timezone.utc) - timedelta(hours=20):
            return ""

    since = datetime.now(timezone.utc) - timedelta(hours=24)
    calls = s.execute(
        select(CallEvent).where(CallEvent.created_at >= since)
    ).scalars().all()
    answered = [c for c in calls if (c.duration or 0) > 0]
    short = [c for c in answered if (c.duration or 0) < 10]
    # заявки отдельно от общего потока: в inbox львиная доля событий — плановый
    # скан дублей, и в сводке они выглядели бы наплывом лидов
    new_leads = s.execute(
        select(func.count()).select_from(InboxEvent)
        .where(InboxEvent.created_at >= since, InboxEvent.event_type.like("add_lead%"))
    ).scalar_one()
    events = s.execute(
        select(func.count()).select_from(InboxEvent).where(InboxEvent.created_at >= since)
    ).scalar_one()
    dead = s.execute(
        select(func.count()).select_from(InboxEvent).where(InboxEvent.status == "deadletter")
    ).scalar_one()
    by_ext = {ext: sum(1 for c in calls if c.ext == ext) for ext in settings.sales_order}

    lines = [
        f"📊 <b>Сводка за сутки</b> | LicenseBridge",
        f"<i>{now.strftime('%d.%m.%Y %H:%M')}</i>\n",
        f"<b>Телефония</b>",
        f"▪️ Всего звонков: <b>{len(calls)}</b>",
        f"▪️ Отвечено: <b>{len(answered)}</b>",
        f"▪️ Короче 10 сек: <b>{len(short)}</b>",
        f"▪️ По линиям: {', '.join(f'{e}: {n}' for e, n in by_ext.items()) or '—'}\n",
        f"<b>Система</b>",
        f"▪️ Новых заявок: <b>{new_leads}</b>",
        f"▪️ Ошибок очереди: <b>{dead}</b>"
    ]
    if answered and len(short) > len(answered) / 2:
        lines.append(f"\n⚠️ <i>Внимание: более 50% отвеченных звонков короче 10 секунд. Высокий риск спам-метки.</i>")
    sent = notify("\n".join(lines), key="")
    s.add(Decision(action=action, shadow=False,
                   detail={"sent": sent, "calls": len(calls), "short": len(short)}))
    return "digest"


def check_chat_silence(s: Session, client) -> str:
    """Клиент ответил в мессенджер, а человек не ответил ему.

    Ответ бота ответом не считается: в июле так без ответа менеджера осталось
    63,6% чатов при медиане ответа 8 часов. Сообщение, пришедшее ночью, попадёт в
    алерт утром — порог считается от начала рабочего дня, а не от полуночи.

    Отмеченные сделки пишем в `decisions`, а не в память процесса: иначе рестарт
    хаба присылал бы один и тот же список заново."""
    now = _local_now()
    if now.weekday() >= 5:
        return ""
    if not (settings.monitor_workday_start_hour <= now.hour
            < settings.monitor_workday_end_hour):
        return ""

    from app.chat_events import lead_url, recent

    # окно двое суток: за более старую тишину алерт уже приходил
    stats = recent(client, days=2)
    threshold = int(datetime.now(timezone.utc).timestamp()) - settings.chat_silence_hours * 3600
    stale = [st for st in stats.values() if st.unanswered and st.last_in < threshold]
    if not stale:
        return ""

    action = "alert.chat_silence"
    since = datetime.now(timezone.utc) - timedelta(hours=12)
    seen_rows = s.execute(
        select(Decision.detail).where(Decision.action == action, Decision.created_at >= since)
    ).scalars().all()
    seen = {int((d or {}).get("lead_id") or 0) for d in seen_rows}
    fresh = [st for st in stale if st.lead_id not in seen]
    if not fresh:
        return ""

    fresh.sort(key=lambda st: st.last_in)
    lines = [f"🟡 <b>Чаты без ответа</b> | LicenseBridge\n",
             f"Клиенты ожидают ответа менеджера более {settings.chat_silence_hours} ч.\n"]
    for st in fresh[:8]:
        waited = (int(datetime.now(timezone.utc).timestamp()) - st.last_in) // 60
        lead = client.get_lead(st.lead_id, with_=None) or {}
        name = str(lead.get("name") or f"Сделка #{st.lead_id}").replace("<", "&lt;").replace(">", "&gt;")
        lines.append(f"▪️ <a href='{lead_url(st.lead_id)}'>{name}</a> ({st.channels()}) — ждёт {waited // 60} ч {waited % 60} мин")
    if len(fresh) > 8:
        lines.append(f"\n<i>...и ещё {len(fresh) - 8} диалогов.</i>")
    lines.append(f"\n<i>Ответы бота не учитываются.</i>")
    sent = notify("\n".join(lines), key="")
    if not sent:
        # Отметку ставим только за доставленное. Иначе алерт, не ушедший из-за
        # ненастроенного бота, глушит настоящий на 12 часов — так и случилось
        # 19.08: проверка отработала за шесть минут до заливки токена.
        log.warning("chat_silence: %s сделок без ответа, но алерт не ушёл", len(fresh))
        return ""
    for st in fresh:
        s.add(Decision(action=action, shadow=False,
                       detail={"lead_id": st.lead_id, "sent": True}))
    return "chat_silence"


CHECKS = (check_deadletter, check_manager_silence, check_lead_burst,
          check_spam_status_due, check_daily_digest)
# проверки, которым нужен Kommo: чат-события живут только в API, не в нашей БД
CHECKS_KOMMO = (check_chat_silence,)


def run_checks(s: Session, client=None) -> list[str]:
    """Прогоняет все проверки. Падение одной не должно ронять остальные."""
    fired: list[str] = []
    checks = list(CHECKS) + (list(CHECKS_KOMMO) if client is not None else [])
    for check in checks:
        try:
            name = check(s, client) if check in CHECKS_KOMMO else check(s)
        except Exception as exc:  # noqa: BLE001 — наблюдатель не мешает работе
            log.warning("monitor %s failed: %s", check.__name__, exc)
            continue
        if name:
            fired.append(name)
    return fired
