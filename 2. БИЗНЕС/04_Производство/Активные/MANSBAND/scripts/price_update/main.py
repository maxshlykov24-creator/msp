"""Обновление прайса в МойСклад (созвон 09.09, Блок 2 плана «Костюм в кассе,
цены прайса, поиск по всему каталогу»).

    python3 main.py --scan            # отчёт: что нашлось, что изменится, блокеры
    python3 main.py --apply           # применяет последний --scan, требует «да» с клавиатуры
    python3 main.py --apply --scan-file _private/price_update/scan_2026-09-09T120000.json

Правило верификации (.cursor/rules/11-верификация.mdc): --apply без явного
«да» на отчёт --scan не запускается — это не только словесное «да» владельцу
в чате, но и отдельное подтверждение здесь, в терминале, перед записью в МС.

Костюмы целиком в этот скрипт не входят: их цена — только в матрице кассы
(packages/shared/src/suitPrices.ts), в МойСклад не пишется (см. план, 2.2).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import STATE_DIR
from ms_client import MoySklad
from rules import load_rules, match_active_rules

CONFIRM_WORDS = {"да", "ок", "окей", "делай", "согласен", "применяй", "подтверждаю"}


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")


def cmd_scan(args: argparse.Namespace) -> None:
    rules = load_rules()
    ms = MoySklad()

    matched: Dict[str, List[Dict[str, Any]]] = {r["id"]: [] for r in rules}
    ambiguous: List[Dict[str, Any]] = []
    no_sale_price: List[Dict[str, Any]] = []
    total_variants = 0

    print("Читаю каталог МойСклад (все модификации)…", flush=True)
    for v in ms.iter_variants():
        total_variants += 1
        product = v.get("product") or {}
        product_name = product.get("name") or ""
        if not product_name:
            continue
        hits = match_active_rules(rules, product_name)
        if len(hits) > 1:
            ambiguous.append(
                {
                    "variantId": v["id"],
                    "variantName": v.get("name") or product_name,
                    "rules": [h["id"] for h in hits],
                }
            )
            continue
        if not hits:
            continue
        rule = hits[0]
        sale_prices = v.get("salePrices") or []
        target_kopecks = rule["priceRub"] * 100
        if not sale_prices:
            no_sale_price.append(
                {"variantId": v["id"], "variantName": v.get("name") or product_name, "rule": rule["id"]}
            )
            current_rub = None
        else:
            current_rub = sale_prices[0]["value"] / 100
        matched[rule["id"]].append(
            {
                "variantId": v["id"],
                "variantName": v.get("name") or product_name,
                "currentRub": current_rub,
                "targetRub": rule["priceRub"],
                "changed": current_rub != rule["priceRub"],
                "salePrices": sale_prices,
            }
        )
        if total_variants % 500 == 0:
            print(f"  …{total_variants} модификаций просмотрено", flush=True)

    blockers_no_match = [r["id"] for r in rules if r.get("active", True) and not matched[r["id"]]]

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    report_path = STATE_DIR / f"scan_{_ts()}.json"
    report = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "totalVariants": total_variants,
        "matched": matched,
        "ambiguous": ambiguous,
        "noSalePrice": no_sale_price,
        "blockersNoMatch": blockers_no_match,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    to_change_total = sum(1 for items in matched.values() for it in items if it["changed"])
    print(f"\nПросмотрено модификаций: {total_variants}")
    print(f"Правил с попаданиями: {sum(1 for v in matched.values() if v)} из {len(rules)}")
    print(f"Позиций, где цена изменится: {to_change_total}")
    print(f"Отчёт: {report_path}\n")

    print("По правилам:")
    for r in rules:
        items = matched[r["id"]]
        changed = sum(1 for it in items if it["changed"])
        status = "active" if r.get("active", True) else "выключено"
        if not items:
            print(f"  ✗ [{r['id']}] {r['label']} ({status}) — ни одной позиции не найдено")
            continue
        sample = ", ".join(it["variantName"] for it in items[:3])
        print(
            f"  ✓ [{r['id']}] {r['label']} ({status}): {len(items)} шт., изменится {changed} → {r['priceRub']} ₽. "
            f"Например: {sample}"
        )

    if blockers_no_match:
        print(f"\nБЛОКЕР — правила без единой позиции ({len(blockers_no_match)}):")
        for rid in blockers_no_match:
            label = next(r["label"] for r in rules if r["id"] == rid)
            print(f"  · {rid}: {label}")
        print("  Проверь имя вида в МойСклад и подправь match в prices_2026-09.json.")

    if ambiguous:
        print(f"\nБЛОКЕР — позиции попали под несколько правил сразу ({len(ambiguous)}):")
        for a in ambiguous[:20]:
            print(f"  · {a['variantName']} → {', '.join(a['rules'])}")
        if len(ambiguous) > 20:
            print(f"  …и ещё {len(ambiguous) - 20}. Полный список — в отчёте.")
        print("  Эти позиции --apply не тронет, пока правила не станут однозначными.")

    if no_sale_price:
        print(f"\nПозиций без единой цены в МойСклад: {len(no_sale_price)} — им выставится цена как новая, без сверки со старой.")

    print(
        "\nПосмотри отчёт и, если всё верно, скажи «да» — тогда: "
        f"python3 main.py --apply --scan-file {report_path}"
    )


def _latest_scan_file() -> Optional[Path]:
    if not STATE_DIR.exists():
        return None
    files = sorted(STATE_DIR.glob("scan_*.json"))
    return files[-1] if files else None


def cmd_apply(args: argparse.Namespace) -> None:
    scan_path = Path(args.scan_file) if args.scan_file else _latest_scan_file()
    if not scan_path or not scan_path.exists():
        print("Нет файла отчёта --scan. Сначала запусти: python3 main.py --scan", file=sys.stderr)
        sys.exit(1)

    report = json.loads(scan_path.read_text(encoding="utf-8"))
    matched: Dict[str, List[Dict[str, Any]]] = report["matched"]

    to_apply: List[Dict[str, Any]] = []
    skipped_no_price_type = 0
    for rule_id, items in matched.items():
        for it in items:
            if not it["changed"]:
                continue
            sale_prices = it.get("salePrices") or []
            target_kopecks = round(it["targetRub"] * 100)
            if sale_prices:
                new_sale_prices = [dict(sale_prices[0]), *sale_prices[1:]]
                new_sale_prices[0]["value"] = target_kopecks
            else:
                skipped_no_price_type += 1
                continue
            to_apply.append(
                {
                    "id": it["variantId"],
                    "name": it["variantName"],
                    "salePrices": new_sale_prices,
                    "fromRub": it["currentRub"],
                    "toRub": it["targetRub"],
                }
            )

    if not to_apply:
        print("Нечего применять — отчёт не содержит изменений цены.")
        return

    print(f"Отчёт: {scan_path}")
    print(f"К изменению: {len(to_apply)} модификаций.")
    if skipped_no_price_type:
        print(
            f"Пропущено без применения (нет ни одной цены в МойСклад, ставить некуда): "
            f"{skipped_no_price_type} — см. noSalePrice в отчёте, выставь цену вручную один раз."
        )
    for it in to_apply[:10]:
        print(f"  {it['name']}: {it['fromRub']} → {it['toRub']} ₽")
    if len(to_apply) > 10:
        print(f"  …и ещё {len(to_apply) - 10}")

    if not args.yes:
        answer = input('\nВведи "да" чтобы применить это в МойСклад: ').strip().lower()
        if answer not in CONFIRM_WORDS:
            print("Не «да» — ничего не применяю.")
            return

    ms = MoySklad()
    print("Применяю…", flush=True)
    ok, errors = ms.batch_update_sale_prices(
        [{"id": it["id"], "salePrices": it["salePrices"]} for it in to_apply]
    )

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    log_path = STATE_DIR / f"apply_{_ts()}.json"
    log_path.write_text(
        json.dumps(
            {
                "generatedAt": datetime.now(timezone.utc).isoformat(),
                "scanFile": str(scan_path),
                "requested": len(to_apply),
                "ok": ok,
                "errors": errors,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nПрименено: {ok}/{len(to_apply)}. Лог: {log_path}")
    if errors:
        print(f"Ошибки ({len(errors)}):")
        for e in errors[:20]:
            print(f"  · {e}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--scan", action="store_true", help="Отчёт: что найдётся и что изменится")
    group.add_argument("--apply", action="store_true", help="Применить прошлый --scan в МойСклад")
    parser.add_argument("--scan-file", help="Конкретный файл отчёта для --apply (по умолчанию — последний)")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Не спрашивать подтверждение в терминале (использовать только если «да» уже получено и логируется отдельно)",
    )
    args = parser.parse_args()

    if args.scan:
        cmd_scan(args)
    else:
        cmd_apply(args)


if __name__ == "__main__":
    main()
