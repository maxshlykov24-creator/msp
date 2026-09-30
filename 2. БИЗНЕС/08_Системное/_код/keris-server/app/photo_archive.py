"""Архив снимка: одна понятная копия, без оригинала с телефона.

Длинная сторона не больше 1600 точек. Этого хватает, чтобы на экране
было видно морду и стрижку. Печать и исходник телефона не храним.
"""
from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

LONG_EDGE = 1600
QUALITY = 80


class NotAnImage(ValueError):
    pass


def archive_jpeg(data: bytes) -> bytes:
    if not data:
        raise NotAnImage("пустой файл")
    try:
        img = Image.open(BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise NotAnImage("это не фото") from exc
    img = ImageOps.exif_transpose(img)
    if img.mode != "RGB":
        img = img.convert("RGB")
    img.thumbnail((LONG_EDGE, LONG_EDGE), Image.Resampling.LANCZOS)
    out = BytesIO()
    img.save(out, format="JPEG", quality=QUALITY, optimize=True, progressive=True)
    return out.getvalue()
