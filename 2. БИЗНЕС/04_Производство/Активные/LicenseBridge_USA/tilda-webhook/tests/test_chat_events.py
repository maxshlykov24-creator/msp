"""Переписка из общего потока событий: единственный доступный нам источник.

Проверено на живом аккаунте 19.08.2026: `GET /events?filter[entity_id]=<сделка>`
чат-события не возвращает, `with=talks` на сделке пусто, `amomessage` в notes нет.
Поэтому группировка окна по сделкам — то место, где ошибка стоит дорого."""
from __future__ import annotations

import time

from app import chat_events


class FakeClient:
    def __init__(self, incoming: list[dict], outgoing: list[dict]):
        self.incoming, self.outgoing = incoming, outgoing
        self.calls = 0

    def paginate(self, path, key, params=None, max_pages=0):
        self.calls += 1
        assert path == "/events"
        return list(self.incoming if params["filter[type]"] == chat_events.INCOMING
                    else self.outgoing)


def _msg(lead_id, created_at, author=0, origin="com.wazzup.whatsapp", entity="lead"):
    return {"entity_type": entity, "entity_id": lead_id, "created_by": author,
            "created_at": created_at, "value_after": [{"message": {"origin": origin}}]}


def test_counts_and_last_message_per_lead():
    now = int(time.time())
    client = FakeClient(
        incoming=[_msg(1, now - 600), _msg(1, now - 300), _msg(2, now - 100)],
        outgoing=[_msg(1, now - 200, author=15648532),
                  _msg(2, now - 50, origin="com.ringcentral.sms")],
    )
    stats = chat_events.fetch(client, now - 86400)

    assert stats[1].incoming == 2
    assert stats[1].outgoing == 1
    assert stats[1].outgoing_human == 1
    assert stats[1].last_in == now - 300
    assert stats[2].outgoing_human == 0        # ушло ботом, человек не отвечал


def test_bot_reply_leaves_chat_unanswered():
    now = int(time.time())
    client = FakeClient(incoming=[_msg(5, now - 600)],
                        outgoing=[_msg(5, now - 100, origin="com.ringcentral.sms")])
    stats = chat_events.fetch(client, now - 86400)

    assert stats[5].unanswered is True
    assert "SMS" in stats[5].channels()


def test_human_reply_after_client_answers_it():
    now = int(time.time())
    client = FakeClient(incoming=[_msg(6, now - 600)],
                        outgoing=[_msg(6, now - 60, author=15537380)])
    stats = chat_events.fetch(client, now - 86400)

    assert stats[6].unanswered is False


def test_contact_level_chat_is_not_a_lead():
    """Чат на карточке контакта нельзя записать сделке: у него нет сделки."""
    now = int(time.time())
    client = FakeClient(incoming=[_msg(9, now - 60, entity="contact")], outgoing=[])
    assert chat_events.fetch(client, now - 86400) == {}


def test_window_is_cached_between_calls():
    """Окно тянет десятки страниц: дедуп не должен делать это на каждую сделку."""
    now = int(time.time())
    client = FakeClient(incoming=[_msg(1, now - 60)], outgoing=[])
    chat_events._cache.clear()

    chat_events.recent(client, days=2)
    chat_events.recent(client, days=2)
    assert client.calls == 2                   # два типа событий, один проход
    chat_events._cache.clear()


def test_describe_names_channel_and_bot_share():
    now = int(time.time())
    client = FakeClient(incoming=[_msg(3, now - 3600)],
                        outgoing=[_msg(3, now - 1800),
                                  _msg(3, now - 900, author=15648532)])
    st = chat_events.fetch(client, now - 86400)[3]
    text = st.describe()

    assert "клиент 1" in text
    assert "из них ботом 1" in text
    assert "WhatsApp" in text


def test_lead_url_points_to_interface_not_api():
    assert "/api/" not in chat_events.lead_url(29892373)
    assert chat_events.lead_url(29892373).endswith("/leads/detail/29892373")
