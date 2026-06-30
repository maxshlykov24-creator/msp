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

# Сглаживаем шероховатости (закрываем микро-разрывы), чтобы хвосты подписи стали цельными
kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel, iterations=1)

# Удаляем мелкий мусор (изолированные точки)
contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
for contour in contours:
    # Проверяем габариты каждого пятна
    x_c, y_c, w_c, h_c = cv2.boundingRect(contour)
    # Если пятно полностью помещается в квадрат 15x15 пикселей - это точка/мусор
    if w_c < 15 and h_c < 15:
        cv2.drawContours(thresh, [contour], -1, 0, -1)

# Расширяем маску для естественной толщины
thresh = cv2.dilate(thresh, kernel, iterations=1)

# Более сильное размытие альфа-канала для очень плавных краёв (сглаживание ступенек)
alpha = cv2.GaussianBlur(thresh, (5, 5), 1.5)

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
coords = cv2.findNonZero(alpha)
if len(coords) > 0:
    # cv2.findNonZero возвращает координаты в формате [x, y]
    x_min = np.min(coords[:, 0, 0])
    x_max = np.max(coords[:, 0, 0])
    y_min = np.min(coords[:, 0, 1])
    y_max = np.max(coords[:, 0, 1])
    
    # Сначала строго вырезаем по фактическим границам чернил
    rgba = rgba[y_min:y_max+1, x_min:x_max+1]
    
    # А затем добавляем искусственный отступ (padding) со всех сторон.
    # Это гарантирует, что даже если на оригинальном фото подпись упиралась
    # в самый край кадра, в финальном PNG всё равно будет пустое пространство
    # и хвосты не будут визуально "обрублены".
    pad = 40
    rgba = cv2.copyMakeBorder(rgba, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=[0, 0, 0, 0])

out_path = os.path.join(OUTPUT_DIR, "signature_for_docs.png")
Image.fromarray(rgba).save(out_path, "PNG")

print(f"✅ Готово! Прозрачная подпись сохранена: {out_path}")
