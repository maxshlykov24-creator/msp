import unittest

import core
import sheet_sync


class ConsumptionTests(unittest.TestCase):
    def test_meters_add_up_by_color(self):
        norms = {
            "Футболка сердце — Чёрный": {"S": 1.2, "M": 1.3, "L": None},
            "Лонгслив скимс — Чёрный": {"S": 2, "M": None, "L": None},
        }
        lines = [
            ("Футболка сердце — Чёрный", "S", 10),
            ("Футболка сердце — Чёрный", "M", 10),
            ("Лонгслив скимс — Чёрный", "S", 4),
        ]
        plan = core.plan_consumption(lines, norms)
        self.assertEqual(plan["missing"], [])
        self.assertEqual(plan["by_color"]["Чёрный"], 33.0)

    def test_empty_norm_is_not_zero(self):
        norms = {"Футболка сердце — Чёрный": {"S": None, "M": 1, "L": None}}
        plan = core.plan_consumption([("Футболка сердце — Чёрный", "S", 5)], norms)
        self.assertEqual(plan["by_color"], {})
        self.assertIn("Футболка сердце — Чёрный, S", plan["missing"])

    def test_blank_cell_and_loss_text(self):
        self.assertIsNone(sheet_sync.meter(""))
        self.assertIsNone(sheet_sync.meter(None))
        self.assertEqual(sheet_sync.meter("1,25"), 1.25)
        plan = core.plan_consumption(
            [("Футболка сердце — Чёрный", "S", 2)],
            {"Футболка сердце — Чёрный": {"S": 1.5, "M": None, "L": None}},
        )
        text = sheet_sync.loss_text("supply", "00012", plan)
        self.assertIn("приёмке 00012", text)
        self.assertIn("1,5 м на штуку", text)
        self.assertIn("Итого Рулон Чёрный: 3 м", text)


if __name__ == "__main__":
    unittest.main()
