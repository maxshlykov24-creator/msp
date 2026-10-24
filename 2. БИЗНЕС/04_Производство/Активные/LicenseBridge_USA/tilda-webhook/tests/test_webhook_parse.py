from app.webhooks import _parse_kommo


def test_parse_add_lead():
    fields = {
        "leads[add][0][id]": "555",
        "leads[add][0][status_id]": "108112044",
        "account[id]": "34679511",
    }
    account, events = _parse_kommo(fields)
    assert account == "34679511"
    assert {"type": "add_lead", "entity_id": 555} in events


def test_parse_status_and_contact():
    fields = {
        "leads[status][0][id]": "10",
        "leads[status][0][status_id]": "142",
        "contacts[update][0][id]": "20",
    }
    _, events = _parse_kommo(fields)
    types = {(e["type"], e["entity_id"]) for e in events}
    assert ("status_lead", 10) in types
    assert ("update_contact", 20) in types
    status_ev = next(e for e in events if e["type"] == "status_lead")
    assert status_ev["status_id"] == 142


def test_pleep_webhook_preserves_handoff_request(monkeypatch):
    import contextlib
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app import webhooks
    from app.config import settings

    received = []
    @contextlib.contextmanager
    def scope():
        yield object()
    monkeypatch.setattr(settings, 'pleep_api_key', 'test-key')
    monkeypatch.setattr(webhooks, 'session_scope', scope)
    monkeypatch.setattr(webhooks, 'enqueue', lambda s, source, kind, key, payload, **kw:
                        (received.append(payload) or 123, True))
    app = FastAPI()
    app.include_router(webhooks.router)
    client = TestClient(app)
    assert client.post('/pleep/sync', json={'phone': '+15551234567'}).status_code == 401
    response = client.post('/pleep/sync', headers={'X-Api-Key': 'test-key'},
                           json={'phone': '+15551234567', 'task_text': ' Нужен менеджер '})
    assert response.status_code == 202
    assert received[0]['task_text'] == 'Нужен менеджер'
    assert response.json()['status'] == 'queued'
    assert response.json()['task_requested'] is True


def test_pleep_identity_only_and_invalid_native_id(monkeypatch):
    import contextlib
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app import webhooks
    from app.config import settings
    received=[]
    @contextlib.contextmanager
    def scope(): yield object()
    monkeypatch.setattr(settings,'pleep_api_key','test-key')
    monkeypatch.setattr(webhooks,'session_scope',scope)
    monkeypatch.setattr(webhooks,'enqueue',lambda s,src,typ,key,payload,**kw: (received.append(payload) or 1,True))
    app=FastAPI();app.include_router(webhooks.router);client=TestClient(app)
    headers={'X-Api-Key':'test-key'}
    assert client.post('/pleep/sync',headers=headers,json={'thread_id':'thread_native','kommo_lead_id':'123'}).status_code==202
    assert received[0]['thread_id']=='thread_native'
    assert client.post('/pleep/sync',headers=headers,json={'kommo_lead_id':'{{unmapped}}'}).status_code==400
    assert client.post('/pleep/sync',headers=headers,json={}).status_code==400
