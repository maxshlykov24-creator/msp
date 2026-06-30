"""
load_products.py — Загрузка номенклатуры RUSbrend → МойСклад
Источник: публичный Google Sheets CSV (376 товаров)

Использование:
  python3 load_products.py --limit 5       # первые 5 (тест)
  python3 load_products.py                 # все строки
  python3 load_products.py --dry-run       # без записи, только лог
  python3 load_products.py --skip 0        # пропустить N строк (для дозагрузки)

Колонки (pub CSV):
  [0]  Магазин
  [2]  Артикул
  [3]  Наименование
  [4]  ШК (Этикетка) — имя PDF
  [5]  Баркод — EAN-13 (числовой) или OZN... (code128) или пусто
  [6]  Часто используемый короб (S/M/L)
  [7]  Количество в выбранном коробе
  [8]  Помещается в короб S mini
  [9]  Помещается в короб S (30х20х30)
  [10] Помещается в короб М (40х30х40)
  [11] Помещается в короб L (60х40х40)
  [13] Количество в пакете
  [16] Размер пакета
  [17] Вес, грамм
  [18] Комментарий
  [19] Короб, шт
  [20] Вес короба, кг
"""

import csv, io, json, time, argparse
import urllib.request
import requests

# ── Конфигурация ──────────────────────────────────────────────────────────────
TOKEN    = "951f3b09451c02691686ac8077cc59d474685a04"
BASE     = "https://api.moysklad.ru/api/remap/1.2"
CSV_URL  = (
    "https://docs.google.com/spreadsheets/d/e/"
    "2PACX-1vTCbqOWDOmg_6x9AX394mM2ODDihf-JmZB-dGUjftHvLfIKSwmmsrT44Az6FSqoGlhAysbooOaYl-NV"
    "/pub?gid=0&single=true&output=csv"
)
# Локальный fallback если нет интернета
CSV_LOCAL = "/Users/max/Downloads/Матрица RUSbrend - База.csv"
DELAY     = 0.35  # сек между запросами

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json",
}

# ── IDs атрибутов продукта ────────────────────────────────────────────────────
ATTR = {
    "Организация":                   "e881075b-4f9d-11f1-0a80-156000141799",
    "Помещается в короб S":          "e8810aa5-4f9d-11f1-0a80-15600014179a",
    "Помещается в короб M":          "e8810bb9-4f9d-11f1-0a80-15600014179b",
    "Помещается в короб L":          "e8810c9f-4f9d-11f1-0a80-15600014179c",
    "Часто используемый короб":      "5b730858-4fa4-11f1-0a80-1d2e0015579d",
    "Количество в выбранном коробе": "e8810f2c-4f9d-11f1-0a80-15600014179f",
    "Количество в пакете":           "e881100a-4f9d-11f1-0a80-1560001417a0",
    "Размер пакета":                 "e8811110-4f9d-11f1-0a80-1560001417a1",
    "Вес г факт":                    "e88111fb-4f9d-11f1-0a80-1560001417a2",
    "Короб, шт":                     "e88112d5-4f9d-11f1-0a80-1560001417a3",
    "Вес короба, кг":                "e88113cc-4f9d-11f1-0a80-1560001417a4",
}

CE_ORG = "06cc7a62-4f9d-11f1-0a80-075500139fb5"   # справочник Организация
CE_BOX = "87b0e62a-4fa3-11f1-0a80-13470014140f"   # справочник Короб

ORG_MAP = {
    "RUSbrend": "2cff3b7d-52be-11f1-0a80-165a0038aa04",
    "РУСбренд": "2cff3b7d-52be-11f1-0a80-165a0038aa04",
    "Новый":    "2cff3b7d-52be-11f1-0a80-165a0038aa04",
}

BOX_MAP = {
    "S": "106993b0-4fa4-11f1-0a80-07550014b96a",
    "M": "12b42bc3-4fa4-11f1-0a80-134700142471",
    "L": "1475f29d-4fa4-11f1-0a80-056700138e7e",
}


# ── Вспомогательные функции ───────────────────────────────────────────────────

def clean(v):
    return str(v).strip() if v else ""

def to_int(v):
    v = clean(v).replace("\xa0", "").replace(" ", "")
    try:
        return int(float(v))
    except Exception:
        return None

def to_float(v):
    v = clean(v).replace("\xa0", "").replace(" ", "").replace(",", ".")
    try:
        return float(v)
    except Exception:
        return None

def attr_long(name, value):
    v = to_int(value)
    if v is None:
        return None
    return {
        "meta": {
            "href": f"{BASE}/entity/product/metadata/attributes/{ATTR[name]}",
            "type": "attributemetadata",
            "mediaType": "application/json",
        },
        "value": v,
    }

def attr_str(name, value):
    v = clean(value)
    if not v:
        return None
    return {
        "meta": {
            "href": f"{BASE}/entity/product/metadata/attributes/{ATTR[name]}",
            "type": "attributemetadata",
            "mediaType": "application/json",
        },
        "value": v,
    }

def attr_double(name, value):
    v = to_float(value)
    if v is None:
        return None
    return {
        "meta": {
            "href": f"{BASE}/entity/product/metadata/attributes/{ATTR[name]}",
            "type": "attributemetadata",
            "mediaType": "application/json",
        },
        "value": v,
    }

def attr_customentity(name, ce_id, item_id):
    if not item_id:
        return None
    return {
        "meta": {
            "href": f"{BASE}/entity/product/metadata/attributes/{ATTR[name]}",
            "type": "attributemetadata",
            "mediaType": "application/json",
        },
        "value": {
            "meta": {
                "href": f"{BASE}/entity/customentity/{ce_id}/{item_id}",
                "type": "customentity",
                "mediaType": "application/json",
            }
        },
    }


def parse_barcode(raw):
    """Вернуть (тип, значение) или (None, None)."""
    v = clean(raw)
    if not v:
        return None, None
    if v.upper().startswith("OZN"):
        return "code128", v
    # Числовой: EAN-13 если 13 цифр, иначе code128
    digits = v.replace("E", "").replace("e", "")
    # Обработка научной нотации (2.051395644582E12)
    try:
        numeric = int(float(v))
        s = str(numeric)
        if len(s) == 13:
            return "ean13", s
        elif len(s) in (8, 12, 14):
            return "ean13", s  # EAN-8, UPC, EAN-14
        else:
            return "code128", s
    except Exception:
        return "code128", v


def build_product(row):
    """Собрать payload для POST /entity/product из строки pub CSV."""
    if len(row) < 6:
        return None

    shop    = clean(row[0])
    article = clean(row[2])
    name    = clean(row[3])
    barcode_raw = clean(row[5]) if len(row) > 5 else ""
    box_type    = clean(row[6]).upper() if len(row) > 6 else ""
    box_qty     = row[7]  if len(row) > 7  else ""
    box_s       = row[9]  if len(row) > 9  else ""   # S (30х20х30)
    box_m       = row[10] if len(row) > 10 else ""
    box_l       = row[11] if len(row) > 11 else ""
    kit_qty     = row[13] if len(row) > 13 else ""
    pkg_sz      = row[16] if len(row) > 16 else ""
    wt_g_raw    = row[17] if len(row) > 17 else ""
    comment     = row[18] if len(row) > 18 else ""
    box_cnt     = row[19] if len(row) > 19 else ""
    box_wt      = row[20] if len(row) > 20 else ""

    if not article or not name:
        return None

    # Вес
    wt_g = to_float(wt_g_raw)
    weight_kg = round(wt_g / 1000, 4) if wt_g and wt_g > 0 else None

    # Штрихкод
    bc_type, bc_value = parse_barcode(barcode_raw)

    # Атрибуты
    org_id = ORG_MAP.get(shop)
    box_id = BOX_MAP.get(box_type) if box_type in BOX_MAP else None

    attributes = []
    for a in [
        attr_customentity("Организация",              CE_ORG, org_id),
        attr_long("Помещается в короб S",             box_s),
        attr_long("Помещается в короб M",             box_m),
        attr_long("Помещается в короб L",             box_l),
        attr_customentity("Часто используемый короб", CE_BOX, box_id),
        attr_long("Количество в выбранном коробе",    box_qty),
        attr_long("Количество в пакете",              kit_qty),
        attr_str("Размер пакета",                     pkg_sz),
        attr_str("Вес г факт",                        wt_g_raw),
        attr_long("Короб, шт",                        box_cnt),
        attr_double("Вес короба, кг",                 box_wt),
    ]:
        if a is not None:
            attributes.append(a)

    payload = {
        "name":    name,
        "article": article,
        "code":    article,
    }
    desc = clean(comment)
    if desc:
        payload["description"] = desc
    if weight_kg:
        payload["weight"] = weight_kg
    if bc_type and bc_value:
        payload["barcodes"] = [{bc_type: bc_value}]
    if attributes:
        payload["attributes"] = attributes

    return payload


def fetch_existing_articles(retries=5):
    """Собрать множество артикулов, уже загруженных в МойСклад."""
    existing = set()
    offset = 0
    while True:
        url = f"{BASE}/entity/product?limit=1000&offset={offset}"
        resp = None
        for attempt in range(1, retries + 1):
            try:
                resp = requests.get(url, headers=HEADERS, timeout=40)
                break
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
                print(f"  сбор артикулов (попытка {attempt}/{retries})...")
                if attempt < retries:
                    time.sleep(3 * attempt)
        if resp is None or resp.status_code != 200:
            print("  Не удалось получить существующие артикулы — продолжаем без пропуска")
            return existing
        rows = resp.json().get("rows", [])
        for p in rows:
            a = (p.get("article") or "").strip()
            if a:
                existing.add(a)
        if len(rows) < 1000:
            break
        offset += 1000
    return existing


def load_csv():
    """Скачать CSV с Google Sheets; fallback — локальный файл."""
    try:
        with urllib.request.urlopen(CSV_URL, timeout=15) as resp:
            text = resp.read().decode("utf-8-sig")
        print("CSV загружен из Google Sheets")
    except Exception as e:
        print(f"Google Sheets недоступен ({e}), читаем локальный файл")
        with open(CSV_LOCAL, encoding="utf-8-sig") as f:
            text = f.read()
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    return rows[1:]  # без заголовка


def create_product(payload, dry_run=False, retries=3):
    if dry_run:
        bc = payload.get("barcodes", [])
        print(f"       [DRY] {payload['article']} | {payload['name'][:40]} | bc={bc}")
        return {"id": "dry-run-" + payload["article"]}
    for attempt in range(1, retries + 1):
        try:
            r = requests.post(f"{BASE}/entity/product", headers=HEADERS, json=payload, timeout=30)
            if r.status_code in (200, 201):
                return r.json()
            print(f"       ОШИБКА {r.status_code}: {r.text[:300]}")
            return None
        except requests.exceptions.Timeout:
            print(f"       Таймаут (попытка {attempt}/{retries})...")
            if attempt < retries:
                time.sleep(3 * attempt)
        except Exception as e:
            print(f"       Ошибка сети: {e}")
            if attempt < retries:
                time.sleep(3 * attempt)
    return None


# ── Основной цикл ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Макс кол-во строк")
    parser.add_argument("--skip",  type=int, default=0,    help="Пропустить N строк (offset)")
    parser.add_argument("--articles", type=str, default=None,
                        help="Грузить только эти артикулы через запятую, напр. 120,121,122")
    parser.add_argument("--resume", action="store_true",
                        help="Пропустить артикулы, уже существующие в МойСклад (безопасно при обрывах)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    existing = set()
    if args.resume and not args.dry_run:
        print("Сбор уже загруженных артикулов...")
        existing = fetch_existing_articles()
        print(f"Уже в МойСклад: {len(existing)} товаров — будут пропущены\n")

    rows = load_csv()

    if args.articles:
        wanted = {a.strip() for a in args.articles.split(",") if a.strip()}
        rows = [r for r in rows if len(r) > 2 and clean(r[2]) in wanted]
    else:
        rows = rows[args.skip:]
        if args.limit:
            rows = rows[:args.limit]

    total   = len(rows)
    created = 0
    skipped = 0
    errors  = 0

    print(f"\nСтрок к обработке: {total} {'(DRY-RUN)' if args.dry_run else ''}\n")

    for i, row in enumerate(rows, 1):
        article = clean(row[2]) if len(row) > 2 else ""
        name    = clean(row[3]) if len(row) > 3 else ""

        if not article or not name:
            print(f"[{i:3}/{total}] ПРОПУСК — нет артикула/наименования")
            skipped += 1
            continue

        if article in existing:
            print(f"[{i:3}/{total}] {article:6} уже есть — пропуск")
            skipped += 1
            continue

        payload = build_product(row)
        if not payload:
            print(f"[{i:3}/{total}] ПРОПУСК — build_product вернул None")
            skipped += 1
            continue

        bc_info = ""
        if payload.get("barcodes"):
            bc_info = str(payload["barcodes"][0])
        print(f"[{i:3}/{total}] {article:6} {name[:45]:45} {bc_info}")

        result = create_product(payload, dry_run=args.dry_run)
        if result:
            print(f"         → OK, id={result.get('id','?')}")
            created += 1
        else:
            errors += 1

        time.sleep(DELAY)

    print(f"\n{'='*60}")
    print(f"Итого: {created} создано | {skipped} пропущено | {errors} ошибок")
    print(f"Всего обработано: {created + skipped + errors}")


if __name__ == "__main__":
    main()
