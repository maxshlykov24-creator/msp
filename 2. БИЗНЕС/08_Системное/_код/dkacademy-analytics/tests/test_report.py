from __future__ import annotations

import unittest

from scripts.render_audit_report import render


class AuditReportTests(unittest.TestCase):
    def test_report_contains_verdict_and_checksum(self) -> None:
        payload = {
            "generated_at": "2026-07-20T10:00:00+03:00",
            "formula_version": "2026-07-20",
            "ok": True,
            "periods": [{
                "period": {"from": "2026-06-01", "to": "2026-06-30"},
                "metrics": {"new_leads": 10},
                "dashboard": {"new_leads": 10},
                "dashboard_minus_audit": {"new_leads": 0},
                "invariants": {"unique_new_leads": True},
                "details": {"new_lead_ids": [1, 2]},
                "ok": True,
            }],
        }
        result = render(payload)
        self.assertIn("0</strong>", result)
        self.assertIn("Контрольная сумма SHA-256", result)
        self.assertIn("2026-06-01", result)


if __name__ == "__main__":
    unittest.main()
