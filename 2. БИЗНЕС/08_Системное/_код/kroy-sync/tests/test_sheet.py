import unittest

import sheet_sync


class SheetTests(unittest.TestCase):
    def test_date_serial_and_text(self):
        self.assertEqual(sheet_sync.iso_date(45937), "2025-10-07")
        self.assertEqual(sheet_sync.iso_date("07.10.2026"), "2026-10-07")
        self.assertEqual(sheet_sync.iso_date("2026-10-07"), "2026-10-07")
        self.assertEqual(sheet_sync.iso_date(""), "")

    def test_second_look_posts_same_row_once(self):
        digest = "abc"
        self.assertEqual(sheet_sync.next_step("", "", digest, True), "wait")
        self.assertEqual(sheet_sync.next_step("проверяю", digest, digest, True), "post")
        self.assertEqual(sheet_sync.next_step("проведено. Рулон", digest, digest, True), "skip")
        self.assertEqual(sheet_sync.next_step("укажи дату", "old", digest, False), "ask")
        self.assertEqual(sheet_sync.next_step("ошибка: обрыв", digest, digest, True), "post")


if __name__ == "__main__":
    unittest.main()
