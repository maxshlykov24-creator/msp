"""Смоук-тест ядра без Telegram: каталог → матчинг → продажа → остаток."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db, operations
from app.catalog import parse_csv
from app.pipeline import parse_text
from app.text import normalize
from app.text.matcher import Matcher

CSV = sys.argv[1] if len(sys.argv) > 1 else "/Users/max/Downloads/Новая таблица - Товары (копия).csv"


def main():
    conn = db.connect(":memory:")
    db.init_db(conn)

    products = parse_csv(CSV, only_active=True)
    print(f"[catalog] активных розничных позиций: {len(products)}")
    db.bulk_insert_products(conn, products)

    # примеры нормализации
    for s in ["пк 1 2 три мешка тысяча двести", "пшеница пять мешков", "пика два"]:
        print(f"[norm] {s!r} -> {normalize.normalize(s)!r}")

    matcher = Matcher(db.aliases_for_matching(conn), threshold=72)

    # продажа: несколько позиций
    text = "пшеница три мешка две тысячи десять дальше овёс два мешка тысяча сто восемьдесят"
    drafts = parse_text(matcher, text, expect_sum=True)
    print(f"\n[sales] распознано строк: {len(drafts)}")
    for d in drafts:
        print(f"  #{d.index} status={d.status} name={d.name} qty={d.qty} {d.unit} sum={d.line_sum} "
              f"cands={[ (c.name, round(c.score,1)) for c in d.candidates[:2]]}")

    ok = [d for d in drafts if d.ok]
    items = [operations.LineItem(d.product_id, d.name, d.qty, d.unit, d.bag_size_kg, d.price_bag, d.line_sum) for d in ok]
    if items:
        # приёмка сначала, чтобы был остаток
        operations.commit_receipt(conn, [operations.LineItem(i.product_id, i.name, 10, "bag", i.bag_size_kg) for i in items])
        r = operations.commit_sales(conn, items, total_spoken=None)
        print(f"\n[sales commit] session={r['session_id']} revenue={r['revenue']} discount={r['discount']}")
        for i in items:
            print(f"  остаток {i.name}: {db.stock_kg(conn, i.product_id):g} кг")

    # деньги
    m = normalize.parse_money_report("нал 15000 перевод 8000 расход 2000 касса 5000")
    print(f"\n[money] {m}")

    print("\nSMOKE OK")


if __name__ == "__main__":
    main()
