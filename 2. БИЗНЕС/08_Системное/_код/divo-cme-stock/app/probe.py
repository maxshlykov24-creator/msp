"""Probe API без записи в таблицу: дилеры, ключи JSON, публикация, комплектация."""
from __future__ import annotations

import json
import logging
import sys
from collections import Counter

from app.config import settings
from app.cme_client import CmeAuthError, CmeClient
from app.filter_cars import detect_publish_field, filter_stock
from app.map_row import COMPLECTATION_KEYS, complectation_of, map_row, vin_of

log = logging.getLogger("probe")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        client = CmeClient()
    except CmeAuthError as exc:
        print(exc, file=sys.stderr)
        return 2

    dealers = client.dealers()
    print(f"dealers: {len(dealers)}")
    for d in dealers[:10]:
        ident = {k: d.get(k) for k in ("id", "dealerId", "name", "title") if k in d}
        print(" ", ident or list(d.keys())[:12])

    items, payload = client.cars_page({"page": 1, "perPage": 5})
    payload_type = type(payload).__name__
    top_keys = list(payload.keys()) if isinstance(payload, dict) else []
    print(f"cars page1: {len(items)} payload={payload_type} top_keys={top_keys}")
    if items:
        print("car keys:", sorted(items[0].keys()))
        sample = {k: items[0].get(k) for k in sorted(items[0].keys())[:40]}
        print("sample (обрезан):")
        print(json.dumps(sample, ensure_ascii=False, default=str, indent=2)[:4000])

    all_cars = client.iter_cars()
    print(f"all cars: {len(all_cars)}")
    field = detect_publish_field(all_cars, settings.cme_publish_field)
    print(f"publish field auto: {field!r} (explicit={settings.cme_publish_field!r})")

    if all_cars:
        key_union: Counter[str] = Counter()
        for c in all_cars[:50]:
            key_union.update(c.keys())
        print("частые ключи:", ", ".join(k for k, _ in key_union.most_common(40)))
        found_compl = [k for k in COMPLECTATION_KEYS if any(k in c for c in all_cars[:20])]
        print("complectation keys present:", found_compl or "нет из списка — смотри sample")
        print("complectation sample:", [complectation_of(c) for c in all_cars[:10]])

    filt = filter_stock(
        all_cars,
        mode=settings.cme_publish_mode,
        publish_field=settings.cme_publish_field,
        dealer_id=settings.cme_dealer_id,
    )
    print("filter stats:", filt.stats, "field:", filt.publish_field, "reason:", filt.reason or "ok")
    print("kept:", len(filt.kept), "unique VIN:", len({vin_of(c) for c in filt.kept if vin_of(c)}))
    if filt.kept:
        print("mapped row[0]:", map_row(filt.kept[0]))
    print("Sheet1 ориентир ~55 строк. Сверка: kept vs текущий Sheet1.")
    client.close()
    return 0 if not filt.reason else 3


if __name__ == "__main__":
    raise SystemExit(main())
