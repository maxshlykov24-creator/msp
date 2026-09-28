from app.actions import Ctx
from app.config import settings
from app.intake import process_intake


def _ctx(fake, session):
    return Ctx(client=fake, session=session, inbox_id=None, phone="+15551234567", shadow=False)


def test_new_phone_creates_single_lead_ilona(fake, session, enable_all):
    res = process_intake(_ctx(fake, session),
                         {"name": "New", "phone": "+15551234567", "channel": "Tilda"})
    assert res["action"] == "created"
    lead = fake.leads[res["lead_id"]]
    assert lead["responsible_user_id"] == settings.default_sales_owner_id
    assert lead["pipeline_id"] == settings.pipeline_id
    assert fake.contacts[res["contact_id"]]["responsible_user_id"] == settings.default_sales_owner_id


def test_repeat_attaches_note_no_new_lead(fake, session, enable_all):
    cid = fake.add_contact(name="Old", phone="+15551234567", created_at=100)
    existing = fake.add_lead(cid, settings.pipeline_id, settings.status_new, created_at=100)
    before = len(fake.leads)

    res = process_intake(_ctx(fake, session),
                         {"name": "Old", "phone": "+15551234567", "channel": "Tilda",
                          "notes": "снова написал"})
    assert res["action"] == "repeat"
    assert res["lead_id"] == existing
    assert len(fake.leads) == before  # новую сделку не создали
    notes = " ".join(n["params"]["text"] for n in fake.notes.get(("leads", existing), []))
    assert "Повторное обращение" in notes


def _emails(fake, contact_id: int) -> list[str]:
    out = []
    for cf in fake.contacts[contact_id].get("custom_fields_values") or []:
        if cf.get("field_id") == settings.field_email:
            out.extend(v.get("value") for v in cf.get("values") or [])
    return out


def test_new_lead_stores_email(fake, session, enable_all):
    res = process_intake(_ctx(fake, session), {
        "name": "New", "phone": "+15551234567", "email": "a@b.co", "channel": "Tilda",
    })
    assert _emails(fake, res["contact_id"]) == ["a@b.co"]


def test_repeat_fills_missing_email(fake, session, enable_all):
    cid = fake.add_contact(name="Old", phone="+15551234567")
    fake.add_lead(cid, settings.pipeline_id, settings.status_new)
    res = process_intake(_ctx(fake, session), {
        "name": "Old", "phone": "+15551234567", "email": "a@b.co", "channel": "Tilda",
    })
    assert res["action"] == "repeat"
    assert _emails(fake, cid) == ["a@b.co"]


def test_repeat_keeps_existing_email(fake, session, enable_all):
    cid = fake.add_contact(name="Old", phone="+15551234567",
                           **{str(settings.field_email): "a@b.co"})
    fake.add_lead(cid, settings.pipeline_id, settings.status_new)
    before = list(fake.contacts[cid]["custom_fields_values"])
    process_intake(_ctx(fake, session), {
        "name": "Old", "phone": "+15551234567", "email": "A@b.co", "channel": "Tilda",
    })
    assert fake.contacts[cid]["custom_fields_values"] == before
