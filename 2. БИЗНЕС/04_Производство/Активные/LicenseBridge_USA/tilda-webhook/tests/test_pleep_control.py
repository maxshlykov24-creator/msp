import httpx
import pytest

from app.pleep_control import PleepControlClient, PleepControlError, PleepControlUncertain

THREAD = "thread_e7174271-8b41-438b-892f-67697810c70e"


def client(handler):
    return PleepControlClient("test-only", "verified-assistant", transport=httpx.MockTransport(handler))


def thread(handler="AI", assistant="verified-assistant"):
    return httpx.Response(201, json={"ok": True, "data": {
        "thread_id": THREAD, "assistant_id": assistant, "message_handler": handler}})


def test_wrong_assistant_never_sends_command():
    calls = []
    def reply(r):
        calls.append(r.url.path)
        return thread(assistant="other")
    c = client(reply)
    with pytest.raises(PleepControlError, match="mismatch"):
        c.set_enabled(THREAD, False)
    assert len(calls) == 1


@pytest.mark.parametrize("value", ["false", 0, None])
def test_non_boolean_never_calls_api(value):
    c = client(lambda r: pytest.fail("must not call API"))
    with pytest.raises(ValueError):
        c.set_enabled(THREAD, value)


def test_resume_requires_pending_message_ack():
    c = client(lambda r: pytest.fail("must not call API"))
    with pytest.raises(ValueError, match="pending"):
        c.set_enabled(THREAD, True)


def test_timeout_is_uncertain_without_replay():
    calls = []
    def reply(r):
        calls.append(r.url.path)
        if r.url.path.endswith("get-lead"):
            return thread()
        raise httpx.ReadTimeout("lost response")
    c = client(reply)
    with pytest.raises(PleepControlUncertain):
        c.set_enabled(THREAD, False)
    assert len(calls) == 2


def test_off_requires_live_readback():
    calls = []
    def reply(r):
        calls.append(r.url.path)
        if r.url.path.endswith("set-ai"):
            return httpx.Response(201, json={"ok": True, "data": {
                "thread_id": THREAD, "ai_enabled": False, "answered_pending_message": False}})
        return thread("OPERATOR" if len(calls) == 3 else "AI")
    assert client(reply).set_enabled(THREAD, False)["ai_enabled"] is False
    assert len(calls) == 3


def test_acceptance_without_matching_readback_is_not_success():
    def reply(r):
        if r.url.path.endswith("set-ai"):
            return httpx.Response(201, json={"ok": True, "data": {
                "thread_id": THREAD, "ai_enabled": False, "answered_pending_message": False}})
        return thread("AI")
    with pytest.raises(PleepControlUncertain, match="readback"):
        client(reply).set_enabled(THREAD, False)


def test_legacy_id_rejected_without_api():
    with pytest.raises(ValueError, match="Real Pleep"):
        client(lambda r: pytest.fail("must not call API")).get_thread("legacy")


def test_readback_failure_after_write_is_uncertain():
    calls = []
    def reply(r):
        calls.append(r.url.path)
        if len(calls) == 1:
            return thread()
        if len(calls) == 2:
            return httpx.Response(201, json={"ok": True, "data": {
                "thread_id": THREAD, "ai_enabled": False, "answered_pending_message": False}})
        raise httpx.ReadTimeout("readback unavailable")
    with pytest.raises(PleepControlUncertain, match="readback failed"):
        client(reply).set_enabled(THREAD, False)
    assert len(calls) == 3
