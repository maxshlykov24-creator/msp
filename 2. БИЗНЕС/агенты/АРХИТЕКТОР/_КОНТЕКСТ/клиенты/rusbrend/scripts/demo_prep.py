"""
demo_prep.py — Подготовка демо-данных RUSbrend в МойСклад к созвону.

Делает «красивые» данные для показа:
  1) del-demands  — удалить все тестовые отгрузки (после этого минусовые остатки → 0)
  2) enter-main   — оприходование показательных товаров на «Основной склад»
  3) enter-fulfil — оприходование пары позиций на склад «Фулфилмент» (клиентское)
  4) demands      — создать ~10 чистых демо-отгрузок и расставить разные статусы
                    (с доп-полями «Наименование» и «Комплектов»)
  5) fix-demands  — проставить «Наименование» и «Комплектов» во всех существующих
                    отгрузках (бэкфилл; «Тип короба» сохраняется)
  6) report       — показать остатки по складам (для проверки)
  7) all          — выполнить 1→4 по порядку

Использование:
  python3 demo_prep.py report
  python3 demo_prep.py del-demands
  python3 demo_prep.py enter-main
  python3 demo_prep.py enter-fulfil
  python3 demo_prep.py demands
  python3 demo_prep.py fix-demands
  python3 demo_prep.py all
  (флаг --dry-run — без записи, только лог)

Сеть нестабильна (SSL EOF) → у каждого запроса до 12 ретраев с backoff.
Идемпотентно по возможности: enter/demands помечаются префиксом в name.
"""

import sys, time, random, argparse
import requests

TOKEN = "951f3b09451c02691686ac8077cc59d474685a04"
BASE  = "https://api.moysklad.ru/api/remap/1.2"

SESS = requests.Session()
SESS.headers.update({
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json",
    "Accept-Encoding": "gzip",
})

# ── Метаданные (получены из API) ───────────────────────────────────────────────
STORE = {
    "Основной":   "bad1b6d6-4f99-11f1-0a80-07550012fedb",
    "Фулфилмент": "41f010dc-4f9c-11f1-0a80-075500137cfd",
}
ORG = {
    "RUSbrend": "bacfb4c2-4f99-11f1-0a80-07550012fed8",  # ИП Ткачев
    "GripOn":   "1c401361-4f9e-11f1-0a80-16560012de2e",
}
AGENT_OZON = "2f5dbcee-52c5-11f1-0a80-17d5007f6f31"

STATES = {
    "Новая":      "660e2016-4f9e-11f1-0a80-1c9700143ddb",
    "В сборке":   "660e21c5-4f9e-11f1-0a80-1c9700143ddc",
    "На проверке":"660e2229-4f9e-11f1-0a80-1c9700143ddd",
    "Проверено":  "660e228a-4f9e-11f1-0a80-1c9700143dde",
    "Отправлена": "660e22e7-4f9e-11f1-0a80-1c9700143ddf",
}

# атрибут товара «Организация» (для определения бренда)
ATTR_ORG = "e881075b-4f9d-11f1-0a80-156000141799"
# атрибуты отгрузки
DEMAND_ATTR_BOX  = "b2243b74-4fa3-11f1-0a80-0d080013d856"  # «Тип короба» (customentity, required)
DEMAND_ATTR_NAME = "3ab984f2-6eca-11f1-0a80-02f3003e352c"  # «Наименование» (string)
DEMAND_ATTR_KITS = "b2243885-4fa3-11f1-0a80-0d080013d855"  # «Комплектов» (long)
CE_BOX = "87b0e62a-4fa3-11f1-0a80-13470014140f"
BOX_S  = "106993b0-4fa4-11f1-0a80-07550014b96a"


def demand_attr(attr_id, value):
    """Атрибут отгрузки: value — строка/число (простой) или dict-meta (customentity)."""
    a = {"meta": {
        "href": f"{BASE}/entity/demand/metadata/attributes/{attr_id}",
        "type": "attributemetadata",
        "mediaType": "application/json",
    }}
    a["value"] = value
    return a


def box_value(box_item_id):
    return {"meta": {
        "href": f"{BASE}/entity/customentity/{CE_BOX}/{box_item_id}",
        "type": "customentity",
        "mediaType": "application/json",
    }}

PREFIX_ENTER  = "Демо-приёмка"
PREFIX_DEMAND = "DEMO"

random.seed(42)


def req(method, path, **kw):
    kw.setdefault("timeout", 45)
    last = None
    for a in range(1, 13):
        try:
            return SESS.request(method, BASE + path, **kw)
        except Exception as e:
            last = e
            time.sleep(min(2 * a, 12))
    raise RuntimeError(f"сеть не отвечает: {last}")


def meta(entity, _id, mtype=None):
    return {"meta": {
        "href": f"{BASE}/entity/{entity}/{_id}",
        "type": mtype or entity,
        "mediaType": "application/json",
    }}


def fetch_products():
    """article -> {id, name, brand, has_image, has_barcode}"""
    out = {}
    off = 0
    while True:
        r = req("GET", f"/entity/product?limit=1000&offset={off}")
        r.raise_for_status()
        rows = r.json()["rows"]
        for p in rows:
            art = (p.get("article") or "").strip()
            if not art:
                continue
            pname = (p.get("name") or "").lower()
            if "gripon" in pname or "грипон" in pname:
                brand = "GripOn"
            else:
                brand = "RUSbrend"
            out[art] = {
                "id": p["id"],
                "name": p.get("name", ""),
                "brand": brand,
                "has_image": p.get("images", {}).get("meta", {}).get("size", 0) > 0,
                "has_barcode": len(p.get("barcodes", []) or []) > 0,
            }
        if len(rows) < 1000:
            break
        off += 1000
    return out


# ── 1. Удаление отгрузок ───────────────────────────────────────────────────────
def del_demands(dry):
    n = 0
    while True:
        r = req("GET", "/entity/demand?limit=100")
        rows = r.json().get("rows", [])
        if not rows:
            break
        ids = [x["id"] for x in rows]
        if dry:
            print(f"  [DRY] удалить {len(ids)} отгрузок (показано первых имён): "
                  f"{[x.get('name') for x in rows[:3]]}")
            return len(ids)
        # пакетное удаление
        payload = [meta("demand", i) for i in ids]
        rr = req("POST", "/entity/demand/delete", json=payload)
        if rr.status_code not in (200, 201):
            # fallback поштучно
            for i in ids:
                req("DELETE", f"/entity/demand/{i}")
                time.sleep(0.1)
        n += len(ids)
        print(f"  удалено {n}...")
    print(f"Отгрузок удалено: {n}")
    return n


# ── 2-3. Оприходование (enter) ─────────────────────────────────────────────────
def make_enter(store_name, org_name, items, prods, qty_lo, qty_hi, dry):
    """items — список артикулов. Создаёт один документ enter."""
    positions = []
    for art in items:
        p = prods.get(art)
        if not p:
            print(f"    нет товара {art} — пропуск")
            continue
        qty = random.randint(qty_lo, qty_hi)
        positions.append({
            "quantity": qty,
            "price": random.randint(5, 80) * 10000,  # коп.: 50-800 ₽
            "assortment": meta("product", p["id"]),
        })
    if not positions:
        print("    нет позиций — документ не создан")
        return None
    payload = {
        "organization": meta("organization", ORG[org_name]),
        "store": meta("store", STORE[store_name]),
        "description": f"{PREFIX_ENTER} · {store_name}",
        "positions": positions,
    }
    if dry:
        print(f"  [DRY] enter {store_name}/{org_name}: {len(positions)} позиций")
        return None
    r = req("POST", "/entity/enter", json=payload)
    if r.status_code not in (200, 201):
        print(f"  ОШИБКА enter {store_name}: {r.status_code} {r.text[:300]}")
        return None
    print(f"  enter OK {store_name}/{org_name}: {len(positions)} позиций, id={r.json()['id']}")
    return r.json()


# ── 4. Демо-отгрузки со статусами ──────────────────────────────────────────────
def make_demands(items, prods, dry):
    state_cycle = ["Новая", "Новая", "В сборке", "В сборке", "На проверке",
                   "На проверке", "Проверено", "Проверено", "Отправлена", "Отправлена"]
    created = 0
    for idx, art in enumerate(items):
        p = prods.get(art)
        if not p:
            continue
        st = state_cycle[idx % len(state_cycle)]
        org = ORG[p["brand"]]
        kits = random.randint(6, 24)  # комплектов в коробе
        # name НЕ задаём — номер отгрузки присваивает сама МойСклад (по порядку).
        # Описание позиции кладём в доп-поле «Наименование» = «артикул — наименование».
        payload = {
            "organization": meta("organization", org),
            "agent": meta("counterparty", AGENT_OZON),
            "store": meta("store", STORE["Основной"]),
            "description": "Демонстрационная отгрузка (1 короб = 1 артикул)",
            "state": {"meta": {
                "href": f"{BASE}/entity/demand/metadata/states/{STATES[st]}",
                "type": "state",
                "mediaType": "application/json",
            }},
            "attributes": [
                demand_attr(DEMAND_ATTR_BOX,  box_value(BOX_S)),
                demand_attr(DEMAND_ATTR_NAME, f"{art} — {p['name']}"),
                demand_attr(DEMAND_ATTR_KITS, kits),
            ],
            "positions": [{
                "quantity": kits,
                "price": random.randint(10, 100) * 10000,
                "assortment": meta("product", p["id"]),
            }],
        }
        if dry:
            print(f"  [DRY] demand {art} → {st}")
            created += 1
            continue
        r = req("POST", "/entity/demand", json=payload)
        if r.status_code not in (200, 201):
            print(f"  ОШИБКА demand {art}: {r.status_code} {r.text[:300]}")
            continue
        print(f"  demand OK {art} → {st}")
        created += 1
        time.sleep(0.3)
    print(f"Демо-отгрузок создано: {created}")
    return created


# ── Бэкфилл доп-полей в существующих отгрузках ──────────────────────────────────
def fix_demands(dry):
    """Проставляет «Наименование» (имя позиции) и «Комплектов» (=кол-во в позиции)
    во ВСЕХ отгрузках. «Тип короба» сохраняется (берётся из самой отгрузки)."""
    fixed = 0
    off = 0
    while True:
        r = req("GET", "/entity/demand?limit=100&offset=" + str(off) +
                "&expand=positions.assortment")
        rows = r.json().get("rows", [])
        if not rows:
            break
        for d in rows:
            poss = (d.get("positions", {}) or {}).get("rows", [])
            if not poss:
                continue
            qty = poss[0].get("quantity", 0)
            assort = poss[0].get("assortment", {}) or {}
            art = (assort.get("article") or "").strip()
            base = assort.get("name", "") or d.get("name", "")
            pname = f"{art} — {base}" if art else base
            # сохранить существующий «Тип короба»
            box_val = None
            for at in d.get("attributes", []):
                if at.get("id") == DEMAND_ATTR_BOX:
                    box_val = at.get("value")
            attrs = []
            if box_val is not None:
                attrs.append(demand_attr(DEMAND_ATTR_BOX, box_val))
            else:
                attrs.append(demand_attr(DEMAND_ATTR_BOX, box_value(BOX_S)))
            attrs.append(demand_attr(DEMAND_ATTR_NAME, pname))
            attrs.append(demand_attr(DEMAND_ATTR_KITS, int(qty) if qty else 0))
            if dry:
                print(f"  [DRY] {d.get('name','?')[:40]} → Наименование={pname[:30]!r} Комплектов={qty}")
                fixed += 1
                continue
            rr = req("PUT", f"/entity/demand/{d['id']}", json={"attributes": attrs})
            if rr.status_code in (200, 201):
                print(f"  OK {d.get('name','?')[:40]} → '{pname[:30]}' / {qty}")
                fixed += 1
            else:
                print(f"  ОШИБКА {d.get('name','?')[:30]}: {rr.status_code} {rr.text[:200]}")
            time.sleep(0.2)
        if len(rows) < 100:
            break
        off += 100
    print(f"Обновлено отгрузок: {fixed}")
    return fixed


# ── Отчёт по остаткам ──────────────────────────────────────────────────────────
def report():
    r = req("GET", "/report/stock/all/current")
    data = r.json()
    pos = [x for x in data if x.get("stock", 0) > 0]
    neg = [x for x in data if x.get("stock", 0) < 0]
    print(f"Позиций с положительным остатком: {len(pos)}")
    print(f"Позиций с отрицательным остатком: {len(neg)}")
    if neg:
        print("  минусовые (первые 10):", [(x.get('assortmentId','')[:8], x['stock']) for x in neg[:10]])
    print("  топ остатков:", sorted([x['stock'] for x in pos], reverse=True)[:10])


# ── Выбор показательных товаров ────────────────────────────────────────────────
def pick_showcase(prods):
    """Возвращает (main_rus, main_grip, fulfil, demand_items)."""
    pref_rus = ["106", "103", "208"]
    with_img = [a for a, p in prods.items() if p["has_image"]]
    rus = [a for a in with_img if prods[a]["brand"] == "RUSbrend"]
    grip = [a for a in with_img if prods[a]["brand"] == "GripOn"]
    rus.sort(); grip.sort()
    # вынести предпочтительные вперёд
    for a in reversed(pref_rus):
        if a in rus:
            rus.remove(a); rus.insert(0, a)
    main_rus = rus[:18]
    main_grip = grip[:7]
    fulfil = (rus[18:21] + grip[7:8]) or rus[:4]
    demand_items = (main_rus[:7] + main_grip[:3])[:10]
    return main_rus, main_grip, fulfil, demand_items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["report", "del-demands", "enter-main",
                                    "enter-fulfil", "demands", "fix-demands",
                                    "all", "showcase"])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    dry = args.dry_run

    if args.cmd == "report":
        report(); return
    if args.cmd == "del-demands":
        del_demands(dry); return
    if args.cmd == "fix-demands":
        fix_demands(dry); return

    print("Сбор товаров...")
    prods = fetch_products()
    print(f"Товаров в МС: {len(prods)} | с фото: {sum(p['has_image'] for p in prods.values())}")
    main_rus, main_grip, fulfil, demand_items = pick_showcase(prods)

    if args.cmd == "showcase":
        print("MAIN RUSbrend:", main_rus)
        print("MAIN GripOn:", main_grip)
        print("FULFIL:", fulfil)
        print("DEMANDS:", demand_items)
        return
    if args.cmd in ("enter-main", "all"):
        print("\n== Оприходование на Основной склад ==")
        make_enter("Основной", "RUSbrend", main_rus, prods, 120, 600, dry)
        make_enter("Основной", "GripOn",   main_grip, prods, 80, 400, dry)
    if args.cmd in ("enter-fulfil", "all"):
        print("\n== Оприходование на Фулфилмент ==")
        make_enter("Фулфилмент", "RUSbrend", fulfil, prods, 30, 120, dry)
    if args.cmd in ("demands", "all"):
        print("\n== Демо-отгрузки со статусами ==")
        make_demands(demand_items, prods, dry)
    if args.cmd == "all":
        print("\n== Остатки после подготовки ==")
        report()


if __name__ == "__main__":
    main()
