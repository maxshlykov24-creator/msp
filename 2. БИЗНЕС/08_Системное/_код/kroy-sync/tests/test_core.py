import os
import unittest

import core
import exchange


class Cut(unittest.TestCase):
    def test_key_stable(self):
        a = core.fact_key("2026-09-26", "Футболка сердце — Чёрный", {"S": 42, "M": 42, "L": 0}, 2)
        b = core.fact_key("2026-09-26", "Футболка сердце — Чёрный", {"M": 42, "S": 42}, 2)
        self.assertEqual(a, b)

    def test_two_colors_two_keys(self):
        a = core.fact_key("2026-09-22", "Лонг скимс — Чёрный", {"S": 120, "M": 120}, 3)
        b = core.fact_key("2026-09-22", "Лонг скимс — Тёмно-коричневый", {"S": 232, "M": 232}, 5)
        self.assertNotEqual(a, b)

    def test_short_and_repeat(self):
        self.assertEqual(core.plan_cut(roll_stock=0, rolls=2, loss_exists=False, enter_exists=False), "рулонов не хватает")
        self.assertEqual(core.plan_cut(roll_stock=None, rolls=2, loss_exists=False, enter_exists=False), "остаток рулона не прочитан")
        self.assertEqual(core.plan_cut(roll_stock=2, rolls=2, loss_exists=True, enter_exists=True), "уже проведено")
        self.assertEqual(core.plan_cut(roll_stock=3, rolls=2, loss_exists=False, enter_exists=False), "провести")

    def test_color_must_match_name(self):
        self.assertEqual(core.check_color("Футболка сердце — Чёрный", "Чёрный"), "")
        self.assertTrue(core.check_color("Футболка сердце — Чёрный", "Бордовый"))


class Stock(unittest.TestCase):
    def test_available_is_quantity_not_stock_plus_reserve(self):
        self.assertEqual(core.available_qty(41, 1, 40), 40)
        self.assertEqual(core.available_qty(0, 24, -24), 0)
        self.assertIsNone(core.available_qty(None, None, None))

    def test_ship_does_not_drop_available_twice(self):
        self.assertEqual(core.available_after_ship(10, 2, 2), 8)

    def test_push_skips_rolls_clash_and_empty(self):
        rows = [
            {"folder": "Рулоны", "article": "ROL-1", "barcode": "111", "stock": 5, "reserve": 0, "quantity": 5},
            {"folder": "YUJI", "barcode": "", "stock": 5, "reserve": 0, "quantity": 5},
            {"folder": "YUJI", "barcode": "222", "stock": 10, "reserve": 2, "quantity": 8},
            {"folder": "YUJI", "barcode": "333", "stock": None, "reserve": None, "quantity": None},
            {"folder": "YUJI", "barcode": "444", "stock": 1, "reserve": 0, "quantity": 1},
        ]
        wb = {
            "222": [{"chrtId": 1}],
            "333": [{"chrtId": 2}],
            "444": [{"chrtId": 3}, {"chrtId": 4}],
        }
        lines, note = core.push_lines(rows, wb, ["Мой склад"])
        self.assertEqual(note, "ok")
        self.assertEqual(lines, [{"chrtId": 1, "amount": 8, "barcode": "222"}])

    def test_two_warehouses_stop(self):
        lines, note = core.push_lines([], {}, ["a", "b"])
        self.assertEqual(lines, [])
        self.assertIn("остановлен", note)


class Switch(unittest.TestCase):
    def setUp(self):
        self.saved = os.environ.get("SYNC_ENABLED")
        self.ozon = os.environ.get("OZON_API_KEY")
        self.confirm = os.environ.get("CONFIRM_DISABLE_NATIVE")
        os.environ["SYNC_ENABLED"] = "0"
        os.environ["OZON_API_KEY"] = ""
        os.environ["CONFIRM_DISABLE_NATIVE"] = "0"
        os.environ["CUTOVER_DATE"] = ""

    def tearDown(self):
        for key, value in (
            ("SYNC_ENABLED", self.saved),
            ("OZON_API_KEY", self.ozon),
            ("CONFIRM_DISABLE_NATIVE", self.confirm),
        ):
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_mode_off_does_not_touch_marketplaces(self):
        self.assertEqual(exchange.customer_order("2026-09-30 09:10:00", False), "выключено")
        self.assertEqual(exchange.shipment(False), "выключено")
        self.assertEqual(exchange.fbo("WB-1", False), "выключено")
        self.assertEqual(exchange.cancel(True), "выключено")
        self.assertEqual(exchange.ozon(), "нет ключа Ozon")
        self.assertEqual(exchange.disable_native(), "не гасим: режим выключен")
        lines, note = exchange.push_stock(
            [{"folder": "YUJI", "barcode": "222", "quantity": 8, "stock": 8, "reserve": 0}],
            {"222": [{"chrtId": 1}]},
            ["Мой склад"],
        )
        self.assertEqual(lines, [])
        self.assertEqual(note, "выключено")

    def test_history_not_pulled_without_cutover(self):
        os.environ["SYNC_ENABLED"] = "1"
        self.assertEqual(exchange.customer_order("2026-09-01 10:00:00", False), "нет даты включения")
        os.environ["CUTOVER_DATE"] = "2026-10-01"
        self.assertEqual(exchange.customer_order("2026-09-30 09:10:00", False), "до даты включения")
        self.assertEqual(exchange.customer_order("2026-10-01 09:10:00", True), "заказ уже есть")

    def test_disable_native_still_blocked_even_if_flags_on(self):
        os.environ["SYNC_ENABLED"] = "1"
        os.environ["CONFIRM_DISABLE_NATIVE"] = "1"
        with self.assertRaises(RuntimeError):
            exchange.disable_native()


if __name__ == "__main__":
    unittest.main()
