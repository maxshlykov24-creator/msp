"""Shipment-period coverage and return allocation; no production API calls."""
import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import collect_dashboard as c


def ref(kind, key):
    return {"meta": {"type": kind, "href": "https://test/" + kind + "/" + key}}


def document(key, order="unpaid", moment="2026-10-01 12:00:00", qty=1, amount=100):
    return {"id": key, "name": key, "moment": moment, "applicable": True,
            "customerOrder": ref("customerorder", order), "sum": amount,
            "rate": {"currency": ref("currency", "rub")},
            "positions": {"meta": {"size": 1}, "rows": [
                {"assortment": ref("product", "p"), "price": 100,
                 "quantity": qty, "discount": 0, "vat": 0}]}}


class Dates(unittest.TestCase):
    def test_stream_fetches_next_page_only_when_consumed(self):
        class API:
            calls = 0
            def req(self, path, params):
                self.calls += 1
                return 200, {"rows": [{"id": str(params["offset"])}], "meta": {"size": 2}}
        api = API()
        rows = c.required_rows(api, "/test", stream=True)
        self.assertEqual(api.calls, 0)
        self.assertEqual(next(rows), {"id": "0"})
        self.assertEqual(api.calls, 1)
        self.assertEqual(list(rows), [{"id": "1"}])
        self.assertEqual(c.required_rows(API(), "/test"), [{"id": "0"}, {"id": "1"}])

    def collect(self, demands, returns=(), orders=None):
        orders = orders or [{"id": "unpaid", "buy": False, "paid_date": None,
                             "lines": [{"id": "p", "name": "Item"}]}]
        snap = {"orders": copy.deepcopy(orders)}
        before = copy.deepcopy(snap["orders"])
        ms = type("ReadOnly", (), {})()
        ms._discount_history = type("Prices", (), {
            "resolve": lambda *a: ({"value": 102, "currency": "rub"}, "historical_catalogue", "")})()
        def rows(ms, path, *args, **kwargs):
            if path == "/entity/demand": return demands
            if path == "/entity/salesreturn": return returns
            raise AssertionError("Unexpected API read: " + path)
        with patch.object(c, "required_rows", rows):
            c.enrich_shipment_discounts(snap, ms)
        self.assertEqual(snap["orders"], before)
        return snap["shipment_discounts"]

    def test_unpaid_and_zero_value_order_are_collected(self):
        result = self.collect([document("d")])
        self.assertIn("shipment", result["date_bases"])
        self.assertEqual(result["documents"][0]["shipment_date"], "2026-10-01")
        self.assertEqual(result["documents"][0]["lines"][0]["discount_kopecks"], 2)

    def test_zero_value_shipment_keeps_catalogue_discount(self):
        row = document("free", amount=0)
        row["positions"]["rows"][0]["price"] = 0
        result = self.collect([row])
        self.assertEqual(result["documents"][0]["lines"][0]["discount_kopecks"], 102)

    def test_mismatching_document_total_is_a_visible_gap(self):
        result = self.collect([document("bad", amount=999)])
        self.assertFalse(result["documents"])
        self.assertIn("Сумма позиций", result["gaps"][0]["reason"])
        self.assertEqual(result["gaps"][0]["shipment_date"], "2026-10-01")

    def test_november_return_keeps_october_shipment_date(self):
        ret = document("r", moment="2026-11-02 12:00:00")
        ret["demand"] = ref("demand", "d")
        result = self.collect([document("d")], [ret])
        row = result["documents"][1]
        self.assertEqual(row["date"], "2026-11-02")
        self.assertEqual(row["shipment_date"], "2026-10-01")

    def test_partial_returns_never_exceed_original_discount(self):
        returns = []
        for i, qty in enumerate([.25, .25, .5]):
            row = document("r" + str(i), qty=qty, amount=qty * 100)
            row["demand"] = ref("demand", "d")
            returns.append(row)
        result = self.collect([document("d")], returns)
        self.assertFalse(result["gaps"])
        self.assertEqual(sum(r["lines"][0]["discount_kopecks"] for r in result["documents"][1:]), 2)

    def test_excess_return_is_a_gap(self):
        row = document("r", qty=2, amount=200)
        row["demand"] = ref("demand", "d")
        result = self.collect([document("d")], [row])
        self.assertEqual(len(result["documents"]), 1)
        self.assertEqual(result["gaps"][0]["shipment_date"], "2026-10-01")

    def test_missing_order_is_visible_as_dated_gap(self):
        result = self.collect([document("d", order="missing")])
        self.assertEqual(result["gaps"][0]["shipment_date"], "2026-10-01")
        self.assertEqual(result["unlinked"]["demand"], 1)

    def test_missing_original_never_uses_return_date_as_shipment_date(self):
        ret = document("r", order="missing", moment="2026-11-02 12:00:00")
        ret["demand"] = ref("demand", "absent")
        result = self.collect([], [ret])
        self.assertEqual(result["gaps"][0]["shipment_date"], "")


if __name__ == "__main__":
    unittest.main()
