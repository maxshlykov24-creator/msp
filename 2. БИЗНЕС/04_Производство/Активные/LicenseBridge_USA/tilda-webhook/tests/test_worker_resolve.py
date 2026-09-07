"""Резолв контакта в worker: случаи, найденные на боевой базе 2026-07-24."""
from app.actions import Ctx
from app.config import settings
from app.worker import _resolve_contact


def _contact_with_phones(fake, phones, **kw):
    cf = [{"field_id": settings.field_phone,
           "values": [{"value": p, "enum_id": settings.field_phone_enum_work} for p in phones]}]
    return fake.add_contact(cfs=cf, **kw)


def _tags(fake, contact_id):
    return {t["name"] for t in fake.contacts[contact_id]["_embedded"]["tags"]}


def test_duplicate_on_second_phone_is_found(fake, session, enable_all):
    """Дубль висит на втором номере карточки — проверять только первый недостаточно."""
    base = _contact_with_phones(fake, ["+18189162603", "+18189162617"],
                                name="Gevorg", created_at=100)
    dup = fake.add_contact(name="", phone="+18189162617", created_at=200)

    ctx = Ctx(client=fake, session=session, inbox_id=None, phone="+18189162617", shadow=False)
    final, has_phone, group = _resolve_contact(ctx, fake.contacts[base])

    assert has_phone and final == base
    assert group == sorted([base, dup])
    assert settings.tag_dup_to_delete in _tags(fake, dup)


def test_stub_phone_does_not_group_autocontacts(fake, session, enable_all):
    """«Autocontact +1»: одинаковый огрызок номера не делает карточки дублями."""
    a = fake.add_contact(name="Autocontact +1", phone="+1", created_at=100)
    b = fake.add_contact(name="Autocontact +1", phone="+1", created_at=200)

    ctx = Ctx(client=fake, session=session, inbox_id=None, phone=None, shadow=False)
    final, has_phone, _group = _resolve_contact(ctx, fake.contacts[b])

    assert not has_phone and final == b
    assert settings.tag_dup_to_delete not in _tags(fake, a)
    assert settings.tag_marker_contact not in _tags(fake, a)
