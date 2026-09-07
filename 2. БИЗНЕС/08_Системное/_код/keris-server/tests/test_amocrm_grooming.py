"""Воронка «Груминг»: карта этапов, матч контакта по телефону, метрики, идемпотентность."""
import dataclasses
from datetime import datetime, timedelta

from app import amocrm_client, amocrm_metrics, amocrm_stages, sync
from app.config import settings as app_settings
from app.amocrm_stages import (
    STAGE_ARRIVED,
    STAGE_CANCELLED,
    STAGE_CREATED,
    STAGE_DONE,
    STAGE_NO_SHOW,
    STAGE_TODAY,
    STAGE_TOMORROW,
    STAGE_WAITING,
    target_stage,
)
from app.models import Booking, BookingSource, BookingStatus, PetType, Subscription

NOW = datetime(2026, 8, 20, 12, 0)


def make_booking(db, starts_at, *, bid="KERIS-9101", created_at=None, price=7800,
                 status=BookingStatus.confirmed, phone="+79990000009", pet="Барс",
                 lead_id=None, stage="", source=BookingSource.miniapp):
    b = Booking(
        id=bid, owner_name="Тест", owner_phone=phone, pet_name=pet,
        pet_type=PetType.dog, pet_size="XS", pet_breed="Шпиц", pet_weight_kg=3.5,
        service_id="dog_complex_cut", addon_ids=[], free_addon_ids=[], applied_promos=[],
        master_id="svetlana", starts_at=starts_at,
        ends_at=starts_at + timedelta(minutes=90), price=price, status=status,
        source=source, personal_data_consent=True,
        created_at=created_at or (starts_at - timedelta(days=3)),
        amocrm_lead_id=lead_id, amocrm_stage=stage,
    )
    db.add(b)
    db.commit()
    return b


# --- карта этапов ---------------------------------------------------------

def test_stage_created_on_the_day_booking_was_made(db_session):
    """Запись оформили сегодня на завтра — в этот день сделка стоит на «Запись
    создана», иначе клиент получит и «записали вас», и «завтра визит» подряд."""
    b = make_booking(db_session, NOW + timedelta(days=1), created_at=NOW)
    assert target_stage(b, NOW) == STAGE_CREATED


def test_stage_tomorrow_next_day(db_session):
    b = make_booking(db_session, NOW + timedelta(days=1), created_at=NOW - timedelta(days=2))
    assert target_stage(b, NOW) == STAGE_TOMORROW


def test_stage_today(db_session):
    b = make_booking(db_session, NOW + timedelta(hours=5), created_at=NOW - timedelta(days=2))
    assert target_stage(b, NOW) == STAGE_TODAY


def test_stage_waiting_far_visit(db_session):
    b = make_booking(db_session, NOW + timedelta(days=6), created_at=NOW - timedelta(days=1))
    assert target_stage(b, NOW) == STAGE_WAITING


def test_stage_arrived_then_done(db_session):
    """«Пришел» в журнале ставят на входе: пока визит идёт — «Клиент пришёл»,
    после окончания — Успех."""
    b = make_booking(db_session, NOW - timedelta(minutes=30), status=BookingStatus.completed)
    assert target_stage(b, NOW) == STAGE_ARRIVED
    b.starts_at = NOW - timedelta(hours=3)
    b.ends_at = NOW - timedelta(hours=1)
    assert target_stage(b, NOW) == STAGE_DONE


def test_stage_cancelled_and_no_show_win_over_dates(db_session):
    b = make_booking(db_session, NOW + timedelta(hours=2), status=BookingStatus.cancelled)
    assert target_stage(b, NOW) == STAGE_CANCELLED
    b.status = BookingStatus.no_show
    assert target_stage(b, NOW) == STAGE_NO_SHOW


def test_stage_today_until_end_of_visit_day(db_session):
    """Время визита прошло, но день ещё тот же — ждём отметки «Пришел» в журнале,
    в Успех сами не закрываем: успех опирается на факт, а не на часы."""
    b = make_booking(db_session, NOW - timedelta(hours=4), created_at=NOW - timedelta(days=1))
    assert target_stage(b, NOW) == STAGE_TODAY


def test_stage_arrived_for_past_day_without_journal_mark(db_session):
    """Записи прошлых дней без отметки не должны висеть в «Сегодня запись» —
    иначе сейлсбот напомнит о визите, который был на прошлой неделе."""
    b = make_booking(db_session, NOW - timedelta(days=7), created_at=NOW - timedelta(days=10))
    assert target_stage(b, NOW) == STAGE_ARRIVED


def test_amo_datetime_matches_account_format():
    """Этот аккаунт отклоняет `Y-m-d` на полях date — нужен ISO с поясом."""
    assert amocrm_client.amo_datetime("2026-08-10") == "2026-08-10T00:00:00+03:00"
    assert amocrm_client.amo_datetime("01.05.2023") == "2023-05-01T00:00:00+03:00"
    assert amocrm_client.amo_datetime(NOW, with_time=True) == "2026-08-20T12:00:00+03:00"
    assert amocrm_client.amo_datetime("") is None


# --- матч контакта по телефону -------------------------------------------

def test_phone_variants_cover_recorded_formats():
    assert amocrm_client.phone_variants("+7 999 368-98-71") == [
        "+79993689871", "89993689871", "79993689871", "9993689871",
    ]
    assert amocrm_client.phone_variants("89993689871")[0] == "+79993689871"


def test_find_contact_ignores_foreign_search_hit(monkeypatch):
    """`query=` ищет по всем полям и возвращает чужие карточки — берём только ту,
    где сходится номер, иначе визит уедет не тому клиенту."""
    calls = []

    def fake_request(method, path, **kwargs):
        calls.append(kwargs.get("params", {}).get("query"))
        return {"_embedded": {"contacts": [
            {"id": 111, "custom_fields_values": [
                {"field_code": "PHONE", "values": [{"value": "+79991112233"}]}]},
            {"id": 222, "custom_fields_values": [
                {"field_code": "PHONE", "values": [{"value": "8 (999) 368-98-71"}]}]},
        ]}}

    monkeypatch.setattr(amocrm_client, "_request", fake_request)
    found = amocrm_client.find_contact_by_phone("+79993689871")
    assert found["id"] == 222
    assert calls[0] == "+79993689871"


def test_find_contact_returns_none_when_phone_differs(monkeypatch):
    monkeypatch.setattr(amocrm_client, "_request", lambda *a, **kw: {"_embedded": {"contacts": [
        {"id": 111, "custom_fields_values": [
            {"field_code": "PHONE", "values": [{"value": "+79991112233"}]}]},
    ]}})
    assert amocrm_client.find_contact_by_phone("+79993689871") is None


# --- метрики клиента -----------------------------------------------------

def test_client_metrics_counts_visits_and_money(db_session):
    make_booking(db_session, NOW - timedelta(days=40), bid="KERIS-9101", price=5000)
    make_booking(db_session, NOW - timedelta(days=10), bid="KERIS-9102", price=7000)
    make_booking(db_session, NOW + timedelta(days=5), bid="KERIS-9103", price=8000)
    make_booking(db_session, NOW - timedelta(days=2), bid="KERIS-9104", price=9000,
                 status=BookingStatus.no_show)

    m = amocrm_metrics.client_metrics(db_session, "+79990000009", NOW)
    assert m["Груминг: визитов всего"] == 2
    assert m["Груминг: сумма всего"] == 12000
    assert m["Груминг: средний чек"] == 6000
    assert m["Груминг: первый визит"] == "2026-07-11"
    assert m["Груминг: последний визит"] == "2026-08-10"
    assert m["Груминг: следующий визит"] == "2026-08-25"
    assert m["Груминг: неявок"] == 1
    assert m["Груминг: статус клиента"] == amocrm_metrics.STATUS_ACTIVE
    assert m["Клиент груминга"] is True


def test_client_status_sleeping_and_lost(db_session):
    b = make_booking(db_session, NOW - timedelta(days=70))
    assert amocrm_metrics.client_metrics(db_session, b.owner_phone, NOW)[
        "Груминг: статус клиента"] == amocrm_metrics.STATUS_SLEEPING
    b.starts_at = NOW - timedelta(days=200)
    b.ends_at = b.starts_at + timedelta(minutes=90)
    db_session.commit()
    assert amocrm_metrics.client_metrics(db_session, b.owner_phone, NOW)[
        "Груминг: статус клиента"] == amocrm_metrics.STATUS_LOST


def test_client_status_new_when_only_future_visit(db_session):
    make_booking(db_session, NOW + timedelta(days=2))
    m = amocrm_metrics.client_metrics(db_session, "+79990000009", NOW)
    assert m["Груминг: визитов всего"] == 0
    assert m["Груминг: статус клиента"] == amocrm_metrics.STATUS_ACTIVE


def test_subscription_in_metrics(db_session):
    make_booking(db_session, NOW - timedelta(days=5))
    db_session.add(Subscription(
        owner_phone="+79990000009", plan_id="habit", size="XS",
        visits_total=6, visits_used=3.5, purchased_at=NOW - timedelta(days=5),
        expires_at=NOW + timedelta(days=30),
    ))
    db_session.commit()
    m = amocrm_metrics.client_metrics(db_session, "+79990000009", NOW)
    assert m["Абонемент: план"] == "Привыкаем"
    assert m["Абонемент: остаток визитов"] == "2.5"
    assert m["Абонемент: действует до"] == "2026-09-19"


def test_pet_fields_count_only_this_pet(db_session):
    make_booking(db_session, NOW - timedelta(days=20), bid="KERIS-9101", pet="Барс")
    make_booking(db_session, NOW - timedelta(days=10), bid="KERIS-9102", pet="Ника")
    last = make_booking(db_session, NOW - timedelta(days=5), bid="KERIS-9103", pet="Барс")
    fields = amocrm_metrics.pet_fields(db_session, last, NOW)
    assert fields["Визитов всего"] == 2
    assert fields["Последний визит"] == "2026-08-15"
    assert fields["Порода"] == "Шпиц"
    assert fields["Вес, кг"] == "3.5"


# --- идемпотентность -----------------------------------------------------

class FakeAmo:
    """Заглушка amoCRM: считает, сколько раз что создавали."""

    def __init__(self):
        self.contacts = 0
        self.leads = 0
        self.companies = 0
        self.stages = []
        self.notes = []
        self.lead_updates = 0

    def install(self, monkeypatch):
        # Время фиксируем: этап зависит от календарной даты, иначе тест живёт
        # ровно до полуночи.
        monkeypatch.setattr(amocrm_stages.clock, "now", lambda: NOW)
        monkeypatch.setattr(amocrm_client, "settings",
                            dataclasses.replace(app_settings, amocrm_token="test-token"))
        monkeypatch.setattr(sync.amocrm_client, "find_or_create_contact",
                            lambda *a, **kw: self._contact())
        monkeypatch.setattr(sync.amocrm_client, "find_or_create_pet_company",
                            lambda *a, **kw: self._company())
        monkeypatch.setattr(sync.amocrm_client, "update_pet_company", lambda *a, **kw: None)
        monkeypatch.setattr(sync.amocrm_client, "create_grooming_lead", lambda **kw: self._lead(kw))
        monkeypatch.setattr(sync.amocrm_client, "update_grooming_lead", self._update)
        monkeypatch.setattr(sync.amocrm_client, "add_lead_note",
                            lambda lead_id, text: self.notes.append(text))
        monkeypatch.setattr(amocrm_stages.amocrm_client, "move_lead_to_stage",
                            lambda lead_id, stage: self.stages.append(stage) is None or True)
        monkeypatch.setattr(sync.amocrm_metrics, "push_client_metrics", lambda *a, **kw: True)

    def _contact(self):
        self.contacts += 1
        return 500

    def _company(self):
        self.companies += 1
        return 600

    def _lead(self, kw):
        self.leads += 1
        self.stages.append(kw.get("status"))
        return 700

    def _update(self, lead_id, **kw):
        self.lead_updates += 1


def test_repeated_sync_creates_single_lead(db_session, monkeypatch):
    fake = FakeAmo()
    fake.install(monkeypatch)
    booking = make_booking(db_session, NOW + timedelta(days=4), created_at=NOW)

    assert sync.sync_booking_to_amocrm(db_session, booking) is True
    assert booking.amocrm_lead_id == 700
    assert booking.amocrm_company_id == 600
    assert booking.amocrm_stage == STAGE_CREATED

    assert sync.sync_booking_to_amocrm(db_session, booking) is True
    assert fake.leads == 1
    assert fake.lead_updates == 1
    assert fake.companies == 1


def test_blocked_account_stops_the_pass(db_session, monkeypatch):
    """Аккаунт без оплаты отвечает «Payment Required» — проход прекращается сразу,
    а не перебирает все записи с тем же результатом. Записи ждут в sync_log."""
    fake = FakeAmo()
    fake.install(monkeypatch)

    state = {"blocked": False, "attempts": 0}

    def blocked_lead(**kw):
        state["attempts"] += 1
        state["blocked"] = True
        raise amocrm_client.AmoCrmBlocked("POST /api/v4/leads -> Payment Required")

    monkeypatch.setattr(sync.amocrm_client, "create_grooming_lead", blocked_lead)
    monkeypatch.setattr(amocrm_client, "blocked", lambda: state["blocked"])

    for i in range(3):
        make_booking(db_session, NOW + timedelta(days=2), bid=f"KERIS-920{i}",
                     created_at=NOW - timedelta(days=1), phone=f"+7999000000{i}")
    result = amocrm_stages.run_once(db_session, NOW)
    assert result["created"] == 0
    assert state["attempts"] == 1


def test_sweep_creates_missing_lead_and_moves_stage(db_session, monkeypatch):
    """Гарантия «без пропусков»: запись без сделки добирается проходом,
    а сделка на устаревшем этапе — переводится."""
    fake = FakeAmo()
    fake.install(monkeypatch)

    make_booking(db_session, NOW + timedelta(days=1), bid="KERIS-9101",
                 created_at=NOW - timedelta(days=2))
    make_booking(db_session, NOW + timedelta(hours=3), bid="KERIS-9102",
                 created_at=NOW - timedelta(days=1), lead_id=701, stage=STAGE_WAITING)

    result = amocrm_stages.run_once(db_session, NOW)
    assert result["created"] == 1
    assert result["moved"] == 1
    assert STAGE_TODAY in fake.stages
    assert STAGE_TOMORROW in fake.stages
