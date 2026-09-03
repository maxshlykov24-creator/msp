from app.actions import Ctx
from app.config import settings
from app.dedup_contacts import resolve


def _ctx(fake, session):
    return Ctx(client=fake, session=session, inbox_id=None, phone="+15551234567", shadow=False)


def _tags(fake, contact_id):
    return {t["name"] for t in fake.contacts[contact_id]["_embedded"]["tags"]}


def test_no_chat_soft_merge_marks_younger(fake, session, enable_all):
    base = fake.add_contact(name="A", phone="+15551234567", created_at=100)
    dup = fake.add_contact(name="A", phone="+15551234567", created_at=200)
    lead = fake.add_lead(dup, settings.pipeline_id, settings.status_new)

    res = resolve(_ctx(fake, session), [fake.contacts[base], fake.contacts[dup]], has_phone=True)
    assert res["action"] == "contact_merged"
    # сделка переехала на старший контакт, дубль помечен к удалению (API удалять не умеет)
    assert fake.leads[lead]["_embedded"]["contacts"] == [{"id": base}]
    assert settings.tag_dup_to_delete in _tags(fake, dup)
    assert settings.tag_dup_to_delete not in _tags(fake, base)


def test_manager_comments_move_to_base(fake, session, enable_all):
    """Комментарии с карточки дубля переезжают: саму карточку потом удаляют руками."""
    base = fake.add_contact(name="A", phone="+15551234567", created_at=100)
    dup = fake.add_contact(name="A", phone="+15551234567", created_at=200)
    fake.notes[("contacts", dup)] = [
        {"note_type": "common", "params": {"text": "просил перезвонить в пятницу"}},
    ]
    resolve(_ctx(fake, session), [fake.contacts[base], fake.contacts[dup]], has_phone=True)
    base_notes = " ".join(n["params"]["text"] for n in fake.notes.get(("contacts", base), []))
    assert "просил перезвонить в пятницу" in base_notes


def test_light_chat_merge_copies_messages(fake, session, enable_all):
    base = fake.add_contact(name="A", phone="+15551234567", created_at=100)
    dup = fake.add_contact(name="A", phone="+15551234567", created_at=200)
    fake.notes[("contacts", dup)] = [
        {"note_type": "amomessage", "params": {"text": "Здравствуйте", "in": True}},
    ]
    res = resolve(_ctx(fake, session), [fake.contacts[base], fake.contacts[dup]], has_phone=True)
    assert res["action"] == "contact_merged"
    assert settings.tag_dup_to_delete in _tags(fake, dup)
    base_notes = " ".join(n["params"]["text"] for n in fake.notes.get(("contacts", base), []))
    assert "Здравствуйте" in base_notes


def test_rich_chat_manual_not_marked(fake, session, enable_all):
    base = fake.add_contact(name="A", phone="+15551234567", created_at=100)
    dup = fake.add_contact(name="A", phone="+15551234567", created_at=200)
    fake.notes[("contacts", dup)] = [
        {"note_type": "amomessage", "params": {"text": "in", "in": True}},
        {"note_type": "amomessage", "params": {"text": "manager", "in": False}},
    ]
    res = resolve(_ctx(fake, session), [fake.contacts[base], fake.contacts[dup]], has_phone=True)
    assert res["action"] == "manual_rich"
    tags = _tags(fake, dup)
    assert settings.tag_marker_contact in tags
    assert settings.tag_dup_to_delete not in tags


def test_merge_aborts_if_leads_not_moved(fake, session, enable_all):
    """Не переехала сделка — дубль нельзя помечать к удалению, только в ручную очередь."""
    base = fake.add_contact(name="A", phone="+15551234567", created_at=100)
    dup = fake.add_contact(name="A", phone="+15551234567", created_at=200)
    fake.add_lead(dup, settings.pipeline_id, settings.status_new)

    def boom(*args, **kwargs):
        raise RuntimeError("kommo 400 on link")

    fake.link_lead_contact = boom

    res = resolve(_ctx(fake, session), [fake.contacts[base], fake.contacts[dup]], has_phone=True)

    assert res["action"] == "contact_merged"
    tags = _tags(fake, dup)
    assert settings.tag_dup_to_delete not in tags
    assert settings.tag_marker_contact in tags


def test_no_phone_instagram_always_manual(fake, session, enable_all):
    base = fake.add_contact(name="nick", created_at=100)
    dup = fake.add_contact(name="nick", created_at=200)
    res = resolve(_ctx(fake, session), [fake.contacts[base], fake.contacts[dup]], has_phone=False)
    assert res["action"] == "manual_no_phone"
    assert settings.tag_dup_to_delete not in _tags(fake, dup)


def test_shared_number_manual(fake, session, enable_all):
    a = fake.add_contact(name="Ivan", phone="+15551234567", created_at=100)
    b = fake.add_contact(name="Petr", phone="+15551234567", created_at=200)
    for _ in range(4):
        fake.add_lead(a, settings.pipeline_id, settings.status_new)
    res = resolve(_ctx(fake, session), [fake.contacts[a], fake.contacts[b]], has_phone=True)
    assert res["action"] == "manual_shared"
    assert settings.tag_dup_to_delete not in _tags(fake, b)


def test_transliterated_name_is_not_shared_number(fake, session, enable_all):
    """«Evgeniy Koeka» и «Евгений Коека» — один человек, а не общий номер семьи."""
    a = fake.add_contact(name="Evgeniy Koeka", phone="+15551234567", created_at=100)
    b = fake.add_contact(name="Евгений Коека", phone="+15551234567", created_at=200)
    for _ in range(4):
        fake.add_lead(a, settings.pipeline_id, settings.status_new)

    res = resolve(_ctx(fake, session), [fake.contacts[a], fake.contacts[b]], has_phone=True)
    assert res["action"] == "contact_merged"
    assert settings.tag_dup_to_delete in _tags(fake, b)


def test_history_of_closed_leads_does_not_block_merge(fake, session, enable_all):
    """Повторный клиент: 3 закрытых сделки в истории раньше включали «общий номер»."""
    a = fake.add_contact(name="Evgeniy Koeka", phone="+15551234567", created_at=100)
    b = fake.add_contact(name="Евгений Коека", phone="+15551234567", created_at=200)
    for _ in range(3):
        fake.add_lead(a, settings.pipeline_id, settings.status_lost, closed_at=500)

    res = resolve(_ctx(fake, session), [fake.contacts[a], fake.contacts[b]], has_phone=True)
    assert res["action"] == "contact_merged"


def test_resolve_returns_full_group(fake, session, enable_all):
    a = fake.add_contact(name="Ivan", phone="+15551234567", created_at=100)
    b = fake.add_contact(name="Petr", phone="+15551234567", created_at=200)
    for _ in range(4):
        fake.add_lead(a, settings.pipeline_id, settings.status_new)
    res = resolve(_ctx(fake, session), [fake.contacts[a], fake.contacts[b]], has_phone=True)
    assert res["group"] == sorted([a, b])
