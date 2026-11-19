from copy import deepcopy
import json

import httpx
import pytest

from app import shift_handoff as h


NINA, ANGELINA = h.OWNERS["Нина"], h.OWNERS["Ангелина"]


def lead(lid, owner, status, contacts=(), tags=()):
    return {"id": lid, "pipeline_id": 7, "status_id": status, "responsible_user_id": owner,
            "_embedded": {"contacts": [{"id": i} for i in contacts], "companies": [],
                          "tags": [{"name": name} for name in tags]}}


class CRM:
    def __init__(self):
        self.data = {
            "leads": [lead(1, NINA, 12, [11]), lead(2, ANGELINA, 12, [12, 13]),
                      lead(3, NINA, 142), lead(4, 99, 12, [13]),
                      lead(5, NINA, 12, [14], ["MB_MIGRATION_IN_PROGRESS"])],
            "contacts": [{"id": i, "responsible_user_id": NINA, "_embedded": {"leads": [{"id": l} for l in lids]}}
                         for i, lids in [(11, [1]), (12, [2]), (13, [2, 4]), (14, [5]), (15, [])]],
            "talks": [{"talk_id": 100, "entity_type": "lead", "entity_id": 2, "contact_id": 15, "is_in_work": True}],
            "tasks": [{"id": i, "responsible_user_id": NINA, "is_completed": done, "entity_type": "leads",
                       "entity_id": lid, "text": message, "task_type_id": 3946574}
                      for i, lid, done, message in [(21, 3, False, ""), (22, 1, True, ""),
                                                    (23, 1, False, "[MB-AUTO:abc]"), (24, 5, False, "")]],
            "companies": [],
        }
        self.calls = []
        self.fail = None
        self.amo = httpx.Client(base_url="https://example.test", transport=httpx.MockTransport(self.request))

    def get(self, path):
        if path.endswith("/account"):
            return {"subdomain": "mansband"}
        if "/users/" in path:
            return {"rights": {"is_active": True}}
        return {"_embedded": {"pipelines": [{"id": 7, "_embedded": {"statuses": [{"id": 12, "type": 0}]}}]}}

    def request(self, req):
        kind = req.url.path.split("/")[-1]
        rows = deepcopy(self.data[kind])
        if req.method == "PATCH":
            if kind == self.fail:
                return httpx.Response(429)
            changes = json.loads(req.content)
            self.calls.append((kind, deepcopy(changes)))
            for change in changes:
                next(r for r in self.data[kind] if r["id"] == change["id"]).update(change)
            return httpx.Response(200, json={"_embedded": {kind: changes}})
        params = req.url.params
        for key in ("id", "responsible_user_id", "entity_id", "entity_type", "is_completed"):
            choices = {v for k, v in params.items() if k == f"filter[{key}]" or k.startswith(f"filter[{key}][")}
            if choices:
                rows = [r for r in rows if str(int(r[key]) if isinstance(r[key], bool) else r[key]) in choices]
        if "filter[statuses][0][status_id]" in params:
            rows = [r for r in rows if r["status_id"] == 12]
        return httpx.Response(200, json={"_embedded": {kind: rows}})


@pytest.fixture
def crm(monkeypatch):
    monkeypatch.setattr(h.time, "sleep", lambda *_: None)
    monkeypatch.setattr(h, "duty", lambda _: ANGELINA)
    return CRM()


def test_handoff_and_reverse(crm, monkeypatch):
    plan = h.reconcile(crm)
    assert not crm.calls
    assert set(plan.targets["contacts"]) == {11, 12, 15}
    assert set(plan.targets["leads"]) == {1}
    assert set(plan.targets["tasks"]) == {21}
    h.reconcile(crm, True)
    crm.calls.clear()
    h.reconcile(crm, True)
    assert not crm.calls
    monkeypatch.setattr(h, "duty", lambda _: NINA)
    h.reconcile(crm, True)
    assert all(r["responsible_user_id"] == NINA for r in crm.data["leads"] if r["id"] in {1, 2, 3, 5})


def test_retry_after_partial_patch(crm):
    crm.fail = "tasks"
    with pytest.raises(httpx.HTTPStatusError):
        h.reconcile(crm, True)
    crm.calls.clear()
    crm.fail = None
    h.reconcile(crm, True)
    assert [kind for kind, changes in crm.calls] == ["tasks"]


def test_conflicts_checked_at_write_time(crm, monkeypatch):
    plan = h.collect(crm, NINA)
    crm.data["contacts"][0]["_embedded"]["leads"].append({"id": 4})
    h.apply_batch(crm, "contacts", [11], plan, ANGELINA, NINA)
    crm.data["tasks"][0]["is_completed"] = True
    h.apply_batch(crm, "tasks", [21], plan, ANGELINA, NINA)
    monkeypatch.setattr(h, "duty", lambda _: NINA)
    with pytest.raises(RuntimeError, match="shift_changed"):
        h.apply_batch(crm, "leads", [1], plan, ANGELINA, NINA)
    assert not crm.calls


def test_unconfirmed_write_is_not_success(crm, monkeypatch):
    plan = h.collect(crm, NINA)
    monkeypatch.setattr(crm.amo, "patch", lambda path, json: httpx.Response(
        200, request=httpx.Request("PATCH", "https://example.test"),
        json={"_embedded": {"leads": json}}))
    with pytest.raises(RuntimeError, match="readback failed"):
        h.apply_batch(crm, "leads", [1], plan, ANGELINA, NINA)


def test_ambiguous_roster_causes_no_writes(crm, monkeypatch):
    def ambiguous(_):
        raise ValueError("shift_not_unique")
    monkeypatch.setattr(h, "duty", ambiguous)
    with pytest.raises(ValueError, match="shift_not_unique"):
        h.reconcile(crm, True)
    assert not crm.calls


def test_full_page_without_next_link(crm, monkeypatch):
    pages = [[{"talk_id": i} for i in range(250)], [{"talk_id": 250}]]
    def get(path, params):
        return httpx.Response(200, request=httpx.Request("GET", "https://example.test"),
                              json={"_embedded": {"talks": pages[params["page"] - 1]}})
    monkeypatch.setattr(crm.amo, "get", get)
    assert len(list(h.rows(crm, "talks", {}))) == 251


def test_task_new_personal_binding_is_preserved(crm):
    plan = h.collect(crm, NINA)
    next(t for t in crm.data["tasks"] if t["id"] == 21)["text"] = "[MB-AUTO:bound]"
    h.apply_batch(crm, "tasks", [21], plan, ANGELINA, NINA)
    assert not crm.calls


def test_contact_task_rechecks_parent_owner(crm):
    task = next(t for t in crm.data["tasks"] if t["id"] == 21)
    task.update(entity_type="contacts", entity_id=12)
    plan = h.collect(crm, NINA)
    assert 21 in plan.targets["tasks"]
    next(c for c in crm.data["contacts"] if c["id"] == 12)["responsible_user_id"] = 99
    h.apply_batch(crm, "tasks", [21], plan, ANGELINA, NINA)
    assert not crm.calls


@pytest.mark.parametrize("today,expected", [
    (["TRUE,FALSE"], NINA), (["FALSE,TRUE"], ANGELINA),
    (["FALSE,FALSE"], None), (["TRUE,TRUE"], None), ([], None),
    (["TRUE,FALSE", "FALSE,TRUE"], None),
])
def test_roster_uses_moscow_date_and_one_checkbox(monkeypatch, today, expected):
    from datetime import datetime, timezone
    from app import campaign

    instant = datetime(2026, 11, 19, 21, 1, tzinfo=timezone.utc)
    lines = ["График", "Дата,День,Нина,Ангелина", "19.11.2026,чт,FALSE,TRUE"]
    lines.extend(f"20.11.2026,пт,{flags}" for flags in today)
    response = httpx.Response(200, request=httpx.Request("GET", "https://example.test"),
                              text="\n".join(lines))
    monkeypatch.setattr(campaign.httpx, "get", lambda *args, **kwargs: response)
    if expected is None:
        with pytest.raises(ValueError, match="shift_"):
            campaign.duty(instant)
    else:
        assert campaign.duty(instant) == expected
