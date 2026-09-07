import socket

from app import tg_http


def test_telegram_host_forces_ipv6(monkeypatch):
    seen: list[tuple[object, int]] = []

    def fake(host, port, family=0, type=0, proto=0, flags=0):  # noqa: A002
        seen.append((host, family))
        return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", int(port or 0)))]

    monkeypatch.setattr(tg_http, "_real_getaddrinfo", fake)
    tg_http.install_telegram_ipv6()
    socket.getaddrinfo("api.telegram.org", 443)
    assert seen[-1][1] == socket.AF_INET6
    socket.getaddrinfo("example.com", 443)
    assert seen[-1] == ("example.com", 0)


def test_ipv6_only_does_not_unpatch_telegram(monkeypatch):
    tg_http.install_telegram_ipv6()
    with tg_http.ipv6_only():
        pass
    assert getattr(socket.getaddrinfo, "_keris_tg_ipv6", False)


def test_send_to_client_uses_tg_send_message(monkeypatch):
    from app import reminders

    called = []

    def fake(token, chat_id, text, **kwargs):
        called.append((token, chat_id, text, kwargs.get("reply_markup")))
        return True

    class Token:
        client_bot_token = "tok-1"

    monkeypatch.setattr(reminders, "tg_send_message", fake)
    monkeypatch.setattr(reminders, "settings", Token())
    kb = {"inline_keyboard": [[{"text": "ok", "callback_data": "x"}]]}
    assert reminders.send_to_client(123, "привет", kb) is True
    assert called == [("tok-1", 123, "привет", kb)]
