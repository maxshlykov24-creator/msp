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
