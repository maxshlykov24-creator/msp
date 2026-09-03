"""Извлечь артикул - штрихкод - наименование из xlsx (PDF в D, баркод в E)."""
import zipfile, xml.etree.ElementTree as ET, re, time, json, sys
from collections import defaultdict
from io import BytesIO
from pathlib import Path
import requests
from pypdf import PdfReader

PATH = sys.argv[1] if len(sys.argv) > 1 else '/Users/max/Downloads/Новая таблица - 2026-06-29T165843.712.xlsx'
OUT = Path(__file__).resolve().parent.parent / 'артикулы_штрихкоды_2026-06-29.txt'
CACHE = Path('/tmp/rusbrend_bc_cache.json')

ns = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}


def col_num(letters):
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n


def norm_article(v):
    s = str(v).strip()
    if not s:
        return ''
    if re.fullmatch(r'\d+\.0+', s):
        s = str(int(float(s)))
    return s


def norm_barcode(v):
    if v is None:
        return ''
    s = str(v).strip()
    if not s:
        return ''
    if re.match(r'^OZN', s, re.I):
        return s.upper()
    try:
        if 'E' in s.upper() or re.fullmatch(r'\d+\.0+', s):
            s = str(int(float(s.replace(',', '.'))))
    except Exception:
        pass
    if s.upper().startswith('OZN'):
        return s.upper()
    return re.sub(r'\D', '', s)


def ean_ok(s):
    if not re.fullmatch(r'\d{13}', s):
        return False
    sm = sum(int(s[i]) * (1 if i % 2 == 0 else 3) for i in range(12))
    return ((10 - (sm % 10)) % 10) == int(s[12])


def extract_bc_from_text(text):
    if not text:
        return None
    ozn = re.search(r'OZN\d+', text, re.I)
    if ozn:
        return ozn.group(0).upper()
    for m in re.findall(r'\d{13}', text):
        if ean_ok(m):
            return m
    for ln in text.splitlines():
        ln = ln.strip()
        if re.fullmatch(r'\d{13}', ln) and ean_ok(ln):
            return ln
        if re.fullmatch(r'OZN\d+', ln, re.I):
            return ln.upper()
    return None


def drive_id(url):
    if not url:
        return None
    m = re.search(r'/file/d/([^/]+)', url)
    if m:
        return m.group(1)
    return None


def fetch_pdf_text(fid, cache):
    if fid in cache:
        return cache[fid]
    url = f'https://drive.google.com/uc?export=download&id={fid}'
    text = ''
    for a in range(1, 5):
        try:
            r = requests.get(url, timeout=40)
            r.raise_for_status()
            if b'%PDF' not in r.content[:20]:
                time.sleep(2 * a)
                continue
            reader = PdfReader(BytesIO(r.content))
            text = '\n'.join((p.extract_text() or '') for p in reader.pages)
            break
        except Exception:
            time.sleep(2 * a)
    cache[fid] = text
    return text


def parse_xlsx(path):
    with zipfile.ZipFile(path) as z:
        shared = []
        root = ET.fromstring(z.read('xl/sharedStrings.xml'))
        for si in root.findall('.//m:si', ns):
            texts = [t.text or '' for t in si.findall('.//m:t', ns)]
            shared.append(''.join(texts))
        rels = ET.fromstring(z.read('xl/worksheets/_rels/sheet1.xml.rels'))
        rid_to_url = {rel.get('Id'): rel.get('Target') for rel in rels}
        sheet = ET.fromstring(z.read('xl/worksheets/sheet1.xml'))
        cells = defaultdict(dict)
        hyper = defaultdict(str)
        for c in sheet.findall('.//m:sheetData/m:row/m:c', ns):
            ref = c.get('r', '')
            m = re.match(r'([A-Z]+)(\d+)', ref)
            if not m:
                continue
            row = int(m.group(2))
            col = col_num(m.group(1))
            t = c.get('t')
            v = c.find('m:v', ns)
            val = ''
            if v is not None:
                val = v.text or ''
                if t == 's':
                    val = shared[int(val)]
            cells[row][col] = val
        for hl in sheet.findall('.//m:hyperlink', ns):
            ref = hl.get('ref')
            m = re.match(r'([A-Z]+)(\d+)', ref)
            if not m:
                continue
            row = int(m.group(2))
            rid = hl.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
            hyper[row] = rid_to_url.get(rid, '')

    rows = []
    for r in sorted(cells):
        if r == 1:
            continue
        art = norm_article(cells[r].get(2, ''))
        name = str(cells[r].get(3, '') or '').strip().replace('\n', ' ')
        bc_raw = cells[r].get(5, '')
        if not art:
            continue
        rows.append({'art': art, 'name': name, 'bc_raw': bc_raw, 'pdf_url': hyper.get(r, '')})
    return rows


def main():
    cache = {}
    if CACHE.exists():
        cache = json.loads(CACHE.read_text(encoding='utf-8'))

    rows = parse_xlsx(PATH)
    results = []
    errors = []

    for i, x in enumerate(rows, 1):
        art, name = x['art'], x['name']
        if str(x['bc_raw']).strip():
            bc = norm_barcode(x['bc_raw'])
            results.append((art, bc, name))
            continue
        fid = drive_id(x['pdf_url'])
        if not fid:
            errors.append((art, 'нет PDF ссылки'))
            results.append((art, '', name))
            continue
        text = fetch_pdf_text(fid, cache)
        bc = extract_bc_from_text(text) or ''
        if not bc:
            errors.append((art, 'ШК не найден в PDF'))
        results.append((art, bc, name))
        if i % 20 == 0:
            CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding='utf-8')
            print(f'  {i}/{len(rows)}...', flush=True)
        time.sleep(0.12)

    CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding='utf-8')
    OUT.write_text('\n'.join(f'{a} - {b} - {n}' for a, b, n in results) + '\n', encoding='utf-8')

    print('=== ИТОГ ===')
    print('Всего:', len(results))
    print('С ШК:', sum(1 for _, b, _ in results if b))
    print('Без ШК:', sum(1 for _, b, _ in results if not b))
    print('Ошибок:', len(errors))
    print('Файл:', OUT)


if __name__ == '__main__':
    main()
