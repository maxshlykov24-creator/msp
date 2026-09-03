from app.config import settings
from app.resolver import resolve_responsible


def test_resolve_open_lead(fake):
    cid = fake.add_contact(phone="+15551234567", created_at=100)
    lid = fake.add_lead(cid, settings.pipeline_id, settings.status_new, responsible=15293564)
    res = resolve_responsible(fake, "+1 555 123 4567")
    assert res["found"] is True
    assert res["lead_id"] == lid
    assert res["responsible_user_id"] == 15293564
    assert res["status"] == "open"


def test_resolve_no_contact(fake):
    res = resolve_responsible(fake, "+15550000000")
    assert res["found"] is False


def test_resolve_bad_phone(fake):
    res = resolve_responsible(fake, "nick")
    assert res["found"] is False
    assert res["reason"] == "bad_phone"
