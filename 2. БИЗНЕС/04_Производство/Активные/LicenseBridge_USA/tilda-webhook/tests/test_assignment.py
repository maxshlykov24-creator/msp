from app.actions import Ctx
from app.assignment import assign_new_lead, resolve_owner_for_new
from app.config import settings


def _ctx(fake, session):
    return Ctx(client=fake, session=session, inbox_id=None, phone="+1", shadow=False)


def test_new_without_history_goes_to_default_owner(fake, session):
    assert resolve_owner_for_new(_ctx(fake, session), []) == settings.default_sales_owner_id


def test_departed_owner_is_not_inherited(fake, session):
    """Уволенного в Kommo обычно оставляют активным ради истории. Без явного
    списка ушедших вернувшийся клиент наследует карточку человека, которого нет
    в команде, и его никто не видит — так и обнаружилась проблема 19.08."""
    fake.users_list.append({"id": 15537380, "rights": {"is_active": True}})
    leads = [{"id": 1, "status_id": settings.status_lost, "closed_at": 500,
              "responsible_user_id": 15537380}]

    assert resolve_owner_for_new(_ctx(fake, session), leads) == settings.default_sales_owner_id


def test_inherit_from_fresh_closed(fake, session):
    leads = [
        {"id": 1, "status_id": settings.status_lost, "closed_at": 100,
         "responsible_user_id": 15293564},
        {"id": 2, "status_id": settings.status_lost, "closed_at": 500,
         "responsible_user_id": settings.default_sales_owner_id},
    ]
    # свежая закрытая (closed_at=500) → её ответственный (Илона активна)
    owner = resolve_owner_for_new(_ctx(fake, session), leads)
    assert owner == settings.default_sales_owner_id


def test_owner_without_ext_is_not_inherited(fake, session):
    """Павел активен в CRM, но продажи не ведёт и звонки не принимает.

    Наследовать его — значит отдать вернувшегося клиента человеку, которого для
    отдела продаж не существует. Признак «ведёт продажи» — добавочный в карте
    телефонии, она же правится при смене менеджеров."""
    pavel = 13291175
    fake.users_list.append({"id": pavel, "rights": {"is_active": True}})
    leads = [{"id": 1, "status_id": settings.status_lost, "closed_at": 500,
              "responsible_user_id": pavel}]

    assert resolve_owner_for_new(_ctx(fake, session), leads) == settings.default_sales_owner_id


def test_inactive_owner_falls_back_to_ilona(fake, session):
    leads = [{"id": 1, "status_id": settings.status_lost, "closed_at": 500,
              "responsible_user_id": 99999999}]  # уволенный
    owner = resolve_owner_for_new(_ctx(fake, session), leads)
    assert owner == settings.default_sales_owner_id


def test_assign_syncs_contact_to_lead_owner(fake, session, enable_all):
    """Make оставляет контакт на Павле — после assign контакт = владелец сделки."""
    pavel = 13291175
    cid = fake.add_contact(name="A", phone="+15551234567")
    fake.contacts[cid]["responsible_user_id"] = pavel
    lid = fake.add_lead(cid, settings.pipeline_id, settings.status_new, responsible=pavel)

    owner = assign_new_lead(_ctx(fake, session), fake.leads[lid], [])

    assert owner == settings.default_sales_owner_id
    assert fake.leads[lid]["responsible_user_id"] == settings.default_sales_owner_id
    assert fake.contacts[cid]["responsible_user_id"] == settings.default_sales_owner_id


def test_assign_noop_still_syncs_contact(fake, session, enable_all):
    pavel = 13291175
    cid = fake.add_contact(name="A", phone="+15551234567")
    fake.contacts[cid]["responsible_user_id"] = pavel
    lid = fake.add_lead(cid, settings.pipeline_id, settings.status_new,
                        responsible=settings.default_sales_owner_id)

    assign_new_lead(_ctx(fake, session), fake.leads[lid], [])

    assert fake.contacts[cid]["responsible_user_id"] == settings.default_sales_owner_id


def test_sweep_moves_stuck_lead_to_sales(fake, session, enable_all):
    """Сделку в воронке продаж завёл сторонний сценарий и оставил на Павле.

    Событие создания такую не ловит, а продавец её не видит. Подметание раз в час
    возвращает карточку тому, кто принимает звонки."""
    from app.assignment import sweep_non_sales_owners

    pavel = 13291175
    fake.users_list.append({"id": pavel, "rights": {"is_active": True}})
    cid = fake.add_contact(name="A", phone="+15551234567")
    fake.contacts[cid]["responsible_user_id"] = pavel
    lid = fake.add_lead(cid, settings.pipeline_id, settings.status_new, responsible=pavel)

    moved = sweep_non_sales_owners(_ctx(fake, session))

    assert moved == [lid]
    assert fake.leads[lid]["responsible_user_id"] == settings.default_sales_owner_id
    assert fake.contacts[cid]["responsible_user_id"] == settings.default_sales_owner_id


def test_sweep_leaves_sales_and_closed_alone(fake, session, enable_all):
    """Карточки продавца и закрытые сделки подметание не трогает."""
    from app.assignment import sweep_non_sales_owners

    cid = fake.add_contact(name="B", phone="+15551234568")
    mine = fake.add_lead(cid, settings.pipeline_id, settings.status_new,
                         responsible=settings.default_sales_owner_id)
    closed = fake.add_lead(cid, settings.pipeline_id, settings.status_lost,
                           responsible=13291175)
    fake.leads[closed]["closed_at"] = 1000

    assert sweep_non_sales_owners(_ctx(fake, session)) == []
    assert fake.leads[mine]["responsible_user_id"] == settings.default_sales_owner_id
    assert fake.leads[closed]["responsible_user_id"] == 13291175


def test_sweep_leaves_client_dept_cards(fake, session, enable_all):
    """Старые сделки Полины в воронке продаж — её работа, автоматика их не отбирает.

    19.08 подметание за один прогон сняло с неё 44 карточки: новых лидов она не
    получает, но это не повод забирать уже начатое."""
    from app.assignment import sweep_non_sales_owners

    polina = 15293564
    cid = fake.add_contact(name="D", phone="+15551234570")
    lid = fake.add_lead(cid, settings.pipeline_id, settings.status_new, responsible=polina)

    assert sweep_non_sales_owners(_ctx(fake, session)) == []
    assert fake.leads[lid]["responsible_user_id"] == polina


def test_sweep_does_not_touch_assembly(fake, session, enable_all):
    """«Сборка» — не продажи: там ответственный ставится своим процессом.

    Даже если Kommo вернул чужую воронку в ответ на фильтр (так и было 19.08),
    подметание обязано отсеять её само."""
    from app.assignment import sweep_non_sales_owners

    cid = fake.add_contact(name="C", phone="+15551234569")
    polina = fake.add_lead(cid, settings.assembly_pipeline_id, 1000, responsible=15293564)
    fired = fake.add_lead(cid, settings.assembly_pipeline_id, 1000, responsible=15537380)

    assert sweep_non_sales_owners(_ctx(fake, session)) == []
    assert fake.leads[polina]["responsible_user_id"] == 15293564
    assert fake.leads[fired]["responsible_user_id"] == 15537380


def test_assign_skips_contact_on_assembly(fake, session, enable_all):
    pavel = 13291175
    cid = fake.add_contact(name="A", phone="+15551234567")
    fake.contacts[cid]["responsible_user_id"] = pavel
    lid = fake.add_lead(cid, settings.assembly_pipeline_id, 1000, responsible=15293564)

    assign_new_lead(_ctx(fake, session), fake.leads[lid], [])

    assert fake.contacts[cid]["responsible_user_id"] == pavel
