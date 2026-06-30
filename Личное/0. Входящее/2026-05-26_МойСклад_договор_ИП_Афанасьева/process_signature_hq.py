import cv2
import numpy as np
from PIL import Image
from pathlib import Path

ASSET = Path("/Users/max/.cursor/projects/Users-max-Desktop-CURSOR/assets/______________2026-05-26___15.36.57-b0a3a425-9f92-4295-8a62-ec6cebc9ca8b.png")
OUT_WHITE = Path("/Users/max/Desktop/подпись_ИП_Афанасьева_белый_фон.png")
OUT_TRANS = Path("/Users/max/Desktop/подпись_ИП_Афанасьева_прозрачный_фон.png")

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

def render_blue(density: np.ndarray) -> np.ndarray:
    d = np.clip(density * 1.12, 0.36, 1.0)
    h = np.full(d.shape, 112.0, dtype=np.float32)
    s = np.clip(90.0 + d * 165.0, 0, 255)
    v = np.clip(70.0 + d * 150.0, 0, 220)
    hsv = cv2.merge([h, s, v]).astype(np.uint8)
    return cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)

def process():
    bgr = cv2.imread(str(ASSET))
    if bgr is None:
        print("Cannot read image")
        return

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

    density_boosted = density.copy()
    boost = tick_zone & ink & (density > 0.05)
    density_boosted[boost] = np.clip(density_boosted[boost] * 1.25 + 0.06, 0, 1)

    blue = render_blue(density_boosted)

    # 1. White background version
    out_white = np.full((ch, cw, 3), 255, dtype=np.uint8)
    draw = ink & (density_boosted >= 0.10)
    draw |= tick_zone & ink & (density_boosted >= 0.07)
    out_white[draw] = blue[draw]
    
    # Make it square
    rgb = cv2.cvtColor(out_white, cv2.COLOR_BGR2RGB)
    pil_white = Image.fromarray(rgb)
    side = max(pil_white.size)
    sq_white = Image.new("RGB", (side, side), (255, 255, 255))
    sq_white.paste(pil_white, ((side - pil_white.width) // 2, (side - pil_white.height) // 2))
    sq_white.save(OUT_WHITE, "PNG", optimize=True)

    # 2. Transparent background version
    out_trans = np.zeros((ch, cw, 4), dtype=np.uint8)
    out_trans[draw, :3] = blue[draw]
    # Use density for alpha channel to make edges smooth
    alpha = np.zeros((ch, cw), dtype=np.float32)
    alpha[draw] = np.clip((density_boosted[draw] - 0.05) * 2.0, 0, 1) * 255
    out_trans[draw, 3] = alpha[draw].astype(np.uint8)
    
    # Make it square
    rgba = cv2.cvtColor(out_trans, cv2.COLOR_BGRA2RGBA)
    pil_trans = Image.fromarray(rgba)
    sq_trans = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    sq_trans.paste(pil_trans, ((side - pil_trans.width) // 2, (side - pil_trans.height) // 2))
    sq_trans.save(OUT_TRANS, "PNG", optimize=True)
    
    # Save directly to the repo folder as well
    REPO_DIR = Path("/Users/max/Desktop/CURSOR/Личное/0. Входящее/2026-05-26_МойСклад_договор_ИП_Афанасьева")
    sq_white.save(REPO_DIR / "печать_ИП_Афанасьева_белый_фон.png", "PNG", optimize=True)
    sq_trans.save(REPO_DIR / "подпись_МойСклад.png", "PNG", optimize=True)

    print(f"Saved HQ versions to Desktop and Repo")

process()
