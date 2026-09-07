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
