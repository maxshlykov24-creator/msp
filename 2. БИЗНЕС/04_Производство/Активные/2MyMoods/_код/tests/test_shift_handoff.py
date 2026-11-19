import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import assign_shift as shift
import shift_roster as roster


class ShiftHandoffTests(unittest.TestCase):
    def setUp(self):
        self.on = shift.lib.USER_KRISTINA
        self.off = shift.lib.USER_TANYA

    def test_no_tasks_still_moves_open_sale_and_conversation_contact(self):
        amo = Mock()
        lead = {"id": 11, "responsible_user_id": self.off,
                "_embedded": {"contacts": [{"id": 22}], "companies": []}}
        talk = {"entity_id": 11, "entity_type": "lead", "contact_id": 33, "is_in_work": True}

        def records(client, kind, query):
            return [talk] if kind == "talks" else []

        def lookup(client, kind, ids, **kwargs):
            return [{"id": i, "responsible_user_id": self.off, "_embedded": {"leads": [{"id": 11}]}}
                    for i in ids]

        with (patch.object(shift, "sales", return_value=[lead]),
              patch.object(shift, "records", side_effect=records),
              patch.object(shift, "by_ids", side_effect=lookup)):
            changes = shift.plan(amo, self.on, self.off)
        self.assertEqual(changes, {"leads": {11}, "tasks": set(), "contacts": {22, 33}, "companies": set()})

    def test_shared_contact_is_preserved(self):
        contacts = [{"id": 22, "_embedded": {"leads": [{"id": 11}, {"id": 77}]}}]
        other = {"id": 77, "pipeline_id": 400, "status_id": 555, "responsible_user_id": 3}
        with patch.object(shift, "by_ids", return_value=[other]):
            guarded = shift.safe_relations(Mock(), contacts, self.on, self.off, [11])
        self.assertEqual(guarded, {22})

    def test_late_schedule_change_prevents_writes(self):
        amo = Mock()
        task = {"id": 44, "is_completed": False, "responsible_user_id": self.off}
        with (patch.object(shift, "by_ids", return_value=[task]),
              patch.object(shift, "roster", return_value=(self.off, "Таня")),
              self.assertRaisesRegex(RuntimeError, "График изменился")):
            shift.apply_group(amo, "tasks", [44], self.on, self.off)
        amo.req.assert_not_called()

    def test_completed_task_and_closed_sale_keep_their_owner(self):
        amo = Mock()
        task = {"id": 44, "is_completed": True, "responsible_user_id": self.off}
        lead = {"id": 11, "pipeline_id": shift.lib.PIPELINE_SALES_NEW,
                "status_id": 142, "responsible_user_id": self.off}
        for kind, row in (("leads", lead), ("tasks", task)):
            with patch.object(shift, "by_ids", return_value=[row]):
                shift.apply_group(amo, kind, [row["id"]], self.on, self.off)
        amo.req.assert_not_called()

    def test_api_acknowledgement_requires_readback(self):
        amo = Mock()
        task = {"id": 44, "is_completed": False, "responsible_user_id": self.off}
        amo.req.return_value = (200, {"_embedded": {"tasks": [{"id": 44}]}})
        with (patch.object(shift, "by_ids", return_value=[task]),
              patch.object(shift, "roster", return_value=(self.on, "Кристина")),
              self.assertRaisesRegex(RuntimeError, "не подтверждён")):
            shift.apply_group(amo, "tasks", [44], self.on, self.off)

    def test_roster_skips_ambiguous_dates_and_reordered_columns(self):
        day = date(2026, 11, 19)
        row = [roster._serial(day), "чт", True, False]
        header = ["Дата", "День", "Кристина", "Таня"]
        for values in ([header, row, row], [header, row[:2] + [False, False]],
                       [header, row[:2] + [True, True]], [header[:2] + list(reversed(header[2:])), row]):
            with patch.object(roster, "_api", return_value={"values": values}), patch.dict(roster._CACHE):
                self.assertIsNone(roster.duty(day, force=True)[0])


if __name__ == "__main__":
    unittest.main()
