"""Повторные вебхуки по одному контакту не должны дублировать работу.

Kommo шлёт add_contact / add_lead / update_contact на одно и то же событие, и
раньше каждый повтор снова «переносил» примечания в основную карточку."""
from app.actions import Ctx
from app.config import settings
from app.dedup_contacts import resolve as resolve_contacts
from app.dedup_deals import resolve as resolve_deals


def _ctx(fake, session):
    return Ctx(client=fake, session=session, inbox_id=None, phone="+15551234567", shadow=False)


def test_deal_dedup_runs_once(fake, session, enable_all):
    contact = fake.add_contact(name="A", phone="+15551234567", created_at=100)
    main = fake.add_lead(contact, settings.pipeline_id, settings.status_new, created_at=100)
    dup = fake.add_lead(contact, settings.pipeline_id, settings.status_new, created_at=200)
    fake.notes[("leads", dup)] = [{"note_type": "common", "params": {"text": "звонил"}}]

    ctx = _ctx(fake, session)
    resolve_deals(ctx, contact, new_lead_id=dup, trigger="add_lead")
    after_first = len(fake.notes.get(("leads", main), []))
    resolve_deals(ctx, contact, new_lead_id=dup, trigger="update_contact")

    assert settings.tag_dup_deal in {t["name"] for t in fake.leads[dup]["_embedded"]["tags"]}
    assert fake.leads[dup]["status_id"] == settings.status_lost
    assert len(fake.notes.get(("leads", main), [])) == after_first


def test_contact_merge_runs_once(fake, session, enable_all):
    base = fake.add_contact(name="A", phone="+15551234567", created_at=100)
    dup = fake.add_contact(name="A", phone="+15551234567", created_at=200)
    fake.add_lead(dup, settings.pipeline_id, settings.status_new)

    ctx = _ctx(fake, session)
    pair = [fake.contacts[base], fake.contacts[dup]]
    resolve_contacts(ctx, pair, has_phone=True)
    after_first = len(fake.notes.get(("contacts", base), []))
    res = resolve_contacts(ctx, [fake.contacts[base], fake.contacts[dup]], has_phone=True)

    assert res["action"] == "single" and res["contact_id"] == base
    assert len(fake.notes.get(("contacts", base), [])) == after_first


def test_assignment_skips_other_funnels(fake, session, enable_all):
    """«Сборка» — не воронка продаж: ответственного там ставит свой процесс."""
    from app.assignment import assign_new_lead

    contact = fake.add_contact(name="A", phone="+15551234567")
    lead_id = fake.add_lead(contact, settings.assembly_pipeline_id, 1000, responsible=15293564)

    owner = assign_new_lead(_ctx(fake, session), fake.leads[lead_id], [])

    assert owner == 15293564
    assert fake.leads[lead_id]["responsible_user_id"] == 15293564
