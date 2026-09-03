#!/usr/bin/env python3
"""Печать: читаемость как на фото, белый фон, без апскейла (не размывает)."""

from pathlib import Path

import cv2
import numpy as np
from PIL import Image

SRC = Path(__file__).parent / "печать_ИП_Афанасьева.png"
OUT_MS = Path(__file__).parent / "печать_МойСклад.png"
DESKTOP = Path("/Users/max/Desktop/печать_ИП_Афанасьева_МойСклад.png")


def find_stamp_circle(bgr):
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    m = cv2.inRange(hsv, (85, 25, 30), (145, 255, 255))
    m = cv2.medianBlur(m, 7)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    contours, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        h, w = bgr.shape[:2]
        return w // 2, h // 2, min(h, w) // 2 - 20
    cnt = max(contours, key=cv2.contourArea)
    (cx, cy), r = cv2.minEnclosingCircle(cnt)
    return int(cx), int(cy), int(r)


def process(src_path: Path):
    bgr = cv2.imread(str(src_path))
    if bgr is None:
        raise SystemExit(f"Не удалось прочитать: {src_path}")

    h, w = bgr.shape[:2]
    cx, cy, radius = find_stamp_circle(bgr)

    pad = int(radius * 0.06)
    r0 = radius + pad
    x0, y0 = max(0, cx - r0), max(0, cy - r0)
    x1, y1 = min(w, cx + r0), min(h, cy + r0)
    crop = bgr[y0:y1, x0:x1].copy()
    ch, cw = crop.shape[:2]
    ccx, ccy = cx - x0, cy - y0

    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    b, g, r_ch = cv2.split(crop)
    s = hsv[:, :, 1]
    v = hsv[:, :, 2]

    yy, xx = np.ogrid[:ch, :cw]
    in_disk = (xx - ccx) ** 2 + (yy - ccy) ** 2 <= radius**2

    # Синие чернила (порог мягкий — видно мелкий ИНН по кругу)
    ink = (
        in_disk
        & (b.astype(np.int16) - r_ch.astype(np.int16) > 6)
        & (b.astype(np.int16) - g.astype(np.int16) > 4)
        & (s > 20)
        & (v > 26)
    )

    result = np.full_like(crop, 255)
    result[ink] = crop[ink]

    # Серый ореол бумаги внутри круга → белый (убирает «мыло» вокруг букв)
    paper_halo = in_disk & ~ink & (s < 45) & (v > 100)
    result[paper_halo] = 255

    rgb = cv2.cvtColor(result, cv2.COLOR_BGR2RGB)
    pil = Image.fromarray(rgb)

    # Квадрат без увеличения — 1 пиксель фото = 1 пиксель файла
    side = max(pil.size)
    sq = Image.new("RGB", (side, side), (255, 255, 255))
    sq.paste(pil, ((side - pil.width) // 2, (side - pil.height) // 2))
    return sq


def main():
    src = SRC if SRC.exists() else Path(
        "/Users/max/.cursor/projects/Users-max-Desktop-CURSOR/assets/"
        "______________2026-05-26___15.25.20-f9b74ca9-b6c2-49e9-a21e-c5a33a8b5171.png"
    )
    ms = process(src)
    ms.save(OUT_MS, "PNG", optimize=True)
    ms.save(DESKTOP, "PNG", optimize=True)
    print(f"OK: {DESKTOP} ({ms.size[0]}x{ms.size[1]} px)")


if __name__ == "__main__":
    main()
