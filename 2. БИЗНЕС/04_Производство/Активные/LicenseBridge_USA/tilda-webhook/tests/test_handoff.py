"""Handoff Pipeline → Сборка: won создаёт новую сделку в производстве, сохраняя
контакт и доп.поля; backdate created_at — чтобы лента новой карточки
подтягивала прежние звонки/чаты (см. app/handoff.py)."""
from app.actions import Ctx
from app.config import settings
from app.dedup_deals import resolve as resolve_deals
from app.handoff import run_handoff
from app.models import Handoff


def _ctx(fake, session, shadow=False):
    return Ctx(client=fake, session=session, inbox_id=None, phone="+15551234567", shadow=shadow)


def _tags(fake, lead_id):
    return {t["name"] for t in fake.leads[lead_id]["_embedded"]["tags"]}


def test_won_creates_assembly_lead_with_backdate_and_fields(fake, session, enable_handoff):
    cid = fake.add_contact(phone="+15551234567")
    cf = [
        {"field_id": settings.field_channel, "values": [{"value": "Facebook"}]},
        {"field_id": settings.field_ttad_id, "values": [{"value": "не должно копироваться"}]},
    ]
    lead = fake.add_lead(cid, settings.pipeline_id, settings.status_won,
                         created_at=12345, cfs=cf, price=900)

    result = run_handoff(_ctx(fake, session), lead)

    assert result["action"] == "handoff.created"
    assembly_id = result["assembly_lead"]
    asm = fake.leads[assembly_id]
    assert asm["pipeline_id"] == settings.assembly_pipeline_id
    assert asm["status_id"] == settings.assembly_status_start
    assert asm["responsible_user_id"] == settings.handoff_owner_id
    assert asm["created_at"] == 12345  # backdate — лента подтянет старые чаты/звонки
    assert asm["price"] == 900

    copied_fields = {cf["field_id"] for cf in asm["custom_fields_values"]}
    assert settings.field_channel in copied_fields
    assert settings.field_ttad_id not in copied_fields  # type=tracking_data — не копируем

    assert settings.handoff_tag in _tags(fake, assembly_id)
    # перекрёстные ссылки в обе стороны
    source_notes = " ".join(n["params"]["text"] for n in fake.notes.get(("leads", lead), []))
    assembly_notes = " ".join(n["params"]["text"] for n in fake.notes.get(("leads", assembly_id), []))
    assert f"#{assembly_id}" in source_notes
    assert f"#{lead}" in assembly_notes

    # запись в реестре для идемпотентности
    reg = session.query(Handoff).filter(Handoff.source_lead_id == lead).one()
    assert reg.assembly_lead_id == assembly_id and reg.shadow is False


def test_second_won_call_is_idempotent(fake, session, enable_handoff):
    cid = fake.add_contact(phone="+15551234567")
    lead = fake.add_lead(cid, settings.pipeline_id, settings.status_won, created_at=100)

    first = run_handoff(_ctx(fake, session), lead)
    second = run_handoff(_ctx(fake, session), lead)

    assert first["action"] == "handoff.created"
    assert second["action"] == "handoff.already"
    assert second["assembly_lead"] == first["assembly_lead"]
    assembly_leads = [l for l in fake.leads.values()
                      if l["pipeline_id"] == settings.assembly_pipeline_id]
    assert len(assembly_leads) == 1


def test_status_142_in_assembly_pipeline_is_not_a_loop(fake, session, enable_handoff):
    """«Экзамен назначен» в Сборке тоже status_id=142 — без guard по
    pipeline_id это плодило бы сделки бесконечно."""
    cid = fake.add_contact(phone="+15551234567")
    lead = fake.add_lead(cid, settings.assembly_pipeline_id, settings.status_won, created_at=100)

    result = run_handoff(_ctx(fake, session), lead)

    assert result["action"] == "handoff.skip_pipeline"
    assert len(fake.leads) == 1


def test_non_won_status_is_skipped_silently(fake, session, enable_handoff):
    cid = fake.add_contact(phone="+15551234567")
    lead = fake.add_lead(cid, settings.pipeline_id, settings.status_new, created_at=100)

    result = run_handoff(_ctx(fake, session), lead)

    assert result["action"] == "handoff.skip_status"
    assert len(fake.leads) == 1


def test_shadow_mode_does_not_create(fake, session, enable_handoff):
    cid = fake.add_contact(phone="+15551234567")
    lead = fake.add_lead(cid, settings.pipeline_id, settings.status_won, created_at=100)

    result = run_handoff(_ctx(fake, session, shadow=True), lead)

    assert result["action"] == "handoff.would_create"
    assert len(fake.leads) == 1
    assert session.query(Handoff).count() == 0


def test_disabled_flag_does_not_create(fake, session, monkeypatch):
    """enable_handoff=False — фича выключена вне авто-раскатки, даже если
    shadow=False. Флаг гасим явно: на боевом он включён и приезжает из .env."""
    monkeypatch.setattr(settings, "enable_handoff", False)
    cid = fake.add_contact(phone="+15551234567")
    lead = fake.add_lead(cid, settings.pipeline_id, settings.status_won, created_at=100)

    result = run_handoff(_ctx(fake, session, shadow=False), lead)

    assert result["action"] == "handoff.would_create"
    assert len(fake.leads) == 1


def test_dup_tagged_lead_is_skipped(fake, session, enable_handoff):
    cid = fake.add_contact(phone="+15551234567")
    lead = fake.add_lead(cid, settings.pipeline_id, settings.status_won,
                         created_at=100, tags=[settings.tag_dup_deal])

    result = run_handoff(_ctx(fake, session), lead)

    assert result["action"] == "handoff.skip_dup_tagged"
    assert len(fake.leads) == 1


def test_notes_and_calls_are_carried_over(fake, session, enable_handoff):
    cid = fake.add_contact(phone="+15551234567")
    lead = fake.add_lead(cid, settings.pipeline_id, settings.status_won, created_at=100)
    fake.notes[("leads", lead)] = [
        {"note_type": "common", "params": {"text": "клиент оплатил 50%"}, "created_at": 90},
        {"note_type": "call_in", "params": {"text": "звонок клиенту", "duration": "120"}},
    ]

    result = run_handoff(_ctx(fake, session), lead)
    assembly_id = result["assembly_lead"]
    notes = fake.notes.get(("leads", assembly_id), [])

    assert any(n["params"]["text"] == "клиент оплатил 50%" for n in notes)
    assert any("звонок клиенту" in n["params"]["text"] for n in notes if "Дайджест" in n["params"]["text"])


def test_dedup_deals_does_not_merge_handoff_leads(fake, session, enable_all):
    """Две законные handoff-сделки одного контакта (повторная продажа) — не
    дубли: антидубль не должен помечать ни одну из них."""
    cid = fake.add_contact(phone="+15551234567")
    first = fake.add_lead(cid, settings.assembly_pipeline_id, settings.assembly_status_start,
                          created_at=100, tags=[settings.handoff_tag])
    second = fake.add_lead(cid, settings.assembly_pipeline_id, settings.assembly_status_start,
                           created_at=200, tags=[settings.handoff_tag])

    res = resolve_deals(_ctx(fake, session), cid, None, trigger="update_contact")

    assert res["action"] == "deal_noop"
    assert settings.tag_dup_deal not in _tags(fake, first)
    assert settings.tag_dup_deal not in _tags(fake, second)
