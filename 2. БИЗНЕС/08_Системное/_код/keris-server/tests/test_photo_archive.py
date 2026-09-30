from io import BytesIO

import pytest
from PIL import Image

from app.photo_archive import LONG_EDGE, NotAnImage, archive_jpeg


def _jpeg(size: tuple[int, int], quality: int = 95) -> bytes:
    img = Image.effect_noise(size, 80).convert("RGB")
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def test_archive_shrinks_phone_frame_and_keeps_jpeg():
    raw = _jpeg((4032, 3024))
    stored = archive_jpeg(raw)
    img = Image.open(BytesIO(stored))
    assert max(img.size) == LONG_EDGE
    assert stored.startswith(b"\xff\xd8")
    assert len(stored) < len(raw) // 5


def test_archive_does_not_enlarge_small_photo():
    raw = _jpeg((800, 600), quality=70)
    stored = archive_jpeg(raw)
    img = Image.open(BytesIO(stored))
    assert img.size == (800, 600)


def test_archive_rejects_text():
    with pytest.raises(NotAnImage):
        archive_jpeg(b"not a photo")
