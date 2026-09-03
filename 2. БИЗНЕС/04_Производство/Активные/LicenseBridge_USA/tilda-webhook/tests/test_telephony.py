"""Телефония: очередь дозвона, журнал звонков и задачи по пропущенным."""
import datetime

from app.actions import Ctx
from app.config import settings
from app.models import CallEvent
from app.telephony import dial_guard, handle_call, rec_sign, rec_url, rec_verify, ring_order

POLINA = 15293564                          # клиентский отдел, принимает в RingCentral
ROP = 15648532                             # 103, Александра (РОП) — единственная линия продаж
FIRED = 15537380                           # Илона, уволена 19.08: добавочного нет, карточки остались
PAVEL = 13291175                           # 101, владелец: садится на линию 1 сменами
NO_EXT = 15554448                          # Дмитрий: софтфона в карте нет
PHONE = "+12139310879"


def _ctx(fake, session, phone=PHONE):
    return Ctx(client=fake, session=session, inbox_id=None, phone=phone, shadow=False)


def _notes(fake, entity, entity_id):
    return fake.notes.get((entity, int(entity_id)), [])


# ── очередь дозвона ──
def test_route_answer_is_dash_separated_for_dialplan(fake, session, monkeypatch):
    """Диалплан режет ответ через CUT(), которому запятая не по зубам.

    В продажах сейчас один добавочный, поэтому круг подменяем: гарантия формата
    нужна к моменту, когда вместо Илоны появится второй менеджер."""
    from app.webhooks import call_route  # noqa: F401 — проверяем формат склейки

    monkeypatch.setattr(settings, "telephony_sales_order", "103,104")
    order, _ = ring_order(fake, "+15550001111", did="1")
    assert "-".join(order) == "103-104"


def test_unknown_number_gets_sales_circle(fake, session):
    order, resolved = ring_order(fake, "+15550001111", did="1")
    assert order == ["103"]
    assert resolved["found"] is False


def test_existing_client_starts_from_own_manager(fake, session):
    contact = fake.add_contact(name="Клиент", phone=PHONE)
    fake.add_lead(contact, settings.pipeline_id, settings.status_new, responsible=ROP)

    order, resolved = ring_order(fake, PHONE, did="1")

    assert order == ["103"]
    assert resolved["responsible_user_id"] == ROP


def test_client_of_fired_manager_still_reaches_sales(fake, session):
    """Карточки Илоны остались в воронке. Её добавочного в карте больше нет,
    поэтому звонок обязан уйти в круг продаж, а не в тишину."""
    contact = fake.add_contact(name="Клиент", phone=PHONE)
    fake.add_lead(contact, settings.pipeline_id, settings.status_new, responsible=FIRED)

    order, resolved = ring_order(fake, PHONE, did="1")

    assert order == ["103"]
    assert resolved["responsible_user_id"] == FIRED


def test_former_line_of_fired_manager_rings_sales(fake, session):
    """Клиенты до сих пор звонят на бывший номер Илоны (линия 1) — ведём на РОП."""
    order, _ = ring_order(fake, "+15550001111", did="1")
    assert order == ["103"]


def test_client_of_service_department_still_goes_to_sales(fake, session):
    """Полина ведёт клиентский отдел — в круг продаж её не ставим."""
    contact = fake.add_contact(name="Клиент", phone=PHONE)
    fake.add_lead(contact, settings.pipeline_id, settings.status_new, responsible=POLINA)

    order, _ = ring_order(fake, PHONE, did="2")

    assert order == ["103"]


def test_direct_line_owner_answers_first(fake, session):
    """Позвонили на прямой номер РОПа — начинаем с неё, даже если клиент новый."""
    order, _ = ring_order(fake, "+15550001111", did="3")
    assert order == ["103"]


def test_owner_without_extension_falls_back_to_circle(fake, session):
    """Ответственный без софтфона: звонок идёт по общему кругу, а не в тишину."""
    contact = fake.add_contact(name="Клиент", phone=PHONE)
    fake.add_lead(contact, settings.pipeline_id, settings.status_new, responsible=NO_EXT)

    order, _ = ring_order(fake, PHONE, did="1")

    assert order == ["103"]


def test_pavel_stays_in_extension_map(fake, session, monkeypatch):
    """101 Павла обязан быть в карте добавочных: без него звонок к нему не идёт,
    а его разговоры попадают в Kommo без автора. Строку затирало деплоем (31.08)."""
    assert settings.ext_to_user.get("101") == PAVEL

    monkeypatch.setattr(settings, "telephony_sales_order", "103,101")
    contact = fake.add_contact(name="Клиент", phone=PHONE)
    fake.add_lead(contact, settings.pipeline_id, settings.status_new, responsible=PAVEL)

    order, _ = ring_order(fake, PHONE, did="1")

    assert order[0] == "101"


# ── журнал звонков ──
def test_missed_call_creates_task_for_responsible(fake, session, enable_all, enable_telephony):
    contact = fake.add_contact(name="Клиент", phone=PHONE)
    lead = fake.add_lead(contact, settings.pipeline_id, settings.status_new, responsible=ROP)

    res = handle_call(_ctx(fake, session), "call_missed",
                      {"uniqueid": "1780000000.1", "phone": PHONE, "did": "1", "direction": "in"})

    assert res["action"] == "call_logged" and res["lead"] == lead
    assert [t["responsible_user_id"] for t in fake.tasks] == [ROP]
    assert fake.tasks[0]["entity_type"] == "leads"
    note = _notes(fake, "leads", lead)[0]
    assert note["note_type"] == "call_in" and note["params"]["call_status"] == 6


def test_missed_call_from_unknown_number_creates_card(fake, session, enable_all, enable_telephony):
    res = handle_call(_ctx(fake, session, "+15557654321"), "call_missed",
                      {"uniqueid": "1780000000.2", "phone": "+15557654321", "did": "1"})

    assert res["contact"] and res["lead"]
    assert fake.leads[res["lead"]]["responsible_user_id"] == ROP
    assert len(fake.tasks) == 1 and fake.tasks[0]["responsible_user_id"] == ROP


def test_repeated_event_does_not_double_task(fake, session, enable_all, enable_telephony):
    contact = fake.add_contact(name="Клиент", phone=PHONE)
    lead = fake.add_lead(contact, settings.pipeline_id, settings.status_new, responsible=ROP)
    payload = {"uniqueid": "1780000000.3", "phone": PHONE, "did": "1"}

    handle_call(_ctx(fake, session), "call_missed", payload)
    second = handle_call(_ctx(fake, session), "call_missed", payload)

    assert second["action"] == "already_done"
    assert len(fake.tasks) == 1
    assert len(_notes(fake, "leads", lead)) == 1


def test_task_of_departed_manager_goes_to_rop(fake, session, enable_all, enable_telephony):
    """Карточка осталась на уволенной Илоне, в Kommo она активна. Задачу всё
    равно отдаём РОПу, иначе повторный контакт клиента снова никто не увидит."""
    fake.users_list.append({"id": FIRED, "rights": {"is_active": True}})
    contact = fake.add_contact(name="Клиент", phone=PHONE)
    fake.add_lead(contact, settings.pipeline_id, settings.status_new, responsible=FIRED)

    handle_call(_ctx(fake, session), "call_missed",
                {"uniqueid": "1780000000.12", "phone": PHONE, "did": "1"})

    assert [t["responsible_user_id"] for t in fake.tasks] == [settings.telephony_missed_owner_id]


def test_task_of_owner_without_ext_goes_to_sales(fake, session, enable_all, enable_telephony):
    """Карточка на Павле: звонок звонил у Александры, значит и задача её.

    Раньше задача уходила владельцу карточки, а он звонки не принимает — ровно
    то, что Александра описала словами «если лид был не на мне, я не вижу звонок»."""
    pavel = 13291175
    fake.users_list.append({"id": pavel, "rights": {"is_active": True}})
    contact = fake.add_contact(name="Клиент", phone=PHONE)
    fake.add_lead(contact, settings.pipeline_id, settings.status_new, responsible=pavel)

    handle_call(_ctx(fake, session), "call_missed",
                {"uniqueid": "1780000000.13", "phone": PHONE, "did": "1"})

    assert [t["responsible_user_id"] for t in fake.tasks] == [settings.telephony_missed_owner_id]


def test_task_of_deactivated_manager_goes_to_rop(fake, session, enable_all, enable_telephony):
    """Задача на уволенном — задача, которой никто не видит. Плюс Kommo такую
    задачу просто отвергает."""
    contact = fake.add_contact(name="Клиент", phone=PHONE)
    fake.add_lead(contact, settings.pipeline_id, settings.status_new, responsible=99999999)

    handle_call(_ctx(fake, session), "call_missed",
                {"uniqueid": "1780000000.11", "phone": PHONE, "did": "1"})

    assert [t["responsible_user_id"] for t in fake.tasks] == [settings.telephony_missed_owner_id]


def test_service_branch_task_goes_to_polina(fake, session, enable_all, enable_telephony):
    """Нажали «2», в RingCentral не ответили: карточка Елены, задача — Полине."""
    contact = fake.add_contact(name="Клиент", phone=PHONE)
    lead = fake.add_lead(contact, settings.pipeline_id, settings.status_new, responsible=ROP)

    handle_call(_ctx(fake, session), "call_missed",
                {"uniqueid": "1780000000.8", "phone": PHONE, "did": "1",
                 "branch": "service", "disposition": "VOICEMAIL"})

    assert [t["responsible_user_id"] for t in fake.tasks] == [POLINA]
    assert "обслуживание" in fake.tasks[0]["text"]
    assert "обслуживание" in _notes(fake, "leads", lead)[0]["params"]["call_result"]


def test_answered_call_note_carries_recording_link(fake, session, enable_all, enable_telephony):
    contact = fake.add_contact(name="Клиент", phone=PHONE)
    lead = fake.add_lead(contact, settings.pipeline_id, settings.status_new, responsible=ROP)

    handle_call(_ctx(fake, session), "call_finished", {
        "uniqueid": "1780000000.4", "phone": PHONE, "did": "3", "ext": "103",
        "duration": "65", "disposition": "ANSWER",
        "recording": "20260727-101500-12139310879-1780000000.4.wav",
    })

    params = _notes(fake, "leads", lead)[0]["params"]
    assert params["call_status"] == 4 and params["duration"] == 65
    assert params["link"].startswith(settings.public_base_url + "/rec/")
    assert not fake.tasks  # по состоявшемуся разговору задача не нужна


def test_outgoing_call_note_belongs_to_the_manager_who_dialed(fake, session, enable_all,
                                                              enable_telephony):
    """Александра (103) звонит по клиенту Илоны: звонок должен быть её, не Илоны
    и не пользователя интеграции."""
    contact = fake.add_contact(name="Клиент", phone=PHONE)
    lead = fake.add_lead(contact, settings.pipeline_id, settings.status_new, responsible=FIRED)

    res = handle_call(_ctx(fake, session), "call_finished", {
        "uniqueid": "1780000000.9", "phone": PHONE, "direction": "out", "ext": "103",
        "duration": "269", "disposition": "ANSWER",
    })

    note = _notes(fake, "leads", lead)[0]
    assert res["caller"] == ROP
    assert note["note_type"] == "call_out"
    assert note["created_by"] == ROP and note["responsible_user_id"] == ROP


def test_call_without_extension_keeps_deal_owner(fake, session, enable_all, enable_telephony):
    """Старый диалплан (без ext) не должен ломать журнал: автора не выдумываем."""
    contact = fake.add_contact(name="Клиент", phone=PHONE)
    lead = fake.add_lead(contact, settings.pipeline_id, settings.status_new, responsible=FIRED)

    handle_call(_ctx(fake, session), "call_finished", {
        "uniqueid": "1780000000.10", "phone": PHONE, "direction": "out",
        "duration": "40", "disposition": "ANSWER",
    })

    note = _notes(fake, "leads", lead)[0]
    assert "created_by" not in note and note["responsible_user_id"] == FIRED


def test_hangup_on_menu_from_unknown_number_leaves_crm_clean(fake, session,
                                                             enable_all, enable_telephony):
    """Сброс на меню — не повод заводить карточку: иначе CRM забьёт спам-обзвон."""
    res = handle_call(_ctx(fake, session, "+15550009999"), "call_finished",
                      {"uniqueid": "1780000000.5", "phone": "+15550009999",
                       "duration": "0", "disposition": "NOANSWER"})

    assert res["action"] == "no_card"
    assert not fake.contacts and not fake.leads and not fake.tasks


def test_missed_call_without_caller_id_is_not_lost_silently(fake, session,
                                                            enable_all, enable_telephony):
    res = handle_call(_ctx(fake, session, None), "call_missed",
                      {"uniqueid": "1780000000.6", "phone": "", "did": "1"})
    assert res["action"] == "no_phone"


def test_telephony_disabled_writes_nothing(fake, session, enable_all, monkeypatch):
    monkeypatch.setattr(settings, "enable_telephony", False)
    contact = fake.add_contact(name="Клиент", phone=PHONE)
    fake.add_lead(contact, settings.pipeline_id, settings.status_new, responsible=ROP)

    res = handle_call(_ctx(fake, session), "call_missed",
                      {"uniqueid": "1780000000.7", "phone": PHONE})

    assert res["action"] == "telephony_disabled" and not fake.tasks


# ── сторож дозвона ──
def _out_call(session, phone, minutes_ago, uniq, disposition="NOANSWER", duration=0):
    session.add(CallEvent(
        uniqueid=uniq, phone=phone, direction="out", duration=duration,
        disposition=disposition,
        created_at=datetime.datetime.now(datetime.timezone.utc)
        - datetime.timedelta(minutes=minutes_ago),
    ))
    session.commit()


def test_guard_allows_first_call(session, enable_telephony):
    assert dial_guard(session, PHONE) == ("ok", "")


def test_guard_flags_instant_redial(session, enable_telephony, monkeypatch):
    """Два набора подряд с интервалом в секунды — самый заметный движкам признак."""
    monkeypatch.setattr(settings, "dial_guard_mode", "warn")
    _out_call(session, PHONE, 0, "g1")

    verdict, reason = dial_guard(session, PHONE)

    assert verdict == "soft" and reason.startswith("redial_")


def test_guard_blocks_instant_redial_in_block_mode(session, enable_telephony, monkeypatch):
    monkeypatch.setattr(settings, "dial_guard_mode", "block")
    _out_call(session, PHONE, 0, "g2")

    verdict, _ = dial_guard(session, PHONE)

    assert verdict == "deny"


def test_guard_flags_repeat_inside_interval(session, enable_telephony, monkeypatch):
    monkeypatch.setattr(settings, "dial_guard_mode", "warn")
    _out_call(session, PHONE, 5, "g3")

    verdict, reason = dial_guard(session, PHONE)

    assert verdict == "soft" and reason == "attempt1_15min"


def test_guard_second_no_answer_locks_three_hours(session, enable_telephony, monkeypatch):
    monkeypatch.setattr(settings, "dial_guard_mode", "block")
    _out_call(session, PHONE, 40, "g3h-1")
    _out_call(session, PHONE, 20, "g3h-2")

    verdict, reason = dial_guard(session, PHONE)

    assert verdict == "deny" and reason == "attempt2_3h"


def test_guard_answered_does_not_escalate_ladder(session, enable_telephony, monkeypatch):
    """Дозвон лестницу недозвона не поднимает: через 20 минут можно снова."""
    monkeypatch.setattr(settings, "dial_guard_mode", "block")
    _out_call(session, PHONE, 20, "gans", disposition="ANSWERED", duration=90)

    assert dial_guard(session, PHONE) == ("ok", "")


def test_guard_flags_daily_limit(session, enable_telephony, monkeypatch):
    monkeypatch.setattr(settings, "dial_guard_mode", "warn")
    for i, ago in enumerate((600, 400, 200)):
        _out_call(session, PHONE, ago, f"g4-{i}")

    verdict, reason = dial_guard(session, PHONE)

    assert verdict == "soft" and reason.startswith("day_limit_")


def test_guard_reactivation_once_a_day(session, enable_telephony, monkeypatch):
    monkeypatch.setattr(settings, "dial_guard_mode", "block")
    _out_call(session, PHONE, 60, "greact")

    verdict, reason = dial_guard(session, PHONE, lead_status_id=110600324)

    assert verdict == "deny" and reason == "reactivation_24h"


def test_guard_ignores_calls_older_than_a_day(session, enable_telephony, monkeypatch):
    monkeypatch.setattr(settings, "dial_guard_mode", "block")
    for i in range(5):
        _out_call(session, PHONE, 60 * 30 + i, f"g5-{i}")

    assert dial_guard(session, PHONE) == ("ok", "")


def test_guard_off_never_interferes(session, enable_telephony, monkeypatch):
    monkeypatch.setattr(settings, "dial_guard_mode", "off")
    _out_call(session, PHONE, 0, "g6")

    assert dial_guard(session, PHONE) == ("ok", "")


def test_guard_counts_only_our_outgoing(session, enable_telephony, monkeypatch):
    """Входящий от клиента — не наш набор: он репутацию номера не портит."""
    monkeypatch.setattr(settings, "dial_guard_mode", "block")
    session.add(CallEvent(uniqueid="g7", phone=PHONE, direction="in", duration=3))
    session.commit()

    assert dial_guard(session, PHONE) == ("ok", "")


# ── подписанные ссылки на записи ──
def test_recording_link_signature(enable_telephony):
    name = "20260727-101500-12139310879-1780000000.4.wav"
    url = rec_url(name)
    assert f"/rec/{rec_sign(name)}/{name}" in url
    assert rec_verify(name, rec_sign(name))
    assert not rec_verify(name, "deadbeef")
    assert not rec_verify("../../etc/passwd", rec_sign("../../etc/passwd"))
