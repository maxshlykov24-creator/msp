"""Prepare MANSBAND catalogue photos from the photographer's sheet and a read-only CSV export."""

import argparse
import csv
import fcntl
import hashlib
import io
import json
import os
import re
import struct
import tempfile
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

from openpyxl import load_workbook
from PIL import Image

HERE = Path(__file__).resolve().parent
WEB = HERE.parents[1] / "kassa/apps/web"
SELECTED = HERE / "selection.json"
INDEX = WEB / "src/data/catalogPhotos.json"
MEDIA = WEB / "public/catalog-photos"
EDGE_SMALL, EDGE_LARGE = 144, 1080
DIMENSION = re.compile(r"(?:[0-9]{1,3}(?:[./–-][0-9]{1,3})?|[2-9]?x{0,4}[sl]|m|one size|onesize)", re.I)


def norm(text):
    return " ".join(unicodedata.normalize("NFC", str(text or "")).lower().split())


def sheet_code(text):
    # Only for the crosswalk from the photographer to MoySklad. Keep the plus signs.
    return norm(text).replace("/", "-")


def read_inputs(args):
    files = defaultdict(list)
    for path in args.source.iterdir():
        if path.is_file() and path.suffix.lower() == ".arw":
            files[norm(path.stem)].append(path)
    sheets = list(args.source.glob("*.xlsx"))
    if len(sheets) != 1:
        raise ValueError("Ожидалась одна таблица XLSX в папке фотосессии")
    book = load_workbook(sheets[0], data_only=True)
    sheet = book["Фотосессия"]
    owners = {}
    for area in sheet.merged_cells.ranges:
        if area.min_col == area.max_col and area.min_col % 2:
            for row in range(area.min_row, area.max_row + 1):
                owners[(area.min_col, row)] = area.min_row
    groups, unnumbered = {}, []
    for col in range(1, sheet.max_column, 2):
        if sheet.cell(1, col).value != "№":
            raise ValueError(f"Неожиданная шапка {sheet.cell(1, col).coordinate}")
        category = str(sheet.cell(1, col + 1).value).strip()
        for row in range(2, sheet.max_row + 1):
            cell = sheet.cell(row, col + 1)
            if cell.value is None or not str(cell.value).strip():
                continue
            start = owners.get((col, row), row)
            number = sheet.cell(start, col).value
            if type(number) is not int:
                unnumbered.append(cell.coordinate)
                continue
            key = sheet.cell(start, col + 1).coordinate
            group = groups.setdefault(key, dict(category=category, number=number, entries=[]))
            group["entries"].append(str(cell.value).strip())
    book.close()
    with args.catalog.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {"ms_id", "ms_type", "name", "category", "size", "height", "color", "pattern", "fit",
                    "variation", "suit_part", "suit_line"}
        if not required.issubset(reader.fieldnames or ()):
            raise ValueError("Выгрузи каталог с колонками из плана")
        catalog = list(reader)
    ids = [r["ms_id"] for r in catalog]
    if not ids or "" in ids or len(ids) != len(set(ids)):
        raise ValueError("Пустой каталог, пропущенный или повторяющийся ms_id")
    return files, groups, unnumbered, catalog


def sheet_sources(group, files):
    return [p for stem in {norm(v) for v in group["entries"]} for p in files.get(stem, [])]


def catalogue_candidates(catalog):
    # Keys include a colour only when that exact colour exists on a live modification.
    exact, colored = defaultdict(Counter), defaultdict(Counter)
    for row in catalog:
        if row["ms_type"] != "variant" or not row["variation"]:
            continue
        key = sheet_code(row["variation"])
        facts = (row["variation"], row["name"].partition(" (")[0], row["color"], row["pattern"], row["fit"])
        exact[key][facts] += 1
        if row["color"]:
            colored[key + "-" + norm(row["color"])][facts] += 1
    folded = defaultdict(set)
    for key in exact.keys() | colored.keys():
        folded[key.replace("м", "m")].add(key)

    def candidates(entry):
        key = sheet_code(entry)
        facts = exact.get(key, Counter()) + colored.get(key, Counter())
        method = "точный код" if key in exact else "код и цвет"
        if not facts:
            angle = re.sub(r"-[0-9]+$", "", key)
            facts = colored.get(angle)
            method = "код, цвет и номер кадра"
        if not facts:
            keys = folded.get(key.replace("м", "m"), ())
            facts = Counter()
            for alternative in keys:
                facts.update(exact.get(alternative) or colored.get(alternative, {}))
            method = "проверить m и м" if facts else "нет соответствия"
        return dict(entry=entry, method=method, candidates=[
            dict(variation=v, model=m, color=c, pattern=p, fit=f, count=n)
            for (v, m, c, p, f), n in sorted(facts.items())])
    return candidates


def inventory(groups, files, unnumbered, candidates):
    counts = Counter((g["category"], g["number"]) for g in groups.values())
    used = {norm(a) for g in groups.values() for a in g["entries"]}
    rows = []
    for key, group in groups.items():
        sources = sorted(p.name for p in sheet_sources(group, files))
        if counts[group["category"], group["number"]] != 1:
            status = "повтор номера"
        elif len(sources) == 1:
            status = "один исходник"
        else:
            status = "несколько исходников" if sources else "нет исходника"
        rows.append(dict(cell=key, **group, sources=sources, status=status,
                         catalog=[candidates(a) for a in group["entries"]]))
    return dict(groups=rows, unnumbered=unnumbered,
                unlisted=sorted(p.name for k in files.keys() - used for p in files[k]))


def camera_preview(path):
    # Sony stores several in-camera JPEGs in the standard TIFF IFD chain.
    # Read only offsets and embedded JPEGs. Orientation comes from their TIFF directory.
    with path.open("rb") as stream:
        header = stream.read(8)
        order = {b"II": "<", b"MM": ">"}.get(header[:2])
        if not order or struct.unpack(order + "H", header[2:4])[0] != 42:
            raise ValueError(f"Неизвестный контейнер ARW: {path.name}")
        offset = struct.unpack(order + "I", header[4:])[0]
        seen, previews = set(), []
        while offset:
            if offset in seen or len(seen) > 32:
                raise ValueError(f"Повреждены каталоги TIFF: {path.name}")
            seen.add(offset)
            stream.seek(offset)
            count, = struct.unpack(order + "H", stream.read(2))
            fields = {}
            for _ in range(count):
                tag, kind, length, raw = struct.unpack(order + "HHI4s", stream.read(12))
                if length == 1 and kind in (3, 4):
                    fields[tag] = (struct.unpack(order + "H", raw[:2]) if kind == 3
                                   else struct.unpack(order + "I", raw))[0]
            offset, = struct.unpack(order + "I", stream.read(4))
            if 513 in fields and 514 in fields:
                previews.append((fields[514], fields[513], fields.get(274, 1)))
        for length, start, orientation in sorted(previews):
            if not 0 < length <= 40_000_000 or start + length > path.stat().st_size:
                continue
            stream.seek(start)
            with Image.open(io.BytesIO(stream.read(length))) as img:
                if img.format != "JPEG" or max(img.size) < EDGE_LARGE:
                    continue
                operations = [None, None, Image.Transpose.FLIP_LEFT_RIGHT, Image.Transpose.ROTATE_180,
                              Image.Transpose.FLIP_TOP_BOTTOM, Image.Transpose.TRANSPOSE,
                              Image.Transpose.ROTATE_270, Image.Transpose.TRANSVERSE, Image.Transpose.ROTATE_90]
                if orientation not in range(1, 9):
                    raise ValueError(f"Неизвестная ориентация: {path.name}")
                rgb = img.convert("RGB")
                return rgb.transpose(operations[orientation]) if operations[orientation] else rgb
    raise ValueError(f"Нет встроенного JPEG достаточного размера: {path.name}")


def encode(path):
    original = camera_preview(path)
    result = []
    for edge, quality, max_bytes in [(EDGE_SMALL, 76, 12 * 1024), (EDGE_LARGE, 80, 180 * 1024)]:
        frame = original.copy()
        frame.thumbnail((edge, edge), Image.Resampling.LANCZOS)
        for q in range(quality, 59, -4):
            buf = io.BytesIO()
            frame.save(buf, format="WEBP", quality=q, method=6, exif=b"", icc_profile=b"")
            data = buf.getvalue()
            if len(data) <= max_bytes:
                result.append(data)
                break
        else:
            raise ValueError(f"Превышен лимит веса, требуется просмотр: {path.name}")
    return result


def split_name(name):
    base, sep, rest = norm(name).rpartition(" (")
    if not sep or not rest.endswith(")"):
        return "", []
    return base, [s.strip() for s in rest[:-1].split(",")]


def signature(tokens, positions):
    return "|".join(t for i, t in enumerate(tokens) if i not in positions)


def name_layout(row):
    base, tokens = split_name(row["name"])
    if not base or "|" in row["name"]:
        raise ValueError(f"Не разобрано наименование {row['name']}")
    positions = []
    for field in ("size", "height"):
        value = norm(row[field])
        if not value:
            continue
        if not DIMENSION.fullmatch(value) or tokens.count(value) != 1:
            raise ValueError(f"Неоднозначный размер или рост: {row['name']}")
        positions.append(tokens.index(value))
    if not positions or len(set(positions)) != len(positions):
        raise ValueError(f"Не подтверждены размер и рост: {row['name']}")
    if not all(norm(row[field]) in tokens for field in ("variation", "color", "pattern", "fit") if row[field]):
        raise ValueError(f"Характеристики расходятся с именем: {row['name']}")
    return base, ",".join(str(i) for i in sorted(positions)), signature(tokens, positions)


def lookup(products, name):
    base, tokens = split_name(name)
    matches = set()
    for mask, rules in products.get(base, {}).items():
        positions = [int(i) for i in mask.split(",")]
        if all(i < len(tokens) and DIMENSION.fullmatch(tokens[i]) for i in positions):
            value = rules.get(signature(tokens, positions))
            if value:
                matches.add(value)
    if len(matches) > 1:
        raise ValueError(f"Конфликт правил: {name}")
    return next(iter(matches), None)


def put(mapping, key, asset):
    if mapping.setdefault(key, asset) != asset:
        raise ValueError(f"Два разных снимка для одного ключа {key}")


def suit_key(row):
    return "|".join(norm(row[field]) for field in ("suit_line", "variation", "color", "pattern", "fit"))


def suit_piece_without_photo(row):
    model = norm(row["name"]).partition(" (")[0]
    if row["suit_part"] not in ("trousers", "vest") and not any(w in model for w in ("брюки", "слаксы", "жилет")):
        return False
    category = norm(row["category"])
    if any(word in category or word in model for word in ("костюм", "смокинг")):
        return True
    # У отдельных брюк в папке одежды тоже бывает suit_part=trousers.
    # Без подтверждающей папки отдельного товара оставляем часть без фото.
    return "одежд" not in category and category.split("/")[0] not in ("clothes", "outerwear")


def attach_code_shots(choice, group, by_variation, files, sources):
    # Один файл на все цвета и кроя артикула. «-» и «/» один разделитель, регистр не важен.
    cell = choice["cell"]
    evidence = str(choice.get("code_match_evidence", "")).strip()
    shots = choice.get("shots") or []
    if not evidence or not shots:
        raise ValueError(f"{cell}: для общего кадра артикула нужна сверка владельца и список кадров")
    if any(key in choice for key in ("source", "variations", "models", "color", "pattern")):
        raise ValueError(f"{cell}: общий кадр артикула не смешивается с отбором одной расцветки")
    excluded = {norm(k): v for k, v in choice.get("excluded_entries", {}).items()}
    entries = group["catalog"]
    entry_norms = {norm(e["entry"]) for e in entries}
    if excluded.keys() - entry_norms or any(not str(v or "").strip() for v in excluded.values()):
        raise ValueError(f"{cell}: укажи причину для каждой пропущенной записи этой группы")
    if entry_norms <= excluded.keys():
        raise ValueError(f"{cell}: нельзя пропустить все записи группы")
    by_source = {}
    for shot in shots:
        key = sheet_code(shot["source"])
        if key in by_source:
            raise ValueError(f"{cell}: повтор кадра {shot['source']}")
        by_source[key] = shot
    used = set()
    for entry in entries:
        if norm(entry["entry"]) in excluded:
            continue
        key = sheet_code(entry["entry"])
        if key not in by_source:
            raise ValueError(f"{cell}: запись {entry['entry']} не входит ни в кадр, ни в пропуск")
        used.add(key)
    if set(by_source) != used:
        raise ValueError(f"{cell}: в кадрах есть артикул, которого нет в группе")
    attached = []
    for shot in shots:
        variations, seen_variations = [], set()
        for value in shot["variations"]:
            actual = norm(value)
            if not actual or actual in seen_variations:
                raise ValueError(f"{cell}: повтор или пустая вариация у {shot['source']}")
            seen_variations.add(actual)
            variations.append(actual)
        seen_looks = set()
        for item in shot["appearances"]:
            look = (norm(item["model"]), norm(item["color"]), norm(item["pattern"]), norm(item.get("fit", "")))
            if look in seen_looks or not look[0]:
                raise ValueError(f"{cell}: повтор или пустой вид у {shot['source']}")
            seen_looks.add(look)
        if not variations or not seen_looks:
            raise ValueError(f"{cell}: у {shot['source']} нет вариаций или видов")
        rows, seen_ids = [], set()
        for code in variations:
            for row in by_variation.get(code, []):
                if row["ms_type"] != "variant":
                    continue
                look = (split_name(row["name"])[0], norm(row["color"]), norm(row["pattern"]), norm(row["fit"]))
                if look not in seen_looks or suit_piece_without_photo(row) or row["ms_id"] in seen_ids:
                    continue
                seen_ids.add(row["ms_id"])
                rows.append(row)
        if not rows:
            raise ValueError(f"{cell}: каталог не подтверждает {shot['source']}")
        got_variations = {norm(row["variation"]) for row in rows}
        got_looks = {(split_name(row["name"])[0], norm(row["color"]), norm(row["pattern"]), norm(row["fit"]))
                     for row in rows}
        if got_variations != seen_variations or got_looks != seen_looks:
            raise ValueError(f"{cell}: каталог не подтверждает все виды {shot['source']}")
        selected_files = files.get(norm(shot["source"]), [])
        if len(selected_files) != 1:
            raise ValueError(f"{cell}: выбранный ARW отсутствует или его имя неоднозначно")
        path, = selected_files
        if (sources and path not in sources) or (len(sources) != 1 and not evidence):
            raise ValueError(f"{cell}: требуется подтверждённый кадр этой группы")
        suit = bool(shot.get("suit"))
        if suit and (any(row["suit_part"] != "jacket" for row in rows) or any(not row["suit_line"] for row in rows)):
            raise ValueError(f"{cell}: фото костюма можно привязать только к пиджаку известной линии")
        attached.append((path, rows, suit))
    return attached


def match_selection(choice, group, by_variation):
    codes = {norm(v) for v in choice["variations"]}
    color, pattern = norm(choice["color"]), norm(choice["pattern"])
    models = {norm(v) for v in choice["models"]}
    fits = {norm(v) for v in choice.get("fits", [])}
    overrides = {norm(k): v for k, v in choice.get("confirmed_codes", {}).items()}
    excluded = {norm(k): v for k, v in choice.get("excluded_entries", {}).items()}
    entries = {norm(e["entry"]) for e in group["catalog"]}
    if overrides.keys() - entries:
        raise ValueError(f"{choice['cell']}: ручная привязка относится к другой группе Excel")
    if (excluded.keys() - entries or excluded.keys() & overrides.keys() or entries <= excluded.keys()
            or any(not str(reason or "").strip() for reason in excluded.values())):
        raise ValueError(f"{choice['cell']}: укажи причину для каждой пропущенной записи этой группы")
    matched = set()
    for entry in group["catalog"]:
        if norm(entry["entry"]) in excluded:
            continue
        candidates = entry["candidates"]
        override = overrides.get(norm(entry["entry"]))
        if override:
            actual = norm(override.get("variation"))
            if not str(override.get("evidence", "")).strip() or actual not in by_variation or not actual:
                raise ValueError(f"{choice['cell']}: ручной код требует источника и точной вариации в кассе")
            candidates = [dict(variation=r["variation"], model=split_name(r["name"])[0], color=r["color"],
                               pattern=r["pattern"], fit=r["fit"]) for r in by_variation[actual]]
        looks = {(norm(c["color"]), norm(c["pattern"]), norm(c["fit"])) for c in candidates
                 if norm(c["model"]) in models}
        if len(looks) > 1 and not str(choice.get("appearance_evidence", "")).strip():
            raise ValueError(f"{choice['cell']}: несколько вариантов внешнего вида, нужна документированная сверка")
        same_color_fits = {fit for c, p, fit in looks if (c, p) == (color, pattern)}
        if len(same_color_fits) > 1 and not fits:
            raise ValueError(f"{choice['cell']}: укажи подтверждённые значения fits")
        if not override and entry["method"] == "проверить m и м" and choice.get("confirmed_m") is not True:
            raise ValueError(f"{choice['cell']}: требуется явная сверка латинской и кириллической m")
        options = {norm(c["variation"]) for c in candidates if norm(c["color"]) == color}
        if len(options) != 1:
            raise ValueError(f"{choice['cell']}: неоднозначные код и цвет для {entry['entry']}")
        matched |= options
    if matched != codes or len(codes) != len(choice["variations"]):
        raise ValueError(f"{choice['cell']}: проверь все артикулы группы")
    selected = [r for code in sorted(codes) for r in by_variation[code] if r["ms_type"] == "variant"
                and split_name(r["name"])[0] in models and norm(r["color"]) == color
                and norm(r["pattern"]) == pattern and (not fits or norm(r["fit"]) in fits)]
    if fits and fits != {norm(r["fit"]) for r in selected}:
        raise ValueError(f"{choice['cell']}: каталог не подтверждает все указанные варианты кроя")
    if {norm(r["variation"]) for r in selected} != codes or {split_name(r["name"])[0] for r in selected} != models:
        raise ValueError(f"{choice['cell']}: каталог не подтверждает перечисленные модели и вариации")
    for row in selected:
        if suit_piece_without_photo(row):
            raise ValueError(f"{choice['cell']}: костюмные брюки и жилеты остаются без фото: {row['name']}")
        if choice.get("suit") and row["suit_part"] != "jacket":
            raise ValueError(f"{choice['cell']}: фото костюма можно привязать только к пиджаку: {row['name']}")
    return selected


def write_atomic(path, data):
    if path.exists() and path.read_bytes() == data:
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        stage = Path(stream.name)
        stream.write(data)
    stage.chmod(0o644)
    stage.replace(path)


def publish(index, assets, catalog):
    MEDIA.mkdir(parents=True, exist_ok=True)
    fd = os.open(INDEX.parent, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        if INDEX.exists():
            old = json.loads(INDEX.read_text())
            for base, layouts in old["products"].items():
                for mask, keys in layouts.items():
                    for key, asset in keys.items():
                        replacement = index["products"].get(base, {}).get(mask, {}).get(key)
                        if replacement == asset:
                            continue
                        if replacement is None:
                            old_rule = {base: {mask: {key: asset}}}
                            covered = [r for r in catalog if lookup(old_rule, r["name"])]
                            if covered and all(suit_piece_without_photo(r) for r in covered):
                                continue
                        raise ValueError("Новая партия меняет уже принятое правило: " + base)
            if any(index["suits"].get(k) != v for k, v in old["suits"].items()):
                raise ValueError("Новая партия меняет уже принятый костюм")
        for asset, pair in assets.items():
            for suffix, data in zip(("s", "l"), pair):
                path = MEDIA / f"{asset}-{suffix}.webp"
                if path.exists() and path.read_bytes() != data:
                    raise ValueError("Повреждён файл " + path.name)
                write_atomic(path, data)
        # All immutable files exist before readers can see the new index.
        data = json.dumps(index, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        write_atomic(INDEX, data.encode())
    finally:
        os.close(fd)


def build(args, files, catalog, audit):
    choices = json.loads(args.selection.read_text())
    cells = [s["cell"] for s in choices]
    if not cells or len(cells) != len(set(cells)):
        raise ValueError("Пустой список или повтор ячейки в отборе")
    groups = {r["cell"]: r for r in audit["groups"]}
    by_variation = defaultdict(list)
    for row in catalog:
        by_variation[norm(row["variation"])].append(row)
    index = dict(products={}, suits={})
    assets, expected, summary = {}, {}, []
    for choice in choices:
        cell = choice["cell"]
        if cell not in groups or groups[cell]["status"] == "повтор номера":
            raise ValueError(f"{cell}: нужна однозначная пронумерованная группа Excel")
        group = groups[cell]
        sources = sheet_sources(group, files)
        def record(path, rows, suit):
            small, large = encode(path)
            asset = hashlib.sha256(small + large).hexdigest()[:32]
            assets[asset] = (small, large)
            for row in rows:
                base, mask, key = name_layout(row)
                put(index["products"].setdefault(base, {}).setdefault(mask, {}), key, asset)
                if row["ms_id"] in expected:
                    raise ValueError("Карточка повторяется в двух группах: " + row["name"])
                expected[row["ms_id"]] = asset
                if suit:
                    if row["suit_part"] != "jacket" or not row["suit_line"]:
                        raise ValueError(f"{cell}: фото костюма можно привязать только к пиджаку известной линии")
                    put(index["suits"], suit_key(row), asset)
            summary.append(dict(cell=cell, number=group["number"], source=path.name, asset=asset,
                                variants=len(rows), sizes=sorted({row["size"] for row in rows}),
                                heights=sorted({row["height"] for row in rows}), raw=path.stat().st_size,
                                small=len(small), large=len(large)))

        def record_extra():
            extra = choice.get("also_shots")
            if not extra:
                return
            if not str(choice.get("code_match_evidence", "")).strip():
                raise ValueError(f"{cell}: для дополнительного артикула нужна сверка владельца")
            shot_keys = {sheet_code(shot["source"]) for shot in extra}
            synthetic = {
                "cell": cell,
                "code_match_evidence": choice["code_match_evidence"],
                "shots": extra,
                "excluded_entries": {
                    entry["entry"]: "Эту запись закрывает основной кадр ячейки"
                    for entry in group["catalog"]
                    if sheet_code(entry["entry"]) not in shot_keys
                },
            }
            for path, rows, suit in attach_code_shots(synthetic, group, by_variation, files, sources):
                record(path, rows, suit)

        if "shots" in choice:
            for path, rows, suit in attach_code_shots(choice, group, by_variation, files, sources):
                record(path, rows, suit)
            record_extra()
            continue
        selected_files = files.get(norm(choice["source"]), [])
        if len(selected_files) != 1:
            raise ValueError(f"{cell}: выбранный ARW отсутствует или его имя неоднозначно")
        path, = selected_files
        if (sources and path not in sources) or (len(sources) != 1 and not str(choice.get("source_evidence", "")).strip()):
            raise ValueError(f"{cell}: требуется подтверждённый основной кадр и source_evidence")
        rows = match_selection(choice, group, by_variation)
        small, large = encode(path)
        asset = hashlib.sha256(small + large).hexdigest()[:32]
        assets[asset] = (small, large)
        for row in rows:
            base, mask, key = name_layout(row)
            put(index["products"].setdefault(base, {}).setdefault(mask, {}), key, asset)
            if row["ms_id"] in expected:
                raise ValueError("Карточка повторяется в двух группах: " + row["name"])
            expected[row["ms_id"]] = asset
            if choice.get("suit") and row["suit_part"] == "jacket":
                if not row["suit_line"]:
                    raise ValueError(f"{cell}: неизвестная линия костюма")
                put(index["suits"], suit_key(row), asset)
        summary.append(dict(cell=cell, number=group["number"], source=path.name, asset=asset,
                            variants=len(rows), sizes=sorted({r["size"] for r in rows}),
                            heights=sorted({r["height"] for r in rows}), raw=path.stat().st_size,
                            small=len(small), large=len(large)))
        record_extra()
    for row in catalog:
        found = lookup(index["products"], row["name"])
        if found and suit_piece_without_photo(row):
            raise ValueError("Фото попало на костюмные брюки или жилет: " + row["name"])
        if found != expected.get(row["ms_id"]):
            raise ValueError("Лишняя или пропущенная привязка: " + row["name"])
        if row["suit_part"] == "jacket":
            suit = index["suits"].get(suit_key(row))
            if suit and suit != expected.get(row["ms_id"]):
                raise ValueError("Костюм захватывает посторонний товар: " + row["name"])
    report = dict(groups=summary, catalog=len(catalog), matched=len(expected), assets=len(assets),
                  total_webp=sum(sum(map(len, pair)) for pair in assets.values()))
    if args.apply:
        if (args.expect_groups, args.expect_links) != (len(choices), len(expected)):
            raise ValueError("Для записи укажи точные --expect-groups и --expect-links из проверки")
        publish(index, assets, catalog)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("scan", "build"))
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--selection", type=Path, default=SELECTED)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expect-groups", type=int)
    parser.add_argument("--expect-links", type=int)
    args = parser.parse_args()
    files, groups, unnumbered, catalog = read_inputs(args)
    result = inventory(groups, files, unnumbered, catalogue_candidates(catalog))
    if args.command == "build":
        result = build(args, files, catalog, result)
        print(f"Групп {len(result['groups'])}, карточек {result['matched']} из {result['catalog']}, "
              f"пар WebP {result['assets']}, байт {result['total_webp']}. "
              + ("Записано." if args.apply else "Проверено без записи в кассу."))
    else:
        print(dict(Counter(r["status"] for r in result["groups"])),
              "Ячеек без номера:", len(unnumbered), "Файлов вне таблицы:", len(result["unlisted"]))
    if args.report:
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
