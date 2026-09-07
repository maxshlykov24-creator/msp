from __future__ import annotations

import unittest

from app.api import _div, _period_plans
from app.collector import _response_deltas, _state_refresh_due, _status_after


class MetricFormulaTests(unittest.TestCase):
    def test_division_by_zero_is_zero(self) -> None:
        self.assertEqual(_div(100, 0), 0)

    def test_partial_month_plan_is_proportional(self) -> None:
        store = {
            "default": {"new_leads": 310, "conv_lead_deal": 75, "avg_check": 15000},
            "by_month": {},
            "by_mgr": {},
        }
        result = _period_plans(store, "2026-07-01", "2026-07-10")
        self.assertEqual(result["new_leads"], 100)
        self.assertEqual(result["conv_lead_deal"], 75)
        self.assertEqual(result["avg_check"], 15000)

    def test_status_event_formats(self) -> None:
        event = {
            "value_after": [
                {"lead_status": {"id": 84291534, "pipeline_id": 10694702}}
            ]
        }
        self.assertEqual(_status_after(event), (84291534, 10694702))
        self.assertEqual(
            _status_after({"value_after": {"status_id": "142", "pipeline_id": "1"}}),
            (142, 1),
        )

    def test_state_refresh_accepts_aware_timestamp(self) -> None:
        class Row:
            value = "2026-07-20 09:00:00+03"

        class Db:
            @staticmethod
            def get(*_args):
                return Row()

        self.assertIsInstance(_state_refresh_due(Db(), "key", 6), bool)

    def test_response_pairs_use_next_outgoing(self) -> None:
        events = {
            10: [
                {"id": 1, "type": "incoming_chat_message", "created_at": 1000},
                {"id": 2, "type": "incoming_chat_message", "created_at": 1010},
                {"id": 3, "type": "outgoing_chat_message", "created_at": 1120},
                {"id": 4, "type": "incoming_chat_message", "created_at": 1200},
                {"id": 5, "type": "outgoing_chat_message", "created_at": 1500},
            ]
        }
        rows = _response_deltas(events)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][2], 2)
        self.assertTrue(rows[0][3])
        self.assertEqual(rows[1][2], 5)
        self.assertFalse(rows[1][3])

if __name__ == "__main__":
    unittest.main()
