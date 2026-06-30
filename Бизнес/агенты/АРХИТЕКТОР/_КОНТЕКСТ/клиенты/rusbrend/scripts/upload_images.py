"""
upload_images.py — Извлечь изображения из XLSX и загрузить в МойСклад

Алгоритм:
  1. Распаковать XLSX, прочитать drawing1.xml → маппинг row → image_file
  2. Прочитать sheet1.xml → маппинг row → article
  3. Объединить: article → image_path
  4. Для каждого артикула найти продукт в МойСклад и загрузить изображение

Использование:
  python3 upload_images.py --limit 5        # первые 5 (тест)
  python3 upload_images.py                  # все
  python3 upload_images.py --dry-run        # без загрузки

Зависимости:
  pip install requests openpyxl
"""

import os, zipfile, shutil, tempfile, time, argparse, json, base64
import xml.etree.ElementTree as ET
import requests

TOKEN    = "951f3b09451c02691686ac8077cc59d474685a04"
BASE     = "https://api.moysklad.ru/api/remap/1.2"
XLSX_PATH = "/Users/max/Downloads/Матрица БЕРЕЗА ГРУПП.xlsx"
DELAY    = 0.4

HEADERS_JSON = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json",
}

NS = {
    "xdr": "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing",
    "a":   "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r":   "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "ns":  "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
}


def unpack_xlsx(xlsx_path):
    """Распаковать XLSX во временную директорию, вернуть путь."""
    tmp = tempfile.mkdtemp(prefix="xlsx_images_")
    with zipfile.ZipFile(xlsx_path, "r") as z:
        z.extractall(tmp)
    return tmp


EMU_PER_PT = 12700
DEFAULT_ROW_HT = 15.75


def build_row_tops(xlsx_dir):
    """Кумулятивная верхняя граница каждой строки (EMU). Учитывает высоты строк."""
    sheet_path = os.path.join(xlsx_dir, "xl", "worksheets", "sheet1.xml")
    root = ET.parse(sheet_path).getroot()

    heights = {}
    max_row = 1
    for row in root.findall(".//ns:row", NS):
        rn = int(row.attrib["r"])
        ht = row.attrib.get("ht")
        heights[rn] = float(ht) if ht else DEFAULT_ROW_HT
        max_row = max(max_row, rn)

    tops = {}
    cum = 0.0
    for rn in range(1, max_row + 2):
        tops[rn] = cum * EMU_PER_PT
        cum += heights.get(rn, DEFAULT_ROW_HT)
    return tops, max_row


def row_at(tops, max_row, emu):
    for rn in range(1, max_row + 1):
        if tops[rn] <= emu < tops[rn + 1]:
            return rn
    return max_row


def build_row_to_image(xlsx_dir):
    """
    Построить dict: excel_row (1-based) → абсолютный путь к изображению.

    Картинки в XLSX — плавающие (oneCellAnchor) и часто смещены вниз (rowOff),
    из-за чего «строка якоря» не совпадает с реальной строкой товара. Поэтому
    определяем строку по ЦЕНТРУ картинки = top(anchor_row) + rowOff + cy/2.
    """
    drawing_path = os.path.join(xlsx_dir, "xl", "drawings", "drawing1.xml")
    rels_path    = os.path.join(xlsx_dir, "xl", "drawings", "_rels", "drawing1.xml.rels")

    if not os.path.exists(drawing_path):
        print("drawing1.xml не найден")
        return {}

    rels_tree = ET.parse(rels_path)
    rid_to_file = {}
    for rel in rels_tree.getroot():
        rid = rel.attrib["Id"]
        tgt = rel.attrib["Target"]
        rid_to_file[rid] = tgt.split("/")[-1]

    tops, max_row = build_row_tops(xlsx_dir)

    draw_root = ET.parse(drawing_path).getroot()

    row_to_img = {}
    for anchor in draw_root.findall("xdr:oneCellAnchor", NS):
        row_el  = anchor.find("xdr:from/xdr:row", NS)
        roff_el = anchor.find("xdr:from/xdr:rowOff", NS)
        ext_el  = anchor.find("xdr:ext", NS)
        blip    = anchor.find(".//a:blip", NS)
        if row_el is None or blip is None:
            continue

        anchor_row = int(row_el.text) + 1  # 0-based → 1-based
        rowoff = int(roff_el.text) if roff_el is not None else 0
        cy     = int(ext_el.attrib.get("cy", "0")) if ext_el is not None else 0

        center = tops.get(anchor_row, 0) + rowoff + cy // 2
        eff_row = row_at(tops, max_row, center)

        rid   = blip.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed", "")
        fname = rid_to_file.get(rid)
        if fname and eff_row not in row_to_img:
            img_path = os.path.join(xlsx_dir, "xl", "media", fname)
            if os.path.exists(img_path):
                row_to_img[eff_row] = img_path

    return row_to_img


def build_row_to_article(xlsx_dir):
    """
    Построить dict: excel_row (1-based) → артикул (из столбца C, индекс 2).
    Учитываем, что строка 1 = заголовок.
    """
    sheet_path = os.path.join(xlsx_dir, "xl", "worksheets", "sheet1.xml")
    ss_path    = os.path.join(xlsx_dir, "xl", "sharedStrings.xml")

    # sharedStrings → список строк
    shared = []
    if os.path.exists(ss_path):
        ss_tree = ET.parse(ss_path)
        for si in ss_tree.getroot().findall(".//ns:si", NS):
            parts = si.findall(".//ns:t", NS)
            shared.append("".join(p.text or "" for p in parts))

    sheet_tree = ET.parse(sheet_path)
    sheet_root = sheet_tree.getroot()

    row_to_article = {}
    for row_el in sheet_root.findall(".//ns:row", NS):
        row_num = int(row_el.attrib.get("r", 0))
        if row_num <= 1:
            continue  # заголовок

        # Найти ячейку столбца C (article column)
        for cell in row_el.findall("ns:c", NS):
            ref = cell.attrib.get("r", "")
            if not ref.startswith("C"):
                continue
            t   = cell.attrib.get("t", "")
            v   = cell.find("ns:v", NS)
            if v is None:
                break
            if t == "s":
                article = shared[int(v.text)].strip()
            else:
                # числовое значение артикула (float → int → str)
                try:
                    article = str(int(float(v.text)))
                except Exception:
                    article = v.text.strip()
            if article:
                row_to_article[row_num] = article
            break

    return row_to_article


def find_product_by_article(article, retries=3):
    """Найти product в МойСклад по артикулу, вернуть (id, images_count) или (None, 0)."""
    url = f"{BASE}/entity/product?filter=article={requests.utils.quote(article)}"
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, headers=HEADERS_JSON, timeout=40)
            if r.status_code != 200:
                return None, 0
            rows = r.json().get("rows", [])
            if not rows:
                return None, 0
            p = rows[0]
            img_count = p.get("images", {}).get("meta", {}).get("size", 0)
            return p["id"], img_count
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            print(f"         Таймаут поиска (попытка {attempt}/{retries})...")
            if attempt < retries:
                time.sleep(3 * attempt)
    return None, 0


def upload_image(product_id, image_path, dry_run=False, retries=3):
    """Загрузить изображение в карточку товара (JSON + base64)."""
    if dry_run:
        print(f"         [DRY] загрузка {os.path.basename(image_path)}")
        return True

    with open(image_path, "rb") as f:
        content_b64 = base64.b64encode(f.read()).decode("ascii")

    # МойСклад: имя файла должно иметь расширение png/jpg/...
    filename = os.path.basename(image_path)

    payload = {"filename": filename, "content": content_b64}
    url = f"{BASE}/entity/product/{product_id}/images"
    for attempt in range(1, retries + 1):
        try:
            r = requests.post(url, headers=HEADERS_JSON, json=payload, timeout=40)
            if r.status_code in (200, 201):
                return True
            print(f"         ОШИБКА загрузки: {r.status_code} {r.text[:200]}")
            return False
        except requests.exceptions.Timeout:
            print(f"         Таймаут загрузки (попытка {attempt}/{retries})...")
            if attempt < retries:
                time.sleep(3 * attempt)
    return False


# ── Основной цикл ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--articles", type=str, default=None,
                        help="Грузить картинки только для этих артикулов, напр. 120,121,122")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print("Распаковка XLSX...")
    xlsx_dir = unpack_xlsx(XLSX_PATH)

    try:
        row_to_img     = build_row_to_image(xlsx_dir)
        row_to_article = build_row_to_article(xlsx_dir)

        print(f"Картинок в drawing1: {len(row_to_img)}")
        print(f"Строк с артикулом:   {len(row_to_article)}")

        # Объединяем: article → image_path
        article_to_img = {}
        for row_num, article in row_to_article.items():
            if row_num in row_to_img:
                article_to_img[article] = row_to_img[row_num]

        print(f"Товаров с картинками: {len(article_to_img)}\n")

        items = list(article_to_img.items())
        if args.articles:
            wanted = {a.strip() for a in args.articles.split(",") if a.strip()}
            items = [(a, p) for a, p in items if a in wanted]
        elif args.limit:
            items = items[:args.limit]

        ok = 0
        not_found = 0
        errors = 0
        already = 0

        for i, (article, img_path) in enumerate(items, 1):
            fname = os.path.basename(img_path)
            print(f"[{i:3}/{len(items)}] {article:6} → {fname}")

            product_id, img_count = find_product_by_article(article)
            if not product_id:
                print(f"         Товар {article} не найден в МойСклад")
                not_found += 1
                time.sleep(0.2)
                continue

            if img_count > 0:
                print(f"         Уже есть картинка — пропуск")
                already += 1
                time.sleep(0.2)
                continue

            success = upload_image(product_id, img_path, dry_run=args.dry_run)
            if success:
                print(f"         → OK")
                ok += 1
            else:
                errors += 1

            time.sleep(DELAY)

        print(f"\n{'='*60}")
        print(f"Итого: {ok} загружено | {already} уже было | {not_found} не найдено | {errors} ошибок")

    finally:
        shutil.rmtree(xlsx_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
