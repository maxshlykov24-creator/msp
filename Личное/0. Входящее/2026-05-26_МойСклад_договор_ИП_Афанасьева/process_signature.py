#!/usr/bin/env python3
"""
Подпись: #FFFFFF + синяя ручка (цвет с фото, нажим — с плотности штриха).
Без копирования серого ореола. Галочка сверху справа сохраняется.
"""

from pathlib import Path

import cv2
import numpy as np
from PIL import Image

DIR = Path(__file__).parent
SRC = DIR / "подпись_исходник.png"
OUT_MS = DIR / "подпись_МойСклад.png"
DESKTOP = Path("/Users/max/Desktop/подпись_ИП_Афанасьева_МойСклад.png")

ASSET = Path(
    "/Users/max/.cursor/projects/Users-max-Desktop-CURSOR/assets/"
    "______________2026-05-26___15.36.57-85b22e9e-06ac-4cf4-8822-ac64de7fbff2.png"
)
DL = Path("/Users/max/Downloads/подпись.png")


def is_ink(bgr, hsv):
    b, g, r = cv2.split(bgr)
    return (
        (b.astype(np.int16) - r.astype(np.int16) > 4)
        & (b.astype(np.int16) - g.astype(np.int16) > 2)
        & (hsv[:, :, 1] > 14)
        & (hsv[:, :, 2] > 20)
    )


def all_ink_parts(mask: np.ndarray, min_area: int = 8) -> np.ndarray:
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
    out = np.zeros(mask.shape, dtype=bool)
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] >= min_area:
            out |= labels == i
    return out


def bbox_from_ink(ink, pad_ratio=0.08):
    ys, xs = np.where(ink)
    if len(xs) == 0:
        h, w = ink.shape
        return 0, 0, w, h
    y0, y1 = ys.min(), ys.max()
    x0, x1 = xs.min(), xs.max()
    py = int((y1 - y0 + 1) * pad_ratio) + 18
    px = int((x1 - x0 + 1) * pad_ratio) + 18
    h, w = ink.shape
    return (
        max(0, x0 - px),
        max(0, y0 - py),
        min(w, x1 + px + 1),
        min(h, y1 + py + 1),
    )


def sample_pen(bgr, hsv) -> np.ndarray:
    b, g, r = cv2.split(bgr)
    s = hsv[:, :, 1]
    good = (s > 30) & (b.astype(np.int16) - r.astype(np.int16) > 8)
    if not np.any(good):
        return np.array([178.0, 56.0, 28.0], dtype=np.float32)
    return np.median(bgr[good].reshape(-1, 3), axis=0).astype(np.float32)


def render_blue(density: np.ndarray) -> np.ndarray:
    """Насыщенная синяя ручка, нажим = плотность с фото."""
    d = np.clip(density * 1.12, 0.36, 1.0)
    h = np.full(d.shape, 112.0, dtype=np.float32)  # оттенок синей ручки
    s = np.clip(90.0 + d * 165.0, 0, 255)
    v = np.clip(70.0 + d * 150.0, 0, 220)
    hsv = cv2.merge([h, s, v]).astype(np.uint8)
    return cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)


def process(src_path: Path) -> Image.Image:
    bgr = cv2.imread(str(src_path))
    if bgr is None:
        raise SystemExit(f"Не удалось прочитать: {src_path}")

    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    ink_full = all_ink_parts(is_ink(bgr, hsv))
    x0, y0, x1, y1 = bbox_from_ink(ink_full)

    crop = bgr[y0:y1, x0:x1].copy()
    hsv_c = hsv[y0:y1, x0:x1]
    ch, cw = crop.shape[:2]

    tick_zone = np.zeros((ch, cw), dtype=bool)
    tick_zone[0 : int(ch * 0.42), int(cw * 0.52) :] = True

    ink = all_ink_parts(is_ink(crop, hsv_c))
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY).astype(np.float32)
    density = np.clip((255.0 - gray) / 255.0, 0, 1)

    # Галочка бледнее — чуть поднимаем плотность в её зоне
    density = density.copy()
    boost = tick_zone & ink & (density > 0.05)
    density[boost] = np.clip(density[boost] * 1.25 + 0.06, 0, 1)

    blue = render_blue(density)

    out = np.full((ch, cw, 3), 255, dtype=np.uint8)
    draw = ink & (density >= 0.10)
    draw |= tick_zone & ink & (density >= 0.07)
    out[draw] = blue[draw]

    rgb = cv2.cvtColor(out, cv2.COLOR_BGR2RGB)
    pil = Image.fromarray(rgb)
    side = max(pil.size)
    sq = Image.new("RGB", (side, side), (255, 255, 255))
    sq.paste(pil, ((side - pil.width) // 2, (side - pil.height) // 2))
    return sq


def main():
    for candidate in (SRC, ASSET, DL):
        if candidate.exists():
            src = candidate
            break
    else:
        raise SystemExit("Нет исходника подписи")

    if src != SRC:
        import shutil

        shutil.copy2(src, SRC)

    img = process(SRC)
    img.save(OUT_MS, "PNG", optimize=True)
    img.save(DESKTOP, "PNG", optimize=True)

    import cv2 as cv

    o = cv.imread(str(DESKTOP))
    g = cv.cvtColor(o, cv.COLOR_BGR2GRAY)
    h = cv.cvtColor(o, cv.COLOR_BGR2HSV)
    stroke = g < 250
    grayish = stroke & (h[:, :, 1] < 25)
    b, _, r = cv.split(o)
    print(f"OK: {DESKTOP} ({img.size[0]}x{img.size[1]} px)")
    print(f"  серых в штрихе: {grayish.sum()}, B>R: {(b[stroke]>r[stroke]).mean():.2f}")
    n, _, stats, _ = cv.connectedComponentsWithStats(stroke.astype(np.uint8), 8)
    print(f"  контуров: {n-1}, площади: {[int(stats[i, cv.CC_STAT_AREA]) for i in range(1, n)]}")


if __name__ == "__main__":
    main()
