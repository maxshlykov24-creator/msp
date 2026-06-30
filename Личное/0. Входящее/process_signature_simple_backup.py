"""
Простое и надежное извлечение подписи в прозрачный PNG для документов.
"""

import cv2
import numpy as np
from PIL import Image
import os

INPUT = "/Users/max/.cursor/projects/Users-max-Desktop-CURSOR/assets/______________2026-06-02___11.40.33-0f99aff2-e53e-4646-a57d-0e4dcf06d830.png"
OUTPUT_DIR = "/Users/max/Desktop/CURSOR/Личное/0. Входящее/2026-05-26_МойСклад_договор_ИП_Афанасьева/"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# 1. Загружаем изображение
img = cv2.imread(INPUT)
img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

# 2. Переводим в градации серого для определения яркости
gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

# 3. Вычисляем альфа-канал (прозрачность)
# Применяем адаптивный порог, чтобы учесть неравномерное освещение бумаги
# Размываем для устранения шума бумаги
blurred = cv2.GaussianBlur(gray, (5, 5), 0)

# Адаптивный порог: всё что темнее локального фона на 15 единиц становится 255 (чернила)
thresh = cv2.adaptiveThreshold(blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, 
                               cv2.THRESH_BINARY_INV, 41, 15)

# Немного расширяем и сглаживаем маску для естественной толщины
kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
thresh = cv2.dilate(thresh, kernel, iterations=1)
alpha = cv2.GaussianBlur(thresh, (3, 3), 0)

# 4. Цвет чернил
# Чтобы подпись на любом фоне выглядела как ручка, 
# зададим всем пикселям насыщенный синий цвет ручки.
# Если использовать оригинальный цвет, на границах может вылезти белый ореол от бумаги.
h, w = img.shape[:2]
color_img = np.zeros((h, w, 3), dtype=np.uint8)
color_img[:] = [28, 65, 155] # RGB цвет шариковой ручки

# 5. Собираем итоговое изображение (Цвет ручки + вычисленная прозрачность)
rgba = np.dstack([color_img, alpha])

# Обрезаем пустые края (crop)
coords = cv2.findNonZero(alpha) # y, x
if len(coords) > 0:
    y_min = np.min(coords[:, 0, 0])
    y_max = np.max(coords[:, 0, 0])
    x_min = np.min(coords[:, 0, 1])
    x_max = np.max(coords[:, 0, 1])
    # Добавляем небольшие отступы
    pad = 10
    y_min = max(0, y_min - pad)
    y_max = min(h, y_max + pad)
    x_min = max(0, x_min - pad)
    x_max = min(w, x_max + pad)
    
    rgba = rgba[y_min:y_max, x_min:x_max]

out_path = os.path.join(OUTPUT_DIR, "signature_for_docs.png")
Image.fromarray(rgba).save(out_path, "PNG")

print(f"✅ Готово! Прозрачная подпись сохранена: {out_path}")
