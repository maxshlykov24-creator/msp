import pytest

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


# Pleep task delivery is isolated from other intake sources and from telephony.
@pytest.fixture()
def pleep_task_env(fake, session, monkeypatch):
    import contextlib
    from app import db

    @contextlib.contextmanager
    def scope():
        yield session
        session.commit()

    @contextlib.contextmanager
    def lock(*args):
        yield

    monkeypatch.setattr(db, 'session_scope', scope)
    monkeypatch.setattr(db, 'phone_lock', lock)
    fake.task_posts = 0

    def paginate(path, key, params):
        assert path == '/tasks' and key == 'tasks'
        return iter(fake.tasks)

    def post_once(path, payload):
        assert path == '/tasks'
        fake.task_posts += 1
        item = dict(payload[0], id=800000 + fake.task_posts, is_completed=False)
        fake.tasks.append(item)
        return {'_embedded': {'tasks': [{'id': item['id']}]}}

    def get(path, params=None):
        return next((t for t in fake.tasks if path == f"/tasks/{t['id']}"), None)

    monkeypatch.setattr(fake, 'paginate', paginate, raising=False)
    monkeypatch.setattr(fake, 'post_once', post_once, raising=False)
    monkeypatch.setattr(fake, 'get', get)
    return fake


def test_pleep_task_preserves_owner_and_reuses_open(fake, session, pleep_task_env):
    from app.tasks import create_pleep_handoff_task
    cid = fake.add_contact()
    lid = fake.add_lead(cid, settings.pipeline_id, settings.status_new, responsible=15293564)
    task = create_pleep_handoff_task(_ctx(fake, session), lid, 'Нужен человек', 'event-1')
    again = create_pleep_handoff_task(_ctx(fake, session), lid, 'Готов оплатить', 'event-2')
    assert task == again and fake.task_posts == 1
    assert fake.tasks[0]['responsible_user_id'] == 15293564
    assert fake.leads[lid]['responsible_user_id'] == 15293564


def test_pleep_task_replay_completed_and_new_handoff(fake, session, pleep_task_env):
    from app.tasks import create_pleep_handoff_task
    cid = fake.add_contact()
    lid = fake.add_lead(cid, settings.pipeline_id, settings.status_new, responsible=13291175)
    first = create_pleep_handoff_task(_ctx(fake, session), lid, 'Человек', 'event-1')
    fake.tasks[0]['is_completed'] = True
    assert create_pleep_handoff_task(_ctx(fake, session), lid, 'Человек', 'event-1') == first
    second = create_pleep_handoff_task(_ctx(fake, session), lid, 'Снова человек', 'event-2')
    assert second != first and fake.task_posts == 2


def test_pleep_task_timeout_after_accept_recovers(fake, session, pleep_task_env, monkeypatch):
    from app.tasks import create_pleep_handoff_task
    cid = fake.add_contact()
    lid = fake.add_lead(cid, settings.pipeline_id, settings.status_new, responsible=13291175)
    post = fake.post_once

    def timeout(path, payload):
        post(path, payload)
        raise TimeoutError('accepted remotely, lost response')

    monkeypatch.setattr(fake, 'post_once', timeout)
    with pytest.raises(TimeoutError):
        create_pleep_handoff_task(_ctx(fake, session), lid, 'Человек', 'event-1')
    result = create_pleep_handoff_task(_ctx(fake, session), lid, 'Человек', 'event-1')
    assert result == fake.tasks[0]['id'] and fake.task_posts == 1


def test_pleep_task_uncertain_never_posts_again(fake, session, pleep_task_env, monkeypatch):
    from app.tasks import create_pleep_handoff_task
    cid = fake.add_contact()
    lid = fake.add_lead(cid, settings.pipeline_id, settings.status_new, responsible=13291175)

    def timeout(*args):
        fake.task_posts += 1
        raise TimeoutError('unknown result')

    monkeypatch.setattr(fake, 'post_once', timeout)
    with pytest.raises(TimeoutError):
        create_pleep_handoff_task(_ctx(fake, session), lid, 'Человек', 'event-1')
    with pytest.raises(RuntimeError, match='uncertain'):
        create_pleep_handoff_task(_ctx(fake, session), lid, 'Человек', 'event-2')
    assert fake.task_posts == 1


def test_pleep_task_shadow_and_inactive_owner(fake, session, pleep_task_env):
    from app.tasks import create_pleep_handoff_task
    cid = fake.add_contact()
    lid = fake.add_lead(cid, settings.pipeline_id, settings.status_new, responsible=999)
    ctx = _ctx(fake, session)
    ctx.shadow = True
    assert create_pleep_handoff_task(ctx, lid, 'Человек', 'event-1') is None
    ctx.shadow = False
    with pytest.raises(RuntimeError, match='inactive'):
        create_pleep_handoff_task(ctx, lid, 'Человек', 'event-1')
    assert fake.task_posts == 0


def test_pleep_intake_without_handoff_creates_no_task(fake, session, enable_all, monkeypatch):
    from app.intake import process_pleep_intake
    from app import tasks
    cid = fake.add_contact(phone='+15551234567')
    fake.add_lead(cid, settings.pipeline_id, settings.status_new, responsible=13291175)
    def unexpected(*args, **kwargs):
        raise AssertionError('task must only be created at handoff')
    monkeypatch.setattr(tasks, 'create_pleep_handoff_task', unexpected)
    result = process_pleep_intake(_ctx(fake, session), {'phone': '+15551234567'})
    assert result['action'] == 'repeat'


def test_pleep_consumed_repeat_not_recreated_after_completion(fake, session, pleep_task_env):
    from app.tasks import create_pleep_handoff_task
    cid = fake.add_contact()
    lid = fake.add_lead(cid, settings.pipeline_id, settings.status_new, responsible=13291175)
    first = create_pleep_handoff_task(_ctx(fake, session), lid, 'Человек', 'event-1')
    assert create_pleep_handoff_task(_ctx(fake, session), lid, 'Повтор', 'event-2') == first
    fake.tasks[0]['is_completed'] = True
    assert create_pleep_handoff_task(_ctx(fake, session), lid, 'Повтор', 'event-2') == first
    assert fake.task_posts == 1


def test_repeat_task_each_submission_not_each_delivery(fake, session, pleep_task_env):
    from app.tasks import create_repeat_inquiry_task
    cid = fake.add_contact()
    lid = fake.add_lead(cid, settings.pipeline_id, settings.status_new, responsible=15648532)
    ctx = _ctx(fake, session)
    first = create_repeat_inquiry_task(ctx, lid, 'fb:100', 'Facebook')
    assert create_repeat_inquiry_task(ctx, lid, 'fb:100', 'Facebook') == first
    second = create_repeat_inquiry_task(ctx, lid, 'fb:101', 'Facebook')
    assert second != first and fake.task_posts == 2
    assert all(t['responsible_user_id'] == 15648532 for t in fake.tasks)
    fake.tasks[0]['is_completed'] = True
    assert create_repeat_inquiry_task(ctx, lid, 'fb:100', 'Facebook') == first
    assert fake.task_posts == 2


def test_repeat_task_timeout_is_reconciled(fake, session, pleep_task_env, monkeypatch):
    from app.tasks import create_repeat_inquiry_task
    cid = fake.add_contact()
    lid = fake.add_lead(cid, settings.pipeline_id, settings.status_new, responsible=13291175)
    post = fake.post_once
    def lost_response(*args):
        post(*args)
        raise TimeoutError()
    monkeypatch.setattr(fake, 'post_once', lost_response)
    with pytest.raises(TimeoutError):
        create_repeat_inquiry_task(_ctx(fake, session), lid, 'fb:102', 'Facebook')
    assert create_repeat_inquiry_task(_ctx(fake, session), lid, 'fb:102', 'Facebook')
    assert fake.task_posts == 1


def test_repeat_task_unknown_result_not_reposted(fake, session, pleep_task_env, monkeypatch):
    from app.tasks import create_repeat_inquiry_task
    cid = fake.add_contact()
    lid = fake.add_lead(cid, settings.pipeline_id, settings.status_new, responsible=13291175)
    def lost(*args):
        fake.task_posts += 1
        raise TimeoutError()
    monkeypatch.setattr(fake, 'post_once', lost)
    with pytest.raises(TimeoutError):
        create_repeat_inquiry_task(_ctx(fake, session), lid, 'fb:103', 'Facebook')
    with pytest.raises(RuntimeError, match='uncertain'):
        create_repeat_inquiry_task(_ctx(fake, session), lid, 'fb:103', 'Facebook')
    assert fake.task_posts == 1


@pytest.mark.parametrize('status', [142, 143, None])
def test_repeat_intake_task_open_and_closed(fake, session, enable_all, monkeypatch, status):
    from app.models import InboxEvent
    from app import tasks
    cid = fake.add_contact(phone='+15551234567')
    fake.add_lead(cid, settings.pipeline_id, status or settings.status_new,
                  responsible=13291175, closed_at=100 if status else None)
    ev = InboxEvent(source='tilda', event_type='intake', event_key=f'tilda:test-{status}',
                    payload={}, phone='+15551234567', status='pending')
    session.add(ev); session.flush()
    called = []
    monkeypatch.setattr(tasks, 'create_repeat_inquiry_task',
                        lambda ctx, lid, key, channel: called.append((lid, key)) or 999)
    ctx = _ctx(fake, session); ctx.inbox_id = ev.id
    result = process_intake(ctx, {'phone': '+15551234567', 'channel': 'Tilda'})
    assert result['task_id'] == 999
    count = len(fake.leads)
    process_intake(ctx, {'phone': '+15551234567', 'channel': 'Tilda'})
    assert len(fake.leads) == count
    assert called[0] == called[1]
