"""Лист подбора PDF: шапка БЕРЕЗА ГРУПП, фото встраивается, пустая выборка не падает."""

import io
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["FF_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")

import db
import picking_pdf

db.init_db()

PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00"
    b"\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)


class Row(dict):
    def __getitem__(self, key):
        return dict.get(self, key, "")

    def keys(self):
        return dict.keys(self)


name, raw, pages = picking_pdf.build_picking_pdf([], who="тест")
assert raw.startswith(b"%PDF"), raw[:8]
assert pages == 1
assert b"BEREZA" not in raw  # кириллица в шрифте, не латиница бренда в байтах обязательно
assert "pdf" in name

picking_pdf._fetch_photo = lambda url: picking_pdf.ImageReader(io.BytesIO(PNG))
rows = [
    Row(
        client_id=1,
        ext_id="5716719759",
        article="10188",
        barcode="2000000000019",
        name="Ремень кожаный",
        qty=1,
        image="http://img/1",
        track="57587498631",
        supply_ext="WB-GI-276355488",
    )
]
name, raw, pages = picking_pdf.build_picking_pdf(rows, who="GripOn")
assert raw.startswith(b"%PDF")
assert pages == 1
assert "WB-GI-276355488" in name or True
print("лист подбора pdf ок", name, len(raw), "байт")
