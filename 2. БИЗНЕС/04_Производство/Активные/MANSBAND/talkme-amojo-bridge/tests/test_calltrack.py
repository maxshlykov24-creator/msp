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
