"""Наблюдатель: алерт приходит там, где раньше мы узнавали о поломке от клиента."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app import monitor
from app.config import settings
from app.models import CallEvent, Decision, InboxEvent


@pytest.fixture()
def sent(monkeypatch):
    """Перехватывает отправку: тест проверяет решение, а не Telegram."""
    box: list[tuple[str, str]] = []

    def fake_notify(text: str, key: str = "", cooldown: float = 0.0) -> bool:
        box.append((text, key))
        return True

    monkeypatch.setattr(monitor, "notify", fake_notify)
    return box


_seq = iter(range(1, 10_000))


def _event(status="pending", event_type="add_lead", minutes_ago=0):
    return InboxEvent(
        event_key=f"k{next(_seq)}",
        source="kommo", event_type=event_type, payload={}, status=status,
        created_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
    )


def test_deadletter_alerts_with_count(session, sent):
    for _ in range(2):
        session.add(_event(status="deadletter"))
    session.add(_event(status="done"))
    session.flush()

    assert monitor.check_deadletter(session) == "deadletter"
    assert "2" in sent[0][0]


def test_no_deadletter_no_noise(session, sent):
    session.add(_event(status="done"))
    session.flush()

    assert monitor.check_deadletter(session) == ""
    assert sent == []


def test_lead_burst_fires_above_threshold(session, sent, monkeypatch):
    monkeypatch.setattr(settings, "monitor_lead_burst_per_hour", 5)
    for _ in range(6):
        session.add(_event())
    session.flush()

    assert monitor.check_lead_burst(session) == "burst"


def test_old_leads_are_not_a_burst(session, sent, monkeypatch):
    """Порог считается за час: спокойный день не должен выглядеть штормом."""
    monkeypatch.setattr(settings, "monitor_lead_burst_per_hour", 5)
    for _ in range(20):
        session.add(_event(minutes_ago=180))
    session.flush()

    assert monitor.check_lead_burst(session) == ""


def test_silence_on_sales_ext_alerts(session, sent, monkeypatch):
    """Ровно случай 07.08: софтфон отвалился, звонков нет, никто не знает."""
    monkeypatch.setattr(monitor, "_local_now",
                        lambda: datetime(2026, 8, 19, 15, 0))  # среда, разгар смены
    assert monitor.check_manager_silence(session) == "silence"
    assert "103" in sent[0][0]


def test_recent_call_means_no_silence(session, sent, monkeypatch):
    monkeypatch.setattr(monitor, "_local_now", lambda: datetime(2026, 8, 19, 15, 0))
    for i, ext in enumerate(settings.sales_order):
        session.add(CallEvent(uniqueid=f"u{i}", phone="+15551234567", direction="out",
                              ext=ext, duration=42))
    session.flush()

    assert monitor.check_manager_silence(session) == ""
    assert sent == []


def test_night_and_weekend_silence_is_normal(session, sent, monkeypatch):
    monkeypatch.setattr(monitor, "_local_now", lambda: datetime(2026, 8, 19, 4, 0))
    assert monitor.check_manager_silence(session) == ""
    monkeypatch.setattr(monitor, "_local_now", lambda: datetime(2026, 8, 22, 15, 0))
    assert monitor.check_manager_silence(session) == ""
    assert sent == []


def test_spam_check_reminder_is_periodic(session, sent):
    assert monitor.check_spam_status_due(session) == "spam_check"
    session.flush()
    # маркер в БД, а не в памяти: рестарт воркера не должен звать повторно
    assert monitor.check_spam_status_due(session) == ""
    assert len(sent) == 1


def test_spam_check_reminder_returns_after_period(session, sent):
    old = datetime.now(timezone.utc) - timedelta(days=settings.monitor_spam_check_days + 1)
    session.add(Decision(action="alert.spam_check", shadow=False, detail={}, created_at=old))
    session.flush()

    assert monitor.check_spam_status_due(session) == "spam_check"


def test_digest_goes_once_a_day(session, sent, monkeypatch):
    monkeypatch.setattr(monitor, "_local_now", lambda: datetime(2026, 8, 20, 9, 5))
    assert monitor.check_daily_digest(session) == "digest"
    session.flush()
    assert monitor.check_daily_digest(session) == ""
    assert len(sent) == 1


def test_digest_waits_for_morning(session, sent, monkeypatch):
    monkeypatch.setattr(monitor, "_local_now", lambda: datetime(2026, 8, 20, 6, 0))
    assert monitor.check_daily_digest(session) == ""
    assert sent == []


def test_digest_flags_short_calls(session, sent, monkeypatch):
    """Профиль «сняли и положили» должен быть виден утром, а не через неделю."""
    monkeypatch.setattr(monitor, "_local_now", lambda: datetime(2026, 8, 20, 9, 5))
    for i in range(4):
        session.add(CallEvent(uniqueid=f"s{i}", phone="+1555000000%d" % i,
                              direction="out", ext="103", duration=4))
    session.add(CallEvent(uniqueid="long", phone="+15550001111",
                          direction="out", ext="103", duration=180))
    session.flush()

    assert monitor.check_daily_digest(session) == "digest"
    text = sent[0][0]
    assert "Всего звонков: <b>5</b>" in text
    assert "Короче 10 сек: <b>4</b>" in text
    assert "спам-метки" in text


def _chat_events(*, incoming_minutes_ago: int, human_reply_minutes_ago: int | None = None,
                 lead_id: int = 777, bot_reply: bool = False):
    """Ответы Kommo для чат-проверки: события отдаёт только общий поток /events."""
    now = int(datetime.now(timezone.utc).timestamp())
    incoming = [{"type": "incoming_chat_message", "entity_type": "lead", "entity_id": lead_id,
                 "created_by": 0, "created_at": now - incoming_minutes_ago * 60,
                 "value_after": [{"message": {"origin": "com.wazzup.whatsapp"}}]}]
    outgoing = []
    if human_reply_minutes_ago is not None:
        outgoing.append({"type": "outgoing_chat_message", "entity_type": "lead",
                         "entity_id": lead_id, "created_by": 15648532,
                         "created_at": now - human_reply_minutes_ago * 60,
                         "value_after": [{"message": {"origin": "com.wazzup.whatsapp"}}]})
    if bot_reply:
        outgoing.append({"type": "outgoing_chat_message", "entity_type": "lead",
                         "entity_id": lead_id, "created_by": 0, "created_at": now,
                         "value_after": [{"message": {"origin": "com.ringcentral.sms"}}]})

    class FakeClient:
        def paginate(self, path, key, params=None, max_pages=0):
            return list(incoming if params["filter[type]"] == "incoming_chat_message"
                        else outgoing)

        def get_lead(self, lead_id, with_="contacts"):
            # в алерт идёт имя клиента, а не номер сделки: по номеру непонятно,
            # кто ждёт ответа
            return {"id": lead_id, "name": f"Клиент {lead_id}"}

    return FakeClient()


@pytest.fixture(autouse=True)
def _no_chat_cache():
    from app import chat_events
    chat_events._cache.clear()
    yield
    chat_events._cache.clear()


def test_chat_silence_alerts_after_threshold(session, sent, monkeypatch):
    """Клиент написал два часа назад, ответа человека нет — тот самый пропуск."""
    monkeypatch.setattr(monitor, "_local_now", lambda: datetime(2026, 8, 19, 15, 0))
    client = _chat_events(incoming_minutes_ago=120)

    assert monitor.check_chat_silence(session, client) == "chat_silence"
    assert "777" in sent[0][0]
    assert "WhatsApp" in sent[0][0]


def test_bot_reply_is_not_an_answer(session, sent, monkeypatch):
    """Автоответ бота закрывал бы 63,6% чатов, которые никто не читал."""
    monkeypatch.setattr(monitor, "_local_now", lambda: datetime(2026, 8, 19, 15, 0))
    client = _chat_events(incoming_minutes_ago=120, bot_reply=True)

    assert monitor.check_chat_silence(session, client) == "chat_silence"


def test_human_reply_closes_the_case(session, sent, monkeypatch):
    monkeypatch.setattr(monitor, "_local_now", lambda: datetime(2026, 8, 19, 15, 0))
    client = _chat_events(incoming_minutes_ago=120, human_reply_minutes_ago=30)

    assert monitor.check_chat_silence(session, client) == ""
    assert sent == []


def test_fresh_message_waits_for_the_hour(session, sent, monkeypatch):
    monkeypatch.setattr(monitor, "_local_now", lambda: datetime(2026, 8, 19, 15, 0))
    client = _chat_events(incoming_minutes_ago=20)

    assert monitor.check_chat_silence(session, client) == ""


def test_chat_silence_does_not_repeat_within_12h(session, sent, monkeypatch):
    monkeypatch.setattr(monitor, "_local_now", lambda: datetime(2026, 8, 19, 15, 0))
    client = _chat_events(incoming_minutes_ago=120)

    assert monitor.check_chat_silence(session, client) == "chat_silence"
    session.flush()
    from app import chat_events
    chat_events._cache.clear()
    assert monitor.check_chat_silence(session, client) == ""
    assert len(sent) == 1


def test_undelivered_alert_leaves_no_marker(session, sent, monkeypatch):
    """Бот ещё не заведён — отметка не ставится, иначе она глушит первый живой алерт."""
    monkeypatch.setattr(monitor, "_local_now", lambda: datetime(2026, 8, 19, 15, 0))
    monkeypatch.setattr(monitor, "notify", lambda *a, **kw: False)
    client = _chat_events(incoming_minutes_ago=120)

    assert monitor.check_chat_silence(session, client) == ""
    session.flush()
    monkeypatch.setattr(monitor, "notify", lambda text, key="", cooldown=0.0:
                        sent.append((text, key)) or True)
    from app import chat_events
    chat_events._cache.clear()
    assert monitor.check_chat_silence(session, client) == "chat_silence"
    assert len(sent) == 1


def test_chat_silence_is_quiet_at_night(session, sent, monkeypatch):
    """Ночью не будим: сообщение попадёт в алерт утром, когда есть кому ответить."""
    monkeypatch.setattr(monitor, "_local_now", lambda: datetime(2026, 8, 19, 3, 0))
    client = _chat_events(incoming_minutes_ago=120)

    assert monitor.check_chat_silence(session, client) == ""
    assert sent == []


def test_one_broken_check_does_not_stop_others(session, sent, monkeypatch):
    def boom(_s):
        raise RuntimeError("нет связи с БД")

    monkeypatch.setattr(monitor, "CHECKS", (boom, monitor.check_spam_status_due))
    assert monitor.run_checks(session) == ["spam_check"]
