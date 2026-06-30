"""
Обработка подписи v2: сохраняем толщину + цвет реальной ручки
Исправлено: маска расширена, цвет приведён к натуральному синему шарику
"""

import cv2
import numpy as np
from PIL import Image, ImageFilter
import os

INPUT = "/Users/max/.cursor/projects/Users-max-Desktop-CURSOR/assets/______________2026-06-02___11.40.33-0f99aff2-e53e-4646-a57d-0e4dcf06d830.png"
OUTPUT_DIR = "/Users/max/Desktop/CURSOR/Личное/0. Входящее/2026-05-26_МойСклад_договор_ИП_Афанасьева/"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ── 1. Загрузка ──────────────────────────────────────────────────────────────
img_bgr = cv2.imread(INPUT)
img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
h, w = img_rgb.shape[:2]

# Диагностика — смотрим реальный цвет чернил в центре штриха
gray_check = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
ys, xs = np.where(gray_check < 120)
if len(xs):
    sample_colors = img_rgb[ys[:20], xs[:20]]
    print(f"Образцы цвета чернил (RGB): {sample_colors[:5]}")

# ── 2. Маска чернил — агрессивнее, чтобы не терять толщину ──────────────────
img_hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)

# Синий диапазон — расширяем верхнюю границу яркости (светлые края штриха)
blue_lo = np.array([85,  20,  20])
blue_hi = np.array([145, 255, 230])
mask_blue = cv2.inRange(img_hsv, blue_lo, blue_hi)

# Тёмные пиксели — порог выше (220 вместо 160), захватываем полутона
gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
_, mask_dark = cv2.threshold(gray, 220, 255, cv2.THRESH_BINARY_INV)

mask_ink = cv2.bitwise_or(mask_blue, mask_dark)

# Дилатация больше — восстанавливаем толщину, потерянную при вырезании
kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
mask_ink = cv2.dilate(mask_ink, kernel, iterations=2)

# Плавные края (сглаживание без потери ширины)
mask_ink_f = cv2.GaussianBlur(mask_ink, (5, 5), 1.2)

alpha = mask_ink_f.astype(np.float32) / 255.0
alpha3 = np.stack([alpha, alpha, alpha], axis=2)

# ── 3. Цвет чернил — реальная шариковая ручка ────────────────────────────────
# Настоящий синий шарик: R≈30-60, G≈60-100, B≈140-180 (тёмный насыщенный синий)
# Не трогаем оригинальный цвет где он уже нормальный — только нормализуем

ink_f = img_rgb.astype(np.float32)
r_c, g_c, b_c = ink_f[:,:,0], ink_f[:,:,1], ink_f[:,:,2]

# Там где чернила — тянем цвет к "шариковому" синему
# Уменьшаем красный, слегка уменьшаем зелёный, синий оставляем
strength = alpha * 0.55  # насколько сильно корректируем (0=ничего, 1=полностью)

PEN_R, PEN_G, PEN_B = 28.0, 65.0, 155.0  # целевой цвет шариковой ручки

r_new = r_c * (1 - strength) + PEN_R * strength
g_new = g_c * (1 - strength) + PEN_G * strength
b_new = b_c * (1 - strength) + PEN_B * strength

ink_corrected = np.stack([r_new, g_new, b_new], axis=2)

# ── 4. Композиция поверх белого фона ─────────────────────────────────────────
white = np.ones((h, w, 3), dtype=np.float32) * 255.0
result_f = alpha3 * ink_corrected + (1 - alpha3) * white
result = np.clip(result_f, 0, 255).astype(np.uint8)

# ── 5. Минимальный шум бумаги (только фон, чернила не трогаем) ───────────────
rng = np.random.default_rng(42)
noise = rng.normal(0, 1.5, result.shape).astype(np.float32)
bg_only = (1 - alpha3)
result_f2 = result.astype(np.float32)
# Шум добавляем только там, где нет чернил; фон клипим в [248,255]
bg_noisy = np.clip(result_f2 + noise * bg_only, 248, 255)
# Чернила — без изменений
result_noisy = np.where(alpha3 > 0.05, result_f2, bg_noisy).astype(np.uint8)

# ── 6. Очень лёгкий шарпинг (имитация сканера) ───────────────────────────────
final = Image.fromarray(result_noisy)
final = final.filter(ImageFilter.UnsharpMask(radius=0.8, percent=40, threshold=3))

# ── 7. Сохранение ─────────────────────────────────────────────────────────────
out_png  = os.path.join(OUTPUT_DIR, "signature_clean.png")
out_rgba = os.path.join(OUTPUT_DIR, "signature_transparent.png")

final.save(out_png, "PNG", optimize=True)

rgba_arr = np.array(final)
rgba_out = np.dstack([rgba_arr, mask_ink_f])
Image.fromarray(rgba_out).save(out_rgba, "PNG")

print(f"✅ Белый фон:       {out_png}")
print(f"✅ Прозрачный фон:  {out_rgba}")
print(f"   Размер: {w}×{h}px")
