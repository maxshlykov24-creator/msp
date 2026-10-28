from contextlib import contextmanager
from copy import deepcopy

import pytest

from app import pleep_bridge as bridge
from app.config import settings
from app.models import PleepControlState
from app.pleep_control import PleepControlUncertain


class CRM:
    def __init__(self, flag=None):
        self.flag = flag
        self.status = ""
    def get_lead(self, lid):
        fields = [] if not self.flag else [{"field_id": 200, "values": [{"value": self.flag}]}]
        return {"id": lid, "custom_fields_values": deepcopy(fields)}
    def patch(self, path, body):
        for f in body["custom_fields_values"]:
            if f["field_id"] == 200:
                self.flag = f["values"][0]["value"]
            if f["field_id"] == 201:
                self.status = f["values"][0]["value"]


class Remote:
    def __init__(self, mode="AI"):
        self.mode = mode
        self.calls = []
    def get_thread(self, t):
        return {"message_handler": self.mode}
    def set_enabled(self, t, enabled, **kw):
        self.calls.append((t, enabled))
        self.mode = "AI" if enabled else "OPERATOR"


@pytest.fixture(autouse=True)
def setup(session, monkeypatch):
    @contextmanager
    def scope():
        yield session
        session.commit()
    monkeypatch.setattr(bridge, "session_scope", scope)
    monkeypatch.setattr(settings, "pleep_control_field", 200)
    monkeypatch.setattr(settings, "pleep_control_status_field", 201)


def test_bootstrap_does_not_enable_operator_dialog():
    crm, remote = CRM(), Remote("OPERATOR")
    bridge.sync_one(crm, remote, 1, ["test"])
    assert crm.flag is False
    assert not remote.calls


def test_manual_checkbox_controls_remote_and_confirms(session):
    crm, remote = CRM(), Remote()
    bridge.sync_one(crm, remote, 1, ["test"])
    assert crm.flag is True
    crm.flag = False
    bridge.sync_one(crm, remote, 1, ["test"])
    assert remote.mode == "OPERATOR" and crm.flag is False
    crm.flag = True
    bridge.sync_one(crm, remote, 1, ["test"])
    assert remote.mode == "AI" and crm.flag is True
    assert not session.get(PleepControlState, 1).data.get("pending")


def test_old_event_cannot_override_manual_resume():
    d = bridge.intent({}, True, "manual", 100)
    assert bridge.intent(d, False, "call", 99) == d
    assert bridge.intent(d, False, "human_message", 100) == d
    assert bridge.intent(d, False, "call", 101)["pending"]["enabled"] is False


def test_native_takeover_turns_off_checkbox(session):
    crm, remote = CRM(), Remote()
    bridge.sync_one(crm, remote, 1, ["test"])
    remote.mode = "OPERATOR"
    bridge.sync_one(crm, remote, 1, ["test"])
    assert crm.flag is False
    assert remote.calls == [("test", False)]
    assert not session.get(PleepControlState, 1).data.get("pending")


def test_new_call_intent_survives_old_network_completion(session):
    bridge._save(1, {"cutoff": 100, "pending": {"at": 200, "enabled": False}})
    bridge._save(1, {"cutoff": 100, "completed_at": 150, "last_flag": True})
    assert session.get(PleepControlState, 1).data["pending"]["at"] == 200


def test_call_trigger_persists_and_deduplicates(session, monkeypatch):
    from types import SimpleNamespace
    from app.models import PleepThreadBinding
    monkeypatch.setattr(settings, "enable_pleep_control", True)
    session.add(PleepThreadBinding(thread_id="test", lead_id=1))
    bridge._save(1, {"last_flag": True, "cutoff": 0})
    ctx = SimpleNamespace(s=session, shadow=False, inbox_id=None)
    bridge.queue_call_pause(ctx, 1, "call-one")
    session.commit()
    first = deepcopy(session.get(PleepControlState, 1).data)
    assert first["pending"]["enabled"] is False
    bridge.queue_call_pause(ctx, 1, "call-one")
    assert session.get(PleepControlState, 1).data == first


def test_discovery_rejects_shared_phone(monkeypatch):
    from app import identity
    monkeypatch.setattr(identity, "find_contacts_by_phone", lambda *a: [{"id": 1}, {"id": 2}])
    assert bridge.bind_unique_phone(None, None, "unused", "+18185550100") is False


def test_signed_event_cannot_be_modified_or_replayed_late():
    import hashlib,hmac
    raw = b'{"event":"crm.status_changed"}'
    sig = 'sha256=' + hmac.new(b'secret', b'1000.' + raw, hashlib.sha256).hexdigest()
    assert bridge.verify_signature(raw, '1000', sig, 'secret', now=1001)
    assert not bridge.verify_signature(raw+b' ', '1000', sig, 'secret', now=1001)
    assert not bridge.verify_signature(raw, '1000', sig, 'secret', now=1301)
    assert not bridge.verify_signature(raw, 'invalid', sig, 'secret', now=1001)


def test_timeout_is_visible_and_intent_survives(session):
    crm, remote = CRM(), Remote()
    bridge.sync_one(crm, remote, 1, ["test"])
    def fail(*a, **kw):
        raise PleepControlUncertain("unknown")
    remote.set_enabled = fail
    crm.flag = False
    bridge.sync_one(crm, remote, 1, ["test"])
    assert "не подтверждено" in crm.status
    assert session.get(PleepControlState, 1).data["pending"]["uncertain"]


def test_inflight_manual_edit_is_not_overwritten(session):
    crm, remote = CRM(), Remote()
    bridge.sync_one(crm, remote, 1, ["test"])
    original = remote.set_enabled
    def set_and_user_resumes(*a, **kw):
        original(*a, **kw)
        crm.flag = True
    remote.set_enabled = set_and_user_resumes
    crm.flag = False
    bridge.sync_one(crm, remote, 1, ["test"])
    assert crm.flag is True  # Old OFF must not overwrite a newer user edit.
    assert session.get(PleepControlState, 1).data["pending"]["confirmed"]
