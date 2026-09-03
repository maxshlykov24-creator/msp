"""
ШАГИ 4–7 — Заполнить доп. поля: Жёсткость, Длина, Категория, Линейка

Все четыре поля обновляются за ОДИН запрос на товар (один PUT).
Запускать ПОСЛЕ Шага 2 (основной article уже является кодом).

Логика определения значений:
  Шаг 4 — Жёсткость:
    D18H00/00-NV      → Ж0
    D18H01/01-NV      → Ж1
    D18H02/02-NV      → Ж2
    D18H02P/02P-NV    → Ж2+
    D18H03/03-NV      → Ж3
    FBCLxxx-1         → Ж1
    FBCLxxx (базовые, без суффикса -1/-3) → Ж2
    FBCLxxx-3         → Ж3
    FBMxxx            → Ж2

  Шаг 5 — Длина, см:
    D18Hxx            → 200 (стандарт)
    FBMxx / FBCLxx    → из артикула: 80, 90, 100, 120, 140, 160, 180, 200

  Шаг 6 — Категория для аналитики:
    D18H*             → Мат Детензор 18%
    FCST1/2, FCSBB/BG → Шейная опора
    FBCLxxx           → Матрас Classic
    FBMxxx            → Матрас Max
    HDMALL, HDMFALL, HYGIENE.BACK, HFCST1, HFCST2 → Аксессуары к мату
    HFBCLxxx          → Аксессуары к матрасам
    HFBMxxx           → Аксессуары к матрасам
    NS405*, DSKBL..DRKRE, NIGHT.*, SILK-*, 3DMASK  → Подушки
    SIM160-*, simproveplate, L400PV                 → Тренажёры
    DH.GLASSES*, BODOFLY, stelki                    → Сопутствующие

  Шаг 7 — Линейка:
    D18H*, HDMALL, HDMFALL, HYGIENE.BACK, HFCST1, HFCST2, FCST1/2, FCSBB/BG → Detensor 18%
    FBCLxxx, HFBCLxxx  → Fibrotop Classic
    FBMxxx, HFBMxxx    → Fibrotop Max
    Остальные          → Другое

Подводные камни:
  1. customentity поле нельзя установить пустой строкой — нужно передать объект value с meta.
  2. Товары могут уже иметь значения — script idempotent, перезаписываем.
  3. Аренда (rent28_*) в текущем каталоге отсутствует — пропускаем без ошибки.
  4. Артикул HFBCL80_6817 — это дубль/вариант HF BCL80, артикул нестандартный.
     Обрабатываем через startswith('HFBCL').

Запуск:
  python3 step4567_fill_attrs.py [--dry-run]
"""

import sys
import re
from ms_client import get_all_products, update_product, make_attr
from config import ZHESTKOST, KATEGORIYA, LINEYKA

DRY_RUN = "--dry-run" in sys.argv

# ── Вспомогательные функции ────────────────────────────────────────────────────

def get_length_from_article(article: str) -> int | None:
    """Извлечь длину из артикула FBMxx / FBCLxx.
    Примеры: FBM80→80, FBCL100-3→100, FBCL140→140
    """
    m = re.match(r"^(?:FBM|FBCL)(\d+)", article)
    if m:
        return int(m.group(1))
    return None


def classify(article: str) -> dict | None:
    """
    По артикулу вернуть словарь:
      { 'zhestkost': str|None, 'dlina': int|None,
        'kategoriya': str|None, 'lineyka': str|None }
    Или None если товар не входит в целевой набор.
    """
    art = article.strip()
    result = {
        "zhestkost": None,
        "dlina": None,
        "kategoriya": None,
        "lineyka": None,
    }

    # ── Маты Детензор D18H ───────────────────────────────────────────────────
    if re.match(r"^D18H", art):
        # Жёсткость
        if art.startswith("D18H00"):
            result["zhestkost"] = "Ж0"
        elif art.startswith("D18H01"):
            result["zhestkost"] = "Ж1"
        elif art.startswith("D18H02P"):
            result["zhestkost"] = "Ж2+"
        elif art.startswith("D18H02"):
            result["zhestkost"] = "Ж2"
        elif art.startswith("D18H03"):
            result["zhestkost"] = "Ж3"
        # Длина — стандарт 200
        result["dlina"] = 200
        result["kategoriya"] = "Мат Детензор 18%"
        result["lineyka"] = "Detensor 18%"
        return result

    # ── Матрасы Fibrotop MAX FBM ─────────────────────────────────────────────
    if re.match(r"^FBM\d", art) or art == "FMAX.ORDER":
        result["zhestkost"] = "Ж2"
        length = get_length_from_article(art)
        result["dlina"] = length  # None для FMAX.ORDER — не ставим
        result["kategoriya"] = "Матрас Max"
        result["lineyka"] = "Fibrotop Max"
        return result

    # ── Матрасы Fibrotop Classic FBCL ───────────────────────────────────────
    if re.match(r"^FBCL\d", art):
        if art.endswith("-1"):
            result["zhestkost"] = "Ж1"
        elif art.endswith("-3"):
            result["zhestkost"] = "Ж3"
        else:
            result["zhestkost"] = "Ж2"
        length = get_length_from_article(art)
        result["dlina"] = length
        result["kategoriya"] = "Матрас Classic"
        result["lineyka"] = "Fibrotop Classic"
        return result

    # ── Шейные опоры FCST / FCS ──────────────────────────────────────────────
    if art in ("FCST1", "FCST2", "FCSBG", "FCSBB"):
        result["kategoriya"] = "Шейная опора"
        result["lineyka"] = "Detensor 18%"
        return result

    # ── Аксессуары к мату ────────────────────────────────────────────────────
    if art in ("HDMALL", "HDMFALL", "HYGIENE.BACK", "HFCST1", "HFCST2"):
        result["kategoriya"] = "Аксессуары к мату"
        result["lineyka"] = "Detensor 18%"
        return result

    # ── Аксессуары к матрасам Classic HFBCL ─────────────────────────────────
    if re.match(r"^HFBCL", art):
        result["kategoriya"] = "Аксессуары к матрасам"
        result["lineyka"] = "Fibrotop Classic"
        return result

    # ── Аксессуары к матрасам Max HFBM ──────────────────────────────────────
    if re.match(r"^HFBM", art):
        result["kategoriya"] = "Аксессуары к матрасам"
        result["lineyka"] = "Fibrotop Max"
        return result

    # ── Подушки для сна NS ───────────────────────────────────────────────────
    if re.match(r"^NS\d", art):
        result["kategoriya"] = "Подушки"
        result["lineyka"] = "Другое"
        return result

    # ── Подушки для сидения DSK ──────────────────────────────────────────────
    if re.match(r"^DSK", art):
        result["kategoriya"] = "Подушки"
        result["lineyka"] = "Другое"
        return result

    # ── Подушки для спины DRK ────────────────────────────────────────────────
    if re.match(r"^DRK", art):
        result["kategoriya"] = "Подушки"
        result["lineyka"] = "Другое"
        return result

    # ── Чехлы и наволочки для подушек ────────────────────────────────────────
    if re.match(r"^NIGHT\.", art):
        result["kategoriya"] = "Подушки"
        result["lineyka"] = "Другое"
        return result

    # ── Шёлковые и 3D маски → Сопутствующие ────────────────────────────────────
    if art.startswith("SILK-") or art == "3DMASK":
        result["kategoriya"] = "Сопутствующие"
        result["lineyka"] = "Другое"
        return result

    # ── Тренажёры Simprove и Lumbus ──────────────────────────────────────────
    if re.match(r"^SIM160", art) or art in ("simproveplate", "L400PV"):
        result["kategoriya"] = "Тренажёры"
        result["lineyka"] = "Другое"
        return result

    # ── Сопутствующие ────────────────────────────────────────────────────────
    if art in ("DH.GLASSES", "DH.GLASSES-L", "BODOFLY", "stelki"):
        result["kategoriya"] = "Сопутствующие"
        result["lineyka"] = "Другое"
        return result

    # Не входит в целевой набор
    return None


def build_payload(classification: dict) -> list:
    """Построить список attributes для PUT-запроса."""
    attrs = []

    zn = classification.get("zhestkost")
    if zn:
        attrs.append(make_attr("Жесткость", {
            "dict_name": "Жесткость",
            "value_id": ZHESTKOST[zn],
        }))

    dl = classification.get("dlina")
    if dl is not None:
        attrs.append(make_attr("Длина, см", dl))

    kt = classification.get("kategoriya")
    if kt:
        attrs.append(make_attr("Категория", {
            "dict_name": "Категория",
            "value_id": KATEGORIYA[kt],
        }))

    ln = classification.get("lineyka")
    if ln:
        attrs.append(make_attr("Линейка", {
            "dict_name": "Линейка",
            "value_id": LINEYKA[ln],
        }))

    return attrs


def main():
    print("=" * 60)
    print("ШАГИ 4–7: Жёсткость / Длина / Категория / Линейка")
    print(f"Режим: {'DRY-RUN (без записи)' if DRY_RUN else 'БОЕВОЙ'}")
    print("=" * 60)

    products = get_all_products()
    print(f"Загружено товаров: {len(products)}")

    ok = skip = miss = err = 0
    for p in products:
        if p.get("archived"):
            continue
        article = p.get("article", "") or ""
        if not article:
            continue

        cls = classify(article)
        if cls is None:
            continue

        payload_attrs = build_payload(cls)
        if not payload_attrs:
            skip += 1
            continue

        zn = cls.get("zhestkost") or "-"
        dl = cls.get("dlina") or "-"
        kt = cls.get("kategoriya") or "-"
        ln = cls.get("lineyka") or "-"
        label = f"{article:20} Ж={zn:4} Д={str(dl):5} К={kt[:20]} Л={ln[:17]}"

        if DRY_RUN:
            print(f"  [DRY] {label}")
            ok += 1
            continue

        try:
            update_product(p["id"], {"attributes": payload_attrs})
            print(f"  OK  {label}")
            ok += 1
        except Exception as e:
            print(f"  ERR {label} → {e}")
            err += 1

    print()
    print(f"Итог: OK={ok}, SKIP={skip}, MISS={miss}, ERR={err}")
    if err:
        sys.exit(1)


if __name__ == "__main__":
    main()
