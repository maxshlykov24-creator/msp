from app.models import DupManual, InboxEvent
from app.rollout import STAGE_DEALS, set_stage
from app.webhooks import _overview, _status_html


def test_overview_and_html_render(session):
    session.add(InboxEvent(event_key="k1", source="tilda", event_type="intake",
                           payload={}, status="pending"))
    session.add(DupManual(kind="contact", tag="mc_01", entity_ids=[1, 2],
                          reason="rich_or_attachments", status="open"))
    set_stage(session, STAGE_DEALS, note="тест")
    session.flush()

    data = _overview(session)
    assert data["rollout"]["stage_name"] == "deals"
    assert data["inbox"]["pending"] == 1
    assert data["manual_queue_open"]["rich_or_attachments"] == 1

    html = _status_html(data)
    assert "LicenseBridge Hub" in html
    assert "deals" in html
