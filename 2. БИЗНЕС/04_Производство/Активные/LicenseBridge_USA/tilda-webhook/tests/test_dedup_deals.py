from app.config import settings
from app.dedup_deals import resolve


def _ctx(fake, session):
    from app.actions import Ctx
    return Ctx(client=fake, session=session, inbox_id=None, phone="+15551234567", shadow=False)


def _tags(fake, lead_id):
    return {t["name"] for t in fake.leads[lead_id]["_embedded"]["tags"]}


def _lost(fake, lead_id):
    lead = fake.leads[lead_id]
    return (lead["status_id"] == settings.status_lost
            and lead.get("loss_reason_id") == settings.dup_lost_reason_id)


def test_duplicate_same_funnel_transfer_and_mark(fake, session, enable_all):
    cid = fake.add_contact(phone="+15551234567")
    main = fake.add_lead(cid, settings.pipeline_id, settings.status_new, created_at=100)
    dup = fake.add_lead(cid, settings.pipeline_id, settings.status_new, created_at=200)
    fake.notes[("leads", dup)] = [{"note_type": "common", "params": {"text": "важное"}}]

    res = resolve(_ctx(fake, session), cid, None, trigger="update_contact")
    assert res["action"] == "deal_dedup"
    # дубль уходит в «Провал» с причиной, основная остаётся в работе
    assert settings.tag_dup_deal in _tags(fake, dup)
    assert _lost(fake, dup)
    assert settings.tag_dup_deal not in _tags(fake, main)
    assert fake.leads[main]["status_id"] == settings.status_new
    # текст перенесён в основную
    main_notes = " ".join(n["params"]["text"] for n in fake.notes.get(("leads", main), []))
    assert "важное" in main_notes


def test_duplicate_moves_to_archive_pipeline_when_configured(fake, session, enable_all,
                                                             monkeypatch):
    monkeypatch.setattr(settings, "archive_pipeline_id", 99001)
    monkeypatch.setattr(settings, "archive_status_id", 99002)
    cid = fake.add_contact(phone="+15551234567")
    fake.add_lead(cid, settings.pipeline_id, settings.status_new, created_at=100)
    dup = fake.add_lead(cid, settings.pipeline_id, settings.status_new, created_at=200)

    resolve(_ctx(fake, session), cid, None, trigger="update_contact")
    assert fake.leads[dup]["pipeline_id"] == 99001
    assert fake.leads[dup]["status_id"] == 99002


def test_talk_on_dup_still_goes_to_lost_with_hint(fake, session, enable_all):
    """Переписка на дубле больше не блокирует разбор (сделка не удаляется, а
    закрывается), но в основной остаётся пометка «проверь переписку»."""
    cid = fake.add_contact(phone="+15551234567")
    main = fake.add_lead(cid, settings.pipeline_id, settings.status_new, created_at=100)
    dup = fake.add_lead(cid, settings.pipeline_id, settings.status_new, created_at=200)
    fake.talks[dup] = [{"id": 1}]  # живой talk на дубле

    resolve(_ctx(fake, session), cid, None, trigger="update_contact")

    assert _lost(fake, dup)
    main_notes = " ".join(n["params"]["text"] for n in fake.notes.get(("leads", main), []))
    assert "переписка" in main_notes


def test_cross_funnel_fold_at_creation(fake, session, enable_all):
    cid = fake.add_contact(phone="+15551234567")
    assembly = fake.add_lead(cid, settings.assembly_pipeline_id, 93231787, created_at=50)
    new_lead = fake.add_lead(cid, settings.pipeline_id, settings.status_new, created_at=300)

    res = resolve(_ctx(fake, session), cid, new_lead_id=new_lead, trigger="add_lead")
    assert res["action"] == "cross_funnel_fold"
    assert settings.tag_dup_deal in _tags(fake, new_lead)
    assert _lost(fake, new_lead)
    assert settings.tag_dup_deal not in _tags(fake, assembly)


def test_unmerged_contacts_of_one_person_are_deduped(fake, session, enable_all):
    """Боевой случай 11.08 (сделки 29755601 / 29807229): карточки одного номера не
    склеились. Имя одно (транслитерация) → сделки всё равно дубли."""
    old = fake.add_contact(name="Evgeniy Koeka", phone="+15551234567", created_at=100)
    new = fake.add_contact(name="Евгений Коека", phone="+15551234567", created_at=200)
    main = fake.add_lead(old, settings.pipeline_id, 106419976, created_at=100)
    dup = fake.add_lead(new, settings.pipeline_id, settings.status_new, created_at=200)

    res = resolve(_ctx(fake, session), old, None, trigger="update_contact",
                  group_contact_ids=[old, new])

    assert res["action"] == "deal_dedup"
    assert _lost(fake, dup)
    assert settings.tag_dup_deal in _tags(fake, dup)
    assert fake.leads[main]["status_id"] == 106419976


def test_four_duplicate_cards_leave_one_open_deal(fake, session, enable_all):
    """4 карточки-дубля одного человека, на каждой своя открытая сделка."""
    ids = [fake.add_contact(name=n, phone="+15551234567", created_at=100 + i * 10)
           for i, n in enumerate(["Иван Петров", "Ivan Petrov", "иван петров", ""])]
    main = fake.add_lead(ids[0], settings.pipeline_id, 96943275, created_at=100)
    dups = [fake.add_lead(cid, settings.pipeline_id, settings.status_new,
                          created_at=200 + i)
            for i, cid in enumerate(ids[1:])]

    resolve(_ctx(fake, session), ids[0], None, trigger="update_contact",
            group_contact_ids=ids)

    assert fake.leads[main]["status_id"] == 96943275
    assert all(_lost(fake, lid) for lid in dups)


def test_different_names_on_shared_number_go_manual(fake, session, enable_all):
    """Разные имена на одном номере (семья/офис) — не схлопывать, только тег пары."""
    a = fake.add_contact(name="Иван Петров", phone="+15551234567", created_at=100)
    b = fake.add_contact(name="Мария Сидорова", phone="+15551234567", created_at=200)
    la = fake.add_lead(a, settings.pipeline_id, 96943275, created_at=100)
    lb = fake.add_lead(b, settings.pipeline_id, settings.status_new, created_at=200)

    resolve(_ctx(fake, session), a, None, trigger="update_contact", group_contact_ids=[a, b])

    for lid in (la, lb):
        assert settings.tag_marker_deal in _tags(fake, lid)
        assert settings.tag_dup_deal not in _tags(fake, lid)
        assert fake.leads[lid]["status_id"] != settings.status_lost


def test_main_is_the_lead_that_went_further(fake, session, enable_all):
    """Старая сделка стоит в «Новой заявке», новая ушла в «В работе» → главная новая."""
    cid = fake.add_contact(phone="+15551234567")
    stale = fake.add_lead(cid, settings.pipeline_id, settings.status_new, created_at=100)
    advanced = fake.add_lead(cid, settings.pipeline_id, 106419976, created_at=200)

    resolve(_ctx(fake, session), cid, None, trigger="update_contact")

    assert _lost(fake, stale)
    assert fake.leads[advanced]["status_id"] == 106419976


def test_single_contact_pair_still_auto_resolved(fake, session, enable_all):
    """Проверка «разные карточки» не должна ломать обычный случай одной карточки."""
    cid = fake.add_contact(phone="+15551234567")
    fake.add_lead(cid, settings.pipeline_id, settings.status_new, created_at=100)
    dup = fake.add_lead(cid, settings.pipeline_id, settings.status_new, created_at=200)

    resolve(_ctx(fake, session), cid, None, trigger="update_contact", group_contact_ids=[cid])
    assert _lost(fake, dup)


def test_parallel_pipeline_assembly_not_merged(fake, session, enable_all):
    cid = fake.add_contact(phone="+15551234567")
    asm = fake.add_lead(cid, settings.assembly_pipeline_id, 93231787, created_at=50)
    # pipeline продвинулся дальше «Новой заявки»
    pipe = fake.add_lead(cid, settings.pipeline_id, 96943275, created_at=300)

    res = resolve(_ctx(fake, session), cid, None, trigger="update_contact")
    assert res["action"] == "deal_noop"
    assert not _tags(fake, asm) and not _tags(fake, pipe)
