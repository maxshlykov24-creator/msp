import cv2
import numpy as np
from PIL import Image
from pathlib import Path

ASSET = Path("/Users/max/.cursor/projects/Users-max-Desktop-CURSOR/assets/______________2026-05-26___15.36.57-b0a3a425-9f92-4295-8a62-ec6cebc9ca8b.png")
OUT_WHITE = Path("/Users/max/Desktop/подпись_белый_фон.png")
OUT_TRANS = Path("/Users/max/Desktop/подпись_прозрачный_фон.png")

def process(src_path):
    bgr = cv2.imread(str(src_path))
    if bgr is None:
        print("Cannot read image")
        return
    
    # Convert to grayscale
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    
    # The signature is blue, paper is grayish/white.
    # Let's use adaptive thresholding or simple thresholding.
    # Actually, the previous script had a good logic for finding ink:
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    b, g, r = cv2.split(bgr)
    
    # Ink mask: Blue is higher than Red and Green, and saturation is high enough
    is_ink = (b.astype(int) - r.astype(int) > 4) & (b.astype(int) - g.astype(int) > 2) & (hsv[:, :, 1] > 14) & (hsv[:, :, 2] > 20)
    
    # Clean up mask
    n, labels, stats, _ = cv2.connectedComponentsWithStats(is_ink.astype(np.uint8), 8)
    mask = np.zeros(is_ink.shape, dtype=bool)
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] >= 8:
            mask |= (labels == i)
            
    # Get bounding box
    ys, xs = np.where(mask)
    if len(xs) == 0:
        print("No ink found")
        return
        
    y0, y1 = ys.min(), ys.max()
    x0, x1 = xs.min(), xs.max()
    
    # Add padding
    pad = 20
    y0 = max(0, y0 - pad)
    y1 = min(bgr.shape[0], y1 + pad)
    x0 = max(0, x0 - pad)
    x1 = min(bgr.shape[1], x1 + pad)
    
    crop_bgr = bgr[y0:y1, x0:x1]
    crop_mask = mask[y0:y1, x0:x1]
    
    # Create white background image
    white_bg = np.full_like(crop_bgr, 255)
    white_bg[crop_mask] = crop_bgr[crop_mask]
    
    # Create transparent background image (RGBA)
    trans_bg = np.zeros((crop_bgr.shape[0], crop_bgr.shape[1], 4), dtype=np.uint8)
    trans_bg[crop_mask, :3] = crop_bgr[crop_mask]
    trans_bg[crop_mask, 3] = 255 # Full opacity for ink
    
    # Save
    cv2.imwrite(str(OUT_WHITE), white_bg)
    cv2.imwrite(str(OUT_TRANS), trans_bg)
    print(f"Saved to {OUT_WHITE} and {OUT_TRANS}")

process(ASSET)
