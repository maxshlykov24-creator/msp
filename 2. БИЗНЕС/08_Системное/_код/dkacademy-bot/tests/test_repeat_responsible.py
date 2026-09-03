"""Выбор последней успешной сделки для автоназначения ответственного."""
from __future__ import annotations

from app.amocrm_client import pick_last_won_lead
from app.repeat_responsible import resolve_responsible_source

SALES = 10694702
REPEAT = 10697570
WON = 142
LOST = 143


def test_prefers_later_repeat_win_over_earlier_sales_win():
    best = pick_last_won_lead(
        [
            {"id": 1, "pipeline_id": SALES, "status_id": WON, "closed_at": 100, "responsible_user_id": 11},
            {"id": 2, "pipeline_id": REPEAT, "status_id": WON, "closed_at": 200, "responsible_user_id": 22},
        ],
        pipeline_ids={SALES, REPEAT},
        status_id=WON,
    )
    assert best is not None
    assert best["id"] == 2
    assert best["responsible_user_id"] == 22


def test_prefers_later_sales_win_over_earlier_repeat_win():
    best = pick_last_won_lead(
        [
            {"id": 1, "pipeline_id": REPEAT, "status_id": WON, "closed_at": 100, "responsible_user_id": 22},
            {"id": 2, "pipeline_id": SALES, "status_id": WON, "closed_at": 300, "responsible_user_id": 11},
        ],
        pipeline_ids={SALES, REPEAT},
        status_id=WON,
    )
    assert best is not None
    assert best["id"] == 2
    assert best["responsible_user_id"] == 11


def test_repeat_only_migrated_client():
    best = pick_last_won_lead(
        [
            {"id": 5, "pipeline_id": REPEAT, "status_id": WON, "closed_at": 150, "responsible_user_id": 33},
            {"id": 9, "pipeline_id": REPEAT, "status_id": 84292042, "closed_at": None, "responsible_user_id": 1},
        ],
        pipeline_ids={SALES, REPEAT},
        status_id=WON,
        exclude_lead_id=9,
    )
    assert best is not None
    assert best["id"] == 5
    assert best["responsible_user_id"] == 33


def test_ignores_lost_and_other_pipelines():
    best = pick_last_won_lead(
        [
            {"id": 1, "pipeline_id": SALES, "status_id": LOST, "closed_at": 400, "responsible_user_id": 11},
            {"id": 2, "pipeline_id": 999, "status_id": WON, "closed_at": 500, "responsible_user_id": 44},
        ],
        pipeline_ids={SALES, REPEAT},
        status_id=WON,
    )
    assert best is None


def test_excludes_current_lead():
    best = pick_last_won_lead(
        [
            {"id": 7, "pipeline_id": REPEAT, "status_id": WON, "closed_at": 900, "responsible_user_id": 55},
            {"id": 3, "pipeline_id": SALES, "status_id": WON, "closed_at": 100, "responsible_user_id": 11},
        ],
        pipeline_ids={SALES, REPEAT},
        status_id=WON,
        exclude_lead_id=7,
    )
    assert best is not None
    assert best["id"] == 3


DUMP = {9490530, 10088354}


def test_resolve_prefers_won_over_contact():
    assert resolve_responsible_source(13611594, 13803674, DUMP) == ("won", 13611594)


def test_resolve_contact_when_no_won():
    assert resolve_responsible_source(None, 13803674, DUMP) == ("contact", 13803674)


def test_resolve_contact_when_won_is_dump():
    assert resolve_responsible_source(10088354, 13793718, DUMP) == ("contact", 13793718)


def test_resolve_none_when_both_dump():
    assert resolve_responsible_source(9490530, 10088354, DUMP) is None


def test_resolve_none_when_nothing():
    assert resolve_responsible_source(None, None, DUMP) is None
