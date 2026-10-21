"""Тесты коллтрекинга. База только bridge_test, см. conftest."""

from __future__ import annotations

import os

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg2://postgres:@/bridge_test?host=/private/tmp/mansband-ct-pg",
)
os.environ["CT_LIVE"] = "true"
os.environ["CT_RECONCILE_ENABLED"] = "false"
os.environ["TALKME_POLL_ENABLED"] = "false"
os.environ["CT_POOL_NUMBERS"] = ",".join(f"931000{i:04d}" for i in range(15))
os.environ["CT_ORGANIC_NUMBER"] = "9310090251"
os.environ["CT_CHANNEL_NUMBERS"] = "9311111111=Авито;9312222222=2ГИС"
os.environ["CT_ORIGINAL_NUMBER"] = "9310090251"
os.environ["CT_UTM_SOURCE_MAP"] = "yandex=Яндекс Директ"
os.environ["CT_HOLD_MINUTES"] = "15"
os.environ["CT_VISIT_ID_BASE"] = "900000000"
os.environ["TELPHIN_WEBHOOK_SECRET"] = "telphin-test"
os.environ["INTERNAL_SECRET"] = "test-secret"
os.environ["BRIDGE_PUBLIC_BASE"] = "https://mansband-chat-bridge.twc1.net"
os.environ["AMO_SUBDOMAIN"] = "mansband"

import pytest
from sqlalchemy import text

_TABLES = (
    "ct_assignment",
    "ct_call",
    "ct_visit",
    "ct_number",
    "conversation_map",
    "processed_event",
    "kv_state",
)


@pytest.fixture(scope="session", autouse=True)
def _database():
    url = os.environ["DATABASE_URL"]
    if "bridge_test" not in url:
        raise RuntimeError("отказ: тесты только на bridge_test")
    from app.config import get_settings

    get_settings.cache_clear()
    from app.database import get_engine, init_db

    eng = get_engine()
    with eng.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS ct_assignment, ct_call, ct_number, ct_visit CASCADE"))
    init_db()
    yield


@pytest.fixture(autouse=True)
def _clean():
    from app.calltrack.routes import _hits
    from app.database import get_engine

    _hits.clear()
    eng = get_engine()
    with eng.begin() as conn:
        conn.execute(text("TRUNCATE " + ", ".join(_TABLES) + " RESTART IDENTITY CASCADE"))
    yield
    _hits.clear()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as c:
        yield c


def _db():
    from app.database import get_session_factory

    return get_session_factory()()


ORIGIN = {"Origin": "https://mansband.ru"}
PAID_URL = (
    "https://mansband.ru/?utm_source=yandex&utm_medium=cpc"
    "&utm_campaign=brand&utm_content=ad&utm_term=suit&yclid=abc"
)


def test_01_paid_visit_keeps_number(client):
    first = client.post("/ct/session", headers=ORIGIN, json={"url": PAID_URL, "referrer": "https://yandex.ru"})
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["mode"] == "pool"
    assert body["visit_id"] >= 900_000_000
    assert body["number"].startswith("+7")
    second = client.post(
        "/ct/session",
        headers=ORIGIN,
        json={"visit_id": body["visit_id"], "url": "https://mansband.ru/catalog"},
    )
    assert second.status_code == 200
    assert second.json()["number"] == body["number"]
    assert second.json()["visit_id"] == body["visit_id"]


def test_02_organic_creates_visit_without_pool(client):
    r = client.post("/ct/session", headers=ORIGIN, json={"url": "https://mansband.ru/", "referrer": ""})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "organic"
    assert body["number"] == "+79310090251"
    assert body["visit_id"]
    db = _db()
    try:
        from app.calltrack.models import Visit

        visit = db.get(Visit, body["visit_id"])
        assert visit is not None
        assert visit.is_paid is False
    finally:
        db.close()


def test_03_sixteenth_visit_uses_fallback_without_stealing(client):
    numbers = []
    for i in range(16):
        r = client.post(
            "/ct/session",
            headers=ORIGIN,
            json={"url": f"https://mansband.ru/?utm_source=yandex&utm_campaign=c{i}"},
        )
        assert r.status_code == 200, r.text
        numbers.append(r.json()["number"])
    assert len(set(numbers[:15])) == 15
    assert numbers[15] == "+79310090251"
    assert r.json()["mode"] == "fallback"
    db = _db()
    try:
        from sqlalchemy import func, select

        from app.calltrack.models import Visit
        from app.models import KvState

        disputed = db.execute(select(func.count()).select_from(Visit).where(Visit.disputed.is_(True))).scalar_one()
        assert disputed == 0
        row = db.execute(select(KvState).where(KvState.key.like("ct_pool_exhausted:%"))).scalar_one()
        assert row.value == "1"
    finally:
        db.close()


def test_04_parallel_allocate_does_not_share_without_dispute():
    import threading
    from datetime import datetime, timedelta, timezone

    from app.calltrack.models import Assignment, PoolNumber, Visit
    from app.calltrack.pool import allocate_pool, sync_numbers
    from app.config import get_settings

    db = _db()
    try:
        sync_numbers(db, get_settings())
        now = datetime.now(timezone.utc)
        dummy = Visit(is_paid=True, landing_url="https://mansband.ru/", created_at=now, last_seen_at=now)
        db.add(dummy)
        db.flush()
        numbers = list(db.execute(select_pool()).scalars())
        assert len(numbers) == 15
        for number in numbers[:14]:
            number.held_by_visit_id = dummy.id
            number.held_until = now + timedelta(hours=1)
            db.add(
                Assignment(
                    number_id=number.id,
                    visit_id=dummy.id,
                    started_at=now,
                    last_seen_at=now,
                )
            )
        numbers[14].held_by_visit_id = None
        numbers[14].held_until = None
        v1 = Visit(is_paid=True, created_at=now, last_seen_at=now)
        v2 = Visit(is_paid=True, created_at=now, last_seen_at=now)
        db.add_all([v1, v2])
        db.commit()
        ids = [v1.id, v2.id]
    finally:
        db.close()

    barrier = threading.Barrier(2)
    out: list[str | None] = []
    errors: list[BaseException] = []

    def worker(vid: int) -> None:
        session = _db()
        try:
            barrier.wait(5)
            visit = session.get(Visit, vid)
            number = allocate_pool(session, visit)
            session.commit()
            out.append(None if number is None else number.number)
        except BaseException as exc:
            session.rollback()
            errors.append(exc)
        finally:
            session.close()

    from app.calltrack.models import Visit

    threads = [threading.Thread(target=worker, args=(vid,)) for vid in ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert not errors
    assert len(out) == 2
    assert sum(number is not None for number in out) == 1
    check = _db()
    try:
        assert not any(check.get(Visit, vid).disputed for vid in ids)
    finally:
        check.close()


def select_pool():
    from sqlalchemy import select

    from app.calltrack.models import PoolNumber

    return select(PoolNumber).where(PoolNumber.kind == "pool").order_by(PoolNumber.id)


def test_05_ping_extends_and_expired_hold_is_free(client):
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import select

    from app.calltrack.models import Assignment, PoolNumber

    first = client.post("/ct/session", headers=ORIGIN, json={"url": PAID_URL}).json()
    db = _db()
    try:
        number = db.execute(select(PoolNumber).where(PoolNumber.held_by_visit_id == first["visit_id"])).scalar_one()
        number.held_until = datetime.now(timezone.utc) + timedelta(minutes=1)
        db.commit()
        number_id = number.id
        shown = number.number
    finally:
        db.close()
    ping = client.post("/ct/ping", headers=ORIGIN, json={"visit_id": first["visit_id"]})
    assert ping.status_code == 200
    assert ping.json()["number"] == first["number"]
    db = _db()
    try:
        number = db.get(PoolNumber, number_id)
        assert number.held_until > datetime.now(timezone.utc) + timedelta(minutes=10)
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        others = db.execute(
            select(PoolNumber).where(PoolNumber.kind == "pool", PoolNumber.id != number_id)
        ).scalars()
        for other in others:
            other.held_until = future
            other.held_by_visit_id = first["visit_id"]
        past = datetime.now(timezone.utc) - timedelta(minutes=16)
        number.held_until = datetime.now(timezone.utc) - timedelta(minutes=1)
        number.held_by_visit_id = first["visit_id"]
        assignment = db.execute(select(Assignment).where(Assignment.visit_id == first["visit_id"])).scalar_one()
        assignment.last_seen_at = past
        db.commit()
    finally:
        db.close()
    nxt = client.post(
        "/ct/session",
        headers=ORIGIN,
        json={"url": "https://mansband.ru/?utm_source=vk"},
    )
    assert nxt.status_code == 200
    assert nxt.json()["number"] == "+7" + shown
    db = _db()
    try:
        from app.calltrack.models import Visit

        visit = db.get(Visit, nxt.json()["visit_id"])
        assert visit.disputed is False
    finally:
        db.close()


def test_06_call_during_hold_finds_visit():
    from datetime import datetime, timedelta, timezone

    from app.calltrack.matching import visit_for_call
    from app.calltrack.models import Assignment, CallEvent, PoolNumber, Visit

    now = datetime.now(timezone.utc)
    db = _db()
    try:
        visit = Visit(utm_source="yandex", is_paid=True, created_at=now, last_seen_at=now)
        db.add(visit)
        db.flush()
        number = PoolNumber(number="9310000000", kind="pool", active=True, held_by_visit_id=visit.id, held_until=now + timedelta(minutes=15))
        db.add(number)
        db.flush()
        db.add(Assignment(number_id=number.id, visit_id=visit.id, started_at=now, last_seen_at=now))
        call = CallEvent(call_id="c6", called="9310000000", caller="9001110006", started_at=now + timedelta(minutes=2), status="new")
        db.add(call)
        db.commit()
        found, disputed = visit_for_call(db, call)
        assert found is not None and found.id == visit.id
        assert disputed is False
    finally:
        db.close()


def test_07_call_during_first_hold_returns_first_visit():
    from datetime import datetime, timedelta, timezone

    from app.calltrack.matching import visit_for_call
    from app.calltrack.models import Assignment, CallEvent, PoolNumber, Visit

    t0 = datetime.now(timezone.utc) - timedelta(minutes=10)
    db = _db()
    try:
        first = Visit(utm_source="yandex", is_paid=True, created_at=t0, last_seen_at=t0)
        second = Visit(utm_source="vk", is_paid=True, created_at=t0 + timedelta(minutes=6), last_seen_at=t0 + timedelta(minutes=6))
        db.add_all([first, second])
        db.flush()
        number = PoolNumber(number="9310000001", kind="pool", active=True)
        db.add(number)
        db.flush()
        db.add(Assignment(number_id=number.id, visit_id=first.id, started_at=t0, last_seen_at=t0))
        db.add(
            Assignment(
                number_id=number.id,
                visit_id=second.id,
                started_at=t0 + timedelta(minutes=6),
                last_seen_at=t0 + timedelta(minutes=6),
            )
        )
        call = CallEvent(
            call_id="c7",
            called="9310000001",
            caller="9001110007",
            started_at=t0 + timedelta(minutes=3),
            status="new",
        )
        db.add(call)
        db.commit()
        found, _ = visit_for_call(db, call)
        assert found is not None and found.id == first.id
    finally:
        db.close()


def test_08_yclid_without_utm_is_direct():
    from datetime import datetime, timedelta, timezone

    from app.calltrack.matching import attribute_call
    from app.calltrack.models import Assignment, CallEvent, PoolNumber, Visit

    now = datetime.now(timezone.utc)
    db = _db()
    try:
        visit = Visit(yclid="clid-1", is_paid=True, created_at=now, last_seen_at=now)
        db.add(visit)
        db.flush()
        number = PoolNumber(number="9310000002", kind="pool", active=True)
        db.add(number)
        db.flush()
        db.add(Assignment(number_id=number.id, visit_id=visit.id, started_at=now, last_seen_at=now))
        call = CallEvent(call_id="c8", called="9310000002", caller="9001110008", started_at=now + timedelta(minutes=1), status="new")
        db.add(call)
        db.commit()
        attr = attribute_call(db, call)
        assert attr.tech_source == "Яндекс Директ"
        assert attr.tracking.get("yclid") == "clid-1"
        assert "utm_source" not in attr.tracking
    finally:
        db.close()


def test_09_channel_number_sets_source_without_utm():
    from datetime import datetime, timezone

    from app.calltrack.matching import attribute_call
    from app.calltrack.models import CallEvent
    from app.calltrack.pool import sync_numbers
    from app.config import get_settings

    db = _db()
    try:
        sync_numbers(db, get_settings())
        call = CallEvent(
            call_id="c9",
            called="9311111111",
            caller="9001110009",
            started_at=datetime.now(timezone.utc),
            status="new",
        )
        db.add(call)
        db.commit()
        attr = attribute_call(db, call)
        assert attr.tech_source == "Авито"
        assert attr.tracking == {}
        assert attr.kind == "channel"
    finally:
        db.close()


def test_10_old_call_does_not_match_new_lead():
    from datetime import datetime, timedelta, timezone

    from app.calltrack.matching import match_call_for_lead
    from app.calltrack.models import CallEvent

    now = datetime.now(timezone.utc)
    db = _db()
    try:
        db.add(
            CallEvent(
                call_id="old",
                called="9310000000",
                caller="9005556600",
                started_at=now - timedelta(hours=3),
                status="new",
            )
        )
        recent = CallEvent(
            call_id="recent",
            called="9310000000",
            caller="9005556677",
            started_at=now - timedelta(minutes=1),
            status="new",
        )
        db.add(recent)
        db.commit()
        assert match_call_for_lead(db, ["+7 900 555-66-00"], now) is None
        found = match_call_for_lead(db, ["89005556677"], now)
        assert found is not None and found.call_id == "recent"
    finally:
        db.close()


def test_11_call_closer_than_chat_wins():
    from datetime import datetime, timedelta, timezone

    from app.calltrack.matching import decide
    from app.calltrack.models import Assignment, CallEvent, PoolNumber, Visit
    from app.models import ConversationMap

    t0 = datetime.now(timezone.utc)
    db = _db()
    try:
        visit = Visit(
            utm_source="yandex",
            utm_medium="cpc",
            is_paid=True,
            landing_url="https://mansband.ru/?utm_source=yandex",
            created_at=t0,
            last_seen_at=t0,
        )
        db.add(visit)
        db.flush()
        number = PoolNumber(number="9310000003", kind="pool", active=True)
        db.add(number)
        db.flush()
        db.add(Assignment(number_id=number.id, visit_id=visit.id, started_at=t0, last_seen_at=t0))
        db.add(
            CallEvent(
                call_id="c11",
                called="9310000003",
                caller="9001112233",
                started_at=t0 + timedelta(minutes=1),
                status="new",
            )
        )
        db.add(
            ConversationMap(
                scope_id="s",
                external_conversation_id="tmc-11",
                tracking={"utm_source": "chat", "roistat": "111"},
                client_phone="9001112233",
                last_message_at=t0 - timedelta(hours=1),
            )
        )
        db.commit()
        decision = decide(db, phones=["+79001112233"], names=[], lead_created_at=t0 + timedelta(minutes=2))
        assert decision["kind"] == "call"
        assert decision["attr"].tracking["utm_source"] == "yandex"
    finally:
        db.close()


def test_12_chat_without_call_keeps_foreign_roistat():
    from datetime import datetime, timezone

    from app.calltrack.matching import decide, resolve_our_visit
    from app.models import ConversationMap

    db = _db()
    try:
        cm = ConversationMap(
            scope_id="s",
            external_conversation_id="tmc-12",
            tracking={"utm_source": "google", "roistat": "4567890"},
            client_phone="9001112244",
            last_message_at=datetime.now(timezone.utc),
        )
        db.add(cm)
        db.commit()
        decision = decide(db, phones=["9001112244"], names=[], lead_created_at=datetime.now(timezone.utc))
        assert decision["kind"] == "chat"
        resolved = resolve_our_visit(db, decision["conversation"].tracking)
        assert resolved["utm_source"] == "google"
        assert resolved["roistat"] == "4567890"
    finally:
        db.close()


def test_13_filled_fields_are_not_overwritten():
    from app.calltrack.matching import plan_lead_patch

    lead = {
        "custom_fields_values": [
            {"field_id": 1, "values": [{"value": "already"}]},
            {"field_id": 99, "values": [{"value": "Яндекс Директ", "enum_id": 5}]},
        ]
    }
    values, meta = plan_lead_patch(
        tracking={"utm_source": "yandex", "yclid": "abc"},
        tech_source="Авито",
        lead=lead,
        id_by_key={"utm_source": 1, "yclid": 2},
        select_info={"field_id": 99, "enums": {"Яндекс Директ": 5, "Авито": 6}},
    )
    written_ids = {item["field_id"] for item in values}
    assert 1 not in written_ids
    assert 99 not in written_ids
    assert written_ids == {2}
    assert meta["written"] == {"yclid": "abc"}
    assert meta["tech_written"] is True


def test_14_our_visit_id_expands_and_foreign_stays():
    from app.calltrack.matching import resolve_our_visit
    from app.calltrack.models import Visit

    db = _db()
    try:
        db.add(Visit(id=900000123, utm_source="yandex", utm_medium="cpc", yclid="yy", is_paid=True))
        db.commit()
        ours = resolve_our_visit(db, {"roistat": "900000123"})
        assert ours["utm_source"] == "yandex"
        assert ours["utm_medium"] == "cpc"
        assert ours["yclid"] == "yy"
        assert "roistat" not in ours
        foreign = resolve_our_visit(db, {"roistat": "4567890", "utm_source": "google"})
        assert foreign["roistat"] == "4567890"
        assert foreign["utm_source"] == "google"
    finally:
        db.close()


def test_15_origin_rate_limit_and_off_mode(client):
    from app.config import get_settings

    denied = client.post("/ct/session", headers={"Origin": "https://evil.example"}, json={"url": "https://mansband.ru/"})
    assert denied.status_code == 403
    from app.calltrack.routes import _hits

    _hits.clear()
    settings = get_settings()
    old = settings.ct_live
    settings.ct_live = False
    try:
        for _ in range(30):
            r = client.post("/ct/session", headers=ORIGIN, json={"url": "https://mansband.ru/", "test": False})
            assert r.status_code == 200, r.text
            assert r.json()["mode"] == "off"
        blocked = client.post("/ct/session", headers=ORIGIN, json={"url": "https://mansband.ru/", "test": False})
        assert blocked.status_code == 429
    finally:
        settings.ct_live = old


def test_bot_does_not_create_visit(client):
    r = client.post(
        "/ct/session",
        headers={**ORIGIN, "User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1)"},
        json={"url": PAID_URL},
    )
    assert r.status_code == 200
    assert r.json()["mode"] == "organic"
    assert r.json()["visit_id"] is None


def test_script_placeholders(client):
    r = client.get("/ct.js")
    assert r.status_code == 200
    assert "var LIVE = true;" in r.text
    assert "9310090251" in r.text
    assert "mansband-chat-bridge.twc1.net" in r.text
    assert "max-age=60" in r.headers["cache-control"]


def test_telphin_webhook_stores_normalized_and_raw(client):
    from datetime import datetime, timezone

    from sqlalchemy import select

    from app.calltrack.models import CallEvent

    denied = client.post("/webhooks/telphin/nope", json={"call_id": "no"})
    assert denied.status_code == 401
    ok = client.post(
        "/webhooks/telphin/telphin-test",
        json={
            "call_id": "real-1",
            "called": "+7 931 000-00-00",
            "caller": "8 900 111-22-33",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "incoming": True,
        },
    )
    assert ok.status_code == 200
    unknown = client.post(
        "/webhooks/telphin/telphin-test",
        json={"EventType": "dial-in", "SomeId": "abc"},
    )
    assert unknown.status_code == 200
    db = _db()
    try:
        parsed = db.execute(select(CallEvent).where(CallEvent.call_id == "real-1")).scalar_one()
        assert parsed.called == "9310000000"
        assert parsed.caller == "9001112233"
        assert parsed.status == "new"
        raw_rows = db.execute(select(CallEvent).where(CallEvent.call_id.like("raw-%"))).scalars().all()
        assert len(raw_rows) == 1
        assert raw_rows[0].status == "new"
        assert raw_rows[0].called == ""
    finally:
        db.close()


def test_classify_repeat_and_no_lead():
    from datetime import datetime, timedelta, timezone

    from app.calltrack.models import CallEvent
    from app.calltrack.reconcile import classify_call

    now = datetime.now(timezone.utc)
    old = CallEvent(call_id="a", called="9310000000", caller="9000000000", started_at=now - timedelta(minutes=40), status="new")
    fresh = CallEvent(call_id="b", called="9310000000", caller="9000000000", started_at=now - timedelta(minutes=10), status="new")
    assert classify_call(old, [{"id": 1}], now) == "repeat"
    assert classify_call(old, [], now) == "no_lead"
    assert classify_call(fresh, [], now) == "wait"


def test_shared_number_never_claims_organic():
    from app.calltrack.matching import attribute_call
    from app.calltrack.models import CallEvent
    from app.calltrack.pool import sync_numbers
    with _db() as db:
        sync_numbers(db)
        db.commit()
        attr = attribute_call(db, CallEvent(called="9310090251"))
        assert attr.kind == "shared"
        assert attr.tech_source is None and attr.tracking == {}


def test_alert_failure_retry_dedup_recovery(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from app.calltrack import telegram
    from app.models import KvState
    import json
    sent = []
    outcomes = iter([False, True, False, True])
    monkeypatch.setattr(telegram, "send", lambda message: sent.append(message) or next(outcomes))
    now = datetime.now(timezone.utc)
    with _db() as db:
        assert not telegram.incident(db, "test", True, "problem", now)
        db.commit()
        assert telegram.incident(db, "test", True, "problem", now + timedelta(minutes=1))
        db.commit()
        assert not telegram.incident(db, "test", True, "problem", now + timedelta(minutes=2))
        assert len(sent) == 2
        assert not telegram.incident(db, "test", False, "problem", now + timedelta(minutes=3))
        db.commit()
        assert telegram.incident(db, "test", False, "problem", now + timedelta(minutes=4))
        db.commit()
        assert json.loads(db.get(KvState, "ct_alert:test").value) == {}


def test_crm_error_persists_and_reconcile_retries(monkeypatch):
    from datetime import datetime, timedelta, timezone
    import httpx
    from app import amo_v4, tokens
    from app.calltrack.matching import apply_call
    from app.calltrack.models import CallEvent
    from app.calltrack.pool import sync_numbers
    from app.calltrack.reconcile import run_reconcile_once
    monkeypatch.setattr(tokens, "get_valid_access_token", lambda db: "test")
    monkeypatch.setattr(amo_v4, "field_id_by_key", lambda **kw: {})
    monkeypatch.setattr(amo_v4, "select_field", lambda **kw: {"field_id": 99, "enums": {"Авито": 5}})
    lead = {"id": 123, "custom_fields_values": []}
    monkeypatch.setattr(amo_v4, "get_lead", lambda **kw: lead)
    responses = iter([500, 200])
    monkeypatch.setattr(amo_v4, "patch_lead_fields", lambda **kw: httpx.Response(next(responses), request=httpx.Request("PATCH", "https://example.test")))
    notes = []
    monkeypatch.setattr(amo_v4, "add_lead_note", lambda **kw: notes.append(kw))
    with _db() as db:
        sync_numbers(db)
        call = CallEvent(call_id="retry-test", called="9311111111", caller="9000000000",
                         started_at=datetime.now(timezone.utc) - timedelta(minutes=6), status="new")
        db.add(call)
        db.commit()
        assert not apply_call(db, call, 123, lead, "test")["ok"]
        cid = call.id
        assert call.status == "retry" and call.lead_id == 123
    with _db() as db:
        result = run_reconcile_once(db)
        call = db.get(CallEvent, cid)
        assert result["retry"] == 1
        assert call.status == "matched"
        assert call.result["attempts"] == 2
        assert len(notes) == 1


def test_summary_delivery_is_retried(monkeypatch):
    from datetime import datetime, timezone
    from app.calltrack import reconcile
    monkeypatch.setattr(reconcile, "summary_text", lambda *args: "summary")
    outcomes = iter([False, True])
    monkeypatch.setattr(reconcile, "send", lambda message: next(outcomes))
    now = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)
    with _db() as db:
        assert not reconcile.maybe_summary(db, now)
        assert "last_hour" not in reconcile._load_state(db)
        assert reconcile.maybe_summary(db, now)
        assert reconcile._load_state(db)["last_hour"]


def test_monitor_detects_exhaustion_and_persists_heartbeat(client, monkeypatch):
    from app.calltrack import reconcile, telegram
    from app.calltrack.routes import _hits
    messages = []
    monkeypatch.setattr(telegram, "send", lambda message: messages.append(message) or True)
    for _ in range(16):
        _hits.clear()
        assert client.post("/ct/session", headers=ORIGIN, json={"url": PAID_URL}).status_code == 200
    with _db() as db:
        reconcile.monitor_once(db)
        assert any("Не хватает номеров" in message for message in messages)
        assert reconcile._load_state(db)["heartbeat"]
        report = reconcile.summary_text(db)
        assert "активных рекламных визитов: 16" in report
        assert "Ответов с резервным номером: 1" in report


def test_crm_retries_stop_after_six_failures(monkeypatch):
    from datetime import datetime, timezone
    from app import amo_v4
    from app.calltrack.matching import apply_call
    from app.calltrack.models import CallEvent
    monkeypatch.setattr(amo_v4, "field_id_by_key", lambda **kw: (_ for _ in ()).throw(RuntimeError("down")))
    with _db() as db:
        call = CallEvent(call_id="failed", called="9311111111", caller="9000000000", started_at=datetime.now(timezone.utc))
        db.add(call); db.commit()
        for _ in range(6):
            assert not apply_call(db, call, 123, {"id": 123}, "test")["ok"]
        assert call.status == "error"
        assert call.result["attempts"] == 6


def test_failed_api_request_is_counted(client, monkeypatch):
    from fastapi import HTTPException
    from app.calltrack import routes
    from app.models import KvState
    def fail(*args):
        raise HTTPException(status_code=503, detail="test")
    monkeypatch.setattr(routes, "_issue", fail)
    assert client.post("/ct/session", headers=ORIGIN, json={"url": PAID_URL}).status_code == 503
    with _db() as db:
        assert db.get(KvState, "ct_api_errors").value == "1"


def test_health_checks_monitor_liveness(client, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from app.config import get_settings
    from app.calltrack.reconcile import _save_state
    monkeypatch.setattr(get_settings(), "ct_reconcile_enabled", True)
    assert client.get("/ct/health").status_code == 503
    with _db() as db:
        _save_state(db, {"heartbeat": datetime.now(timezone.utc).isoformat()})
        db.commit()
    assert client.get("/ct/health").status_code == 200
    with _db() as db:
        _save_state(db, {"heartbeat": (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat()})
        db.commit()
    assert client.get("/ct/health").status_code == 503


def test_ten_number_pool_falls_back_on_eleventh(client, monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "ct_pool_numbers", ",".join(f"931000{i:04d}" for i in range(10)))
    issued = [client.post("/ct/session", headers=ORIGIN, json={"url": PAID_URL}).json() for _ in range(11)]
    assert len({r["number"] for r in issued[:10]}) == 10
    assert all(r["mode"] == "pool" for r in issued[:10])
    assert issued[10]["mode"] == "fallback"
    assert issued[10]["number"] == "+79310090251"


def test_telphin_urlencoded_preserves_real_event_fields(client):
    from sqlalchemy import select
    from app.calltrack.models import CallEvent
    payload = {"EventType": "dial-in", "CallID": "form-event-1", "CalledDID": "+79310000000", "Empty": ""}
    response = client.post("/webhooks/telphin/telphin-test", data=payload)
    assert response.status_code == 200
    with _db() as db:
        event = db.execute(select(CallEvent)).scalar_one()
        assert event.raw == payload


def test_telphin_invalid_body_not_acknowledged(client):
    path = "/webhooks/telphin/telphin-test"
    assert client.post(path, content="{", headers={"Content-Type": "application/json"}).status_code == 400
    assert client.post(path, content="broken", headers={"Content-Type": "application/x-www-form-urlencoded"}).status_code == 400
    assert client.post(path, content="unhandled", headers={"Content-Type": "text/plain"}).status_code == 415


def test_native_telphin_dial_in_microseconds_and_did():
    from app.calltrack.telphin import parse_telphin
    from datetime import datetime, timezone
    raw = {"EventType": "dial-in", "CallFlow": "in", "CallID": "native-test-id", "CalledDID": "+79310000000", "CalledNumber": "account*102", "CallerIDNum": "+79001112233", "EventTime": "1792598074745888"}
    result = parse_telphin(raw)
    assert result["parsed"] is True
    assert result["called"] == "9310000000"
    assert result["caller"] == "9001112233"
    assert result["started_at"] == datetime(2026, 10, 21, 15, 54, 34, 745888, tzinfo=timezone.utc)
    assert parse_telphin({**raw, "CallFlow": "out"})["incoming"] is False
    assert parse_telphin({**raw, "EventTime": "invalid"})["parsed"] is False
    assert parse_telphin({**raw, "CalledDID": ""})["parsed"] is False
