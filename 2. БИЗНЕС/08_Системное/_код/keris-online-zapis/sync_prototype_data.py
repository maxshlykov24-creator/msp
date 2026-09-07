#!/usr/bin/env python3
"""Пересобирает блок `const DATA` в prototype.html из seed_data.json сервера.

Прайс живёт в одном месте (`keris-server/app/seed_data.json`), прототип —
только отображение. Раньше цены правились в двух файлах и разъезжались.

Запуск:  python3 sync_prototype_data.py [--check]
  --check — ничего не писать, только сказать, актуален ли prototype.html.

Блок мастеров не трогается: там встроенные фото (base64).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROTOTYPE = HERE / "prototype.html"
SEED = HERE.parent / "keris-server" / "app" / "seed_data.json"

DOG_CAT_START = "    dog:{\n"
DOG_CAT_END = "    masters:["
TAIL_START = "    dogSizes:[\n"
TAIL_END = "  };\n"

ADDON_ICONS = {
    "nails": "✦", "nails_file": "✧", "ears": "◠", "teeth": "◇", "pawcure": "❋",
    "paw_wax": "◐", "mats": "⌁", "mask_hydra": "◌", "mask": "◌", "ozone": "≈",
    "antistress": "❀", "perfume": "❃", "creative": "✺", "eyes": "◉", "tartar": "◈",
    "glands": "●", "wait": "◷",
    "color_short": "✹", "color_medium": "✹", "color_long": "✹",
    "color_ears": "✷", "color_tail": "✷",
}


def js(value) -> str:
    """JSON без пробелов — компактно, как в остальном файле."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def icon_for(addon_id: str) -> str:
    key = addon_id.split("_", 1)[1] if "_" in addon_id else addon_id
    return ADDON_ICONS.get(key, "✦")


def service_line(svc: dict) -> str:
    parts = [
        f'id:{js(svc["id"])}',
        f'name:{js(svc["name"])}',
        f'group:{js(svc.get("group", "base"))}',
        f'prices:{js(svc["prices"])}',
        f'durations:{js(svc.get("durations") or {})}',
        f'duration:{svc["duration"]}',
    ]
    if svc.get("recommended"):
        parts.append("recommended:true")
    parts.append(f'desc:{js(svc.get("desc", ""))}')
    if svc.get("includes"):
        parts.append(f'includes:{js(svc["includes"])}')
    return "        {" + ",".join(parts) + "}"


def addon_line(ad: dict) -> str:
    parts = [
        f'id:{js(ad["id"])}',
        f'name:{js(ad["name"])}',
        f'price:{ad["price"]}',
    ]
    if ad.get("prices"):
        parts.append(f'prices:{js(ad["prices"])}')
    parts.append(f'duration:{ad["duration"]}')
    if ad.get("durations"):
        parts.append(f'durations:{js(ad["durations"])}')
    if ad.get("price_from"):
        parts.append("from:true")
    if ad.get("group"):
        parts.append(f'group:{js(ad["group"])}')
    parts.append(f'icon:{js(icon_for(ad["id"]))}')
    return "        {" + ",".join(parts) + "}"


def build_dog_cat(seed: dict) -> str:
    out: list[str] = []
    for pet in ("dog", "cat"):
        out.append(f"    {pet}:{{")
        out.append("      services:[")
        out.append(",\n".join(service_line(s) for s in seed[pet]["services"]))
        out.append("      ],")
        out.append("      addons:[")
        out.append(",\n".join(addon_line(a) for a in seed[pet]["addons"]))
        out.append("      ]")
        out.append("    },")
    return "\n".join(out) + "\n"


def build_tail(seed: dict) -> str:
    subs = seed["subscriptions"]
    grid = subs["size_grid"]
    rules = seed["booking_rules"]
    open_min, close_min = seed["salon_open_min"], seed["salon_close_min"]
    step = seed["slot_step_min"]

    def slot_list(from_min: int, to_min: int) -> list[str]:
        return [f"{m // 60:02d}:{m % 60:02d}" for m in range(from_min, to_min, step)]

    groups = {
        "Утро": slot_list(open_min, 13 * 60),
        "День": slot_list(13 * 60, 17 * 60),
        "Вечер": slot_list(17 * 60, close_min - step),
    }
    packages = [
        {"id": p["id"], "name": p["name"], "visits": p["visits"], "months": p["months"],
         "prices": p["prices"], "bonus": p["bonus"]}
        for p in subs["packages"]
    ]
    lines = [
        "    dogSizes:[",
        ",\n".join(
            f'      {{id:{js(k)},label:{js(k)},hint:{js(h)}}}'
            for k, h in (("XS", "до 5 кг"), ("S", "5–10 кг"), ("M", "10–15 кг"),
                          ("L", "15–25 кг"), ("XL", "от 25 кг"))
        ),
        "    ],",
        "    catWeights:[",
        '      {id:"small",label:"До 5 кг",hint:"маленький"},',
        '      {id:"large",label:"От 5 кг",hint:"крупный"}',
        "    ],",
        "    subSizes:[",
        ",\n".join(
            f'      {{id:{js(k)},label:{js(k)},hint:{js(grid[k])}}}'
            for k in ("XS", "S", "M", "L", "XL")
        ),
        "    ],",
        "    subscriptions:[",
        ",\n".join("      " + js(p) for p in packages),
        "    ],",
        f'    subscriptionIncluded:{js(subs.get("included_addons", []))},',
        f'    coefficients:{js(subs.get("coefficients", {}))},',
        f"    salonOpenMin:{open_min},",
        f"    salonCloseMin:{close_min},",
        f'    rules:{js({"minLeadHours": rules["min_lead_hours"], "horizonDays": rules["horizon_days"], "freeRescheduleHours": rules["free_reschedule_hours"], "lateGraceMin": rules["late_grace_min"]})},',
        f"    slotGroups:{js(groups)}",
    ]
    return "\n".join(lines) + "\n"


def splice(text: str, start_anchor: str, end_anchor: str, replacement: str) -> str:
    start = text.index(start_anchor)
    end = text.index(end_anchor, start)
    return text[:start] + replacement + text[end:]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    seed = json.loads(SEED.read_text(encoding="utf-8"))
    html = PROTOTYPE.read_text(encoding="utf-8")
    updated = splice(html, DOG_CAT_START, DOG_CAT_END, build_dog_cat(seed))
    updated = splice(updated, TAIL_START, TAIL_END, build_tail(seed))

    if args.check:
        if updated == html:
            print("prototype.html актуален")
            return 0
        print("prototype.html расходится с seed_data.json — запустите без --check")
        return 1

    if updated == html:
        print("prototype.html уже актуален")
        return 0
    PROTOTYPE.write_text(updated, encoding="utf-8")
    print(f"prototype.html обновлён из {SEED.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
