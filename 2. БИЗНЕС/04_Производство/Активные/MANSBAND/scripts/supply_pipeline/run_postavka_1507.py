#!/usr/bin/env python3
"""Пайплайн Поставка 15.07 → Прогон / Блокеры / (опционально) МС."""
from __future__ import annotations

import argparse
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import (  # noqa: E402
    MS_API,
    RUN_DESCRIPTION,
    RUN_EXTERNAL_CODE,
    RUN_MOMENT,
    SHEET_BLOCK,
    SHEET_FLAT,
    SHEET_NOMEN,
    SHEET_RUN,
    SHEET_SPR,
)
from expand import aggregate, expand_flat, load_parent_uuids, qty_control  # noqa: E402
from nomen_index import build_index, lookup  # noqa: E402
from sheets_io import ensure_sheet, get_values, sheets_service, write_values  # noqa: E402
from sostav import format_sostav_description  # noqa: E402
from variation import apply_variation, load_abbr_maps  # noqa: E402


RUN_HEADER = [
    "src_num",
    "src_kit",
    "vid",
    "variation",
    "size",
    "rost",
    "qty",
    "status",
    "reason",
    "uuid",
    "code",
    "brand",
    "model",
    "art",
    "color",
    "uzor",
    "kroy",
    "sostav",
    "parent_uuid",
    "nomen_color",
    "nomen_uzor",
]


def _norm_attr(v: str) -> str:
    return (v or "").replace("\xa0", " ").strip().lower()


def classify(positions, nomen_idx):
    """
    Антиключ совпал:
      - цвет/узор совпали → exists
      - цвет и/или узор отличаются → update_attrs (править в МС, не создавать новую)
    Антиключ не найден → create
    """
    for p in positions:
        hits = lookup(nomen_idx, p.vid, p.variation, p.size, p.rost)
        if len(hits) > 1 and p.color:
            color_hits = [
                h
                for h in hits
                if _norm_attr(h.color) == _norm_attr(p.color)
            ]
            if len(color_hits) == 1:
                hits = color_hits
        if len(hits) > 1:
            p.status = "blocked"
            p.reason = "blocked_duplicate_in_nomen"
            continue
        if len(hits) == 1:
            hit = hits[0]
            p.uuid = hit.uuid
            p.code = hit.code
            p.nomen_color = hit.color
            p.nomen_uzor = hit.uzor
            diffs = []
            if p.color and hit.color and _norm_attr(hit.color) != _norm_attr(p.color):
                diffs.append(f"color:{hit.color!r}->{p.color!r}")
            if p.uzor and hit.uzor and _norm_attr(hit.uzor) != _norm_attr(p.uzor):
                diffs.append(f"uzor:{hit.uzor!r}->{p.uzor!r}")
            if p.color and not hit.color:
                diffs.append(f"color:''->{p.color!r}")
            if p.uzor and not hit.uzor:
                diffs.append(f"uzor:''->{p.uzor!r}")
            if diffs:
                p.status = "update_attrs"
                p.reason = ";".join(diffs)
            else:
                p.status = "exists"
                p.reason = ""
            continue
        p.status = "create"
        p.reason = "not_in_nomen"


def write_run_sheets(svc, positions, blockers, control: Dict[str, Any]):
    run_id = ensure_sheet(svc, SHEET_RUN, rows=max(len(positions) + 50, 100), cols=24)
    rows = [RUN_HEADER]
    for p in positions:
        rows.append(
            [
                p.src_num,
                p.src_kit,
                p.vid,
                p.variation,
                p.size,
                p.rost,
                p.qty,
                p.status,
                p.reason,
                p.uuid,
                p.code,
                p.brand,
                p.model,
                p.art,
                p.color,
                p.uzor,
                p.kroy,
                p.sostav,
                p.parent_uuid,
                p.nomen_color,
                p.nomen_uzor,
            ]
        )
    write_values(svc, SHEET_RUN, rows, sheet_id=run_id)

    block_id = ensure_sheet(svc, SHEET_BLOCK, rows=max(len(blockers) + 50, 100), cols=16)
    bheader = [
        "reason",
        "num",
        "kit",
        "vid",
        "brand",
        "model",
        "art",
        "size",
        "rost",
        "detail",
        "row",
    ]
    brows = [bheader]
    for b in blockers:
        brows.append([b.get(h, "") for h in bheader])
    brows.append([])
    brows.append(["qty_control", "", "", "", "", "", "", "", "", str(control)])
    write_values(svc, SHEET_BLOCK, brows, sheet_id=block_id)


def build_positions(
    *,
    flat_rows: Optional[List[List[Any]]] = None,
    skip_nomen: bool = False,
):
    """Общий прогон: flat → expand → variation → classify по выгрузке Номенкларутра."""
    from sheets_io import get_values_retry

    svc = sheets_service()
    spr = get_values_retry(svc, SHEET_SPR, "A:R")
    if flat_rows is not None:
        flat = flat_rows
    else:
        flat = get_values_retry(svc, SHEET_FLAT, "A:M")  # M = note
    nomen: List[List[Any]] = []
    if not skip_nomen:
        try:
            nomen = get_values_retry(svc, SHEET_NOMEN, "A:BH")
        except Exception as e:
            print(f"WARN nomen sheet skip: {e}", flush=True)
            nomen = []

    parents = load_parent_uuids(spr)
    brand_map, model_map = load_abbr_maps(spr)
    positions, blockers = expand_flat(flat, parents)
    ready, var_blockers = apply_variation(positions, brand_map, model_map)
    blockers.extend(var_blockers)
    ready = aggregate(ready)
    nomen_idx = build_index(nomen) if nomen else {}
    classify(ready, nomen_idx)
    control = qty_control(flat, ready)
    return svc, ready, blockers, control


def dry_run():
    svc, ready, blockers, control = build_positions()
    write_run_sheets(svc, ready, blockers, control)

    st = Counter(p.status for p in ready)
    br = Counter(b["reason"] for b in blockers)
    print("=== DRY-RUN OK ===")
    print(f"positions={len(ready)} blockers={len(blockers)}")
    print("status:", dict(st))
    print("blocker reasons:", dict(br))
    print("qty_control:", control)
    print(f"sheets: '{SHEET_RUN}', '{SHEET_BLOCK}'")
    miss_models = sorted(
        {
            b.get("model", "")
            for b in blockers
            if b.get("reason") == "missing_model_abbr" and b.get("model")
        }
    )
    if miss_models:
        print("NEED model abbr:", miss_models)


def _variant_chars(p) -> Dict[str, str]:
    """Все характеристики целиком — PUT не должен затирать остальные."""
    chars = {
        "Рзамер": p.size,
        "Ростовка": p.rost,
        "Вариация": p.variation,
        "Цвет": p.color,
        "Узорность": p.uzor,
        "Крой": p.kroy,
    }
    return {k: v for k, v in chars.items() if v}


def _chars_from_variant(v: Dict[str, Any]) -> Dict[str, str]:
    return {
        c.get("name"): str(c.get("value", ""))
        for c in v.get("characteristics", [])
    }


def _live_reclass(ms, p, parent_uuid: str, index=None) -> None:
    """Переклассификация по live API (истина перед записью)."""
    hits = ms.find_variant_by_key(
        parent_uuid, p.variation, p.size, p.rost, index=index
    )
    if len(hits) > 1 and p.color:
        color_hits = []
        for v in hits:
            chars = _chars_from_variant(v)
            if _norm_attr(chars.get("Цвет", "")) == _norm_attr(p.color):
                color_hits.append(v)
        if len(color_hits) == 1:
            hits = color_hits
    if len(hits) > 1:
        p.status = "blocked"
        p.reason = "blocked_duplicate_in_ms_live"
        return
    if len(hits) == 1:
        v = hits[0]
        chars = _chars_from_variant(v)
        p.uuid = v["id"]
        p.code = v.get("code") or ""
        p.nomen_color = chars.get("Цвет", "")
        p.nomen_uzor = chars.get("Узорность", "")
        diffs = []
        if p.color and chars.get("Цвет") and _norm_attr(chars.get("Цвет", "")) != _norm_attr(p.color):
            diffs.append(f"color:{chars.get('Цвет')!r}->{p.color!r}")
        if p.uzor and chars.get("Узорность") and _norm_attr(chars.get("Узорность", "")) != _norm_attr(p.uzor):
            diffs.append(f"uzor:{chars.get('Узорность')!r}->{p.uzor!r}")
        if p.color and not chars.get("Цвет"):
            diffs.append(f"color:''->{p.color!r}")
        if p.uzor and not chars.get("Узорность"):
            diffs.append(f"uzor:''->{p.uzor!r}")
        want_desc = format_sostav_description(p.sostav)
        have_desc = (v.get("description") or "").strip()
        if want_desc and have_desc != want_desc:
            diffs.append("description_format")
        if diffs:
            p.status = "update_attrs"
            p.reason = ";".join(diffs)
        else:
            p.status = "exists"
            p.reason = "already_in_ms_live"
        return
    p.status = "create"
    p.reason = "not_in_ms_live"
    p.uuid = ""
    p.code = ""


def _enter_position(variant_uuid: str, qty: int) -> Dict[str, Any]:
    return {
        "quantity": float(qty),
        "price": 0,
        "assortment": {
            "meta": {
                "href": f"{MS_API}/entity/variant/{variant_uuid}",
                "type": "variant",
                "mediaType": "application/json",
            }
        },
    }


def apply_to_ms(
    *,
    variants_only: bool,
    limit: int = 0,
    flat_rows: Optional[List[List[Any]]] = None,
):
    """
    Live-проверка → create / update_attrs → (опц.) одно оприходование.
    variants_only=True: без enter; limit>0 ограничивает только create.
    flat_rows — локальный flat (если Google Sheets недоступен).
    """
    from ms_api import MoySklad

    svc, ready, blockers, control = build_positions(
        flat_rows=flat_rows, skip_nomen=True
    )
    if blockers:
        print(f"ABORT: blockers={len(blockers)} — сначала закрой Блокеры")
        write_run_sheets(svc, ready, blockers, control)
        sys.exit(2)
    if not control.get("ok"):
        print(f"ABORT: qty_control failed: {control}")
        write_run_sheets(svc, ready, blockers, control)
        sys.exit(2)

    ms = MoySklad()
    parent_cache: Dict[str, str] = {}
    next_code = ms.next_cristal_code()
    print(f"next code start: {next_code}")

    # 1) родители из Справочника (без лишних GET — сеть нестабильна)
    spr_by_vid = {p.vid: p.parent_uuid for p in ready}
    for vid in sorted(spr_by_vid):
        uid = spr_by_vid[vid]
        if not uid:
            raise RuntimeError(f"нет parent_uuid в справочнике для {vid!r}")
        parent_cache[vid] = uid
        print(f"parent {vid!r} → {uid}", flush=True)

    # 2) live reclass: 1 запрос на (parent, variation), не на каждую позицию
    from collections import defaultdict

    groups: Dict[tuple, List[Any]] = defaultdict(list)
    for p in ready:
        p.parent_uuid = parent_cache[p.vid]
        groups[(p.parent_uuid, p.variation)].append(p)

    print(
        f"live reclass positions={len(ready)} groups={len(groups)}...",
        flush=True,
    )
    for gi, ((parent_uuid, variation), plist) in enumerate(groups.items(), 1):
        rows = []
        try:
            data = ms.get(
                "/entity/variant",
                filter=f"productid={parent_uuid};code={variation}",
                limit=100,
                expand="characteristics",
                timeout=90,
                retries=8,
            )
            rows = data.get("rows") or []
        except Exception as e:
            print(f"  WARN group {variation}: {e} — treat as create", flush=True)
        by_key: Dict[tuple, List[Any]] = defaultdict(list)
        for v in rows:
            chars = _chars_from_variant(v)
            sz = chars.get("Рзамер") or chars.get("Размер") or ""
            rost = chars.get("Ростовка") or ""
            by_key[(sz, rost)].append(v)
        for p in plist:
            hits = by_key.get((p.size, p.rost or ""), [])
            if len(hits) > 1 and p.color:
                color_hits = []
                for v in hits:
                    chars = _chars_from_variant(v)
                    if _norm_attr(chars.get("Цвет", "")) == _norm_attr(p.color):
                        color_hits.append(v)
                if len(color_hits) == 1:
                    hits = color_hits
            if len(hits) > 1:
                p.status = "blocked"
                p.reason = "blocked_duplicate_in_ms_live"
            elif len(hits) == 1:
                v = hits[0]
                chars = _chars_from_variant(v)
                p.uuid = v["id"]
                p.code = v.get("code") or ""
                p.nomen_color = chars.get("Цвет", "")
                p.nomen_uzor = chars.get("Узорность", "")
                diffs = []
                if (
                    p.color
                    and chars.get("Цвет")
                    and _norm_attr(chars.get("Цвет", "")) != _norm_attr(p.color)
                ):
                    diffs.append(f"color:{chars.get('Цвет')!r}->{p.color!r}")
                if (
                    p.uzor
                    and chars.get("Узорность")
                    and _norm_attr(chars.get("Узорность", "")) != _norm_attr(p.uzor)
                ):
                    diffs.append(f"uzor:{chars.get('Узорность')!r}->{p.uzor!r}")
                if p.color and not chars.get("Цвет"):
                    diffs.append(f"color:''->{p.color!r}")
                if p.uzor and not chars.get("Узорность"):
                    diffs.append(f"uzor:''->{p.uzor!r}")
                want_desc = format_sostav_description(p.sostav)
                have_desc = (v.get("description") or "").strip()
                if want_desc and have_desc != want_desc:
                    diffs.append("description_format")
                if diffs:
                    p.status = "update_attrs"
                    p.reason = ";".join(diffs)
                else:
                    p.status = "exists"
                    p.reason = "already_in_ms_live"
            else:
                p.status = "create"
                p.reason = "not_in_ms_live"
                p.uuid = ""
                p.code = ""
        print(
            f"  group {gi}/{len(groups)} {variation}: rows={len(rows)} "
            f"pos={len(plist)}",
            flush=True,
        )
        time.sleep(0.25)

    blocked_live = [p for p in ready if p.status == "blocked"]
    if blocked_live:
        print(f"ABORT: live blockers={len(blocked_live)}")
        for p in blocked_live[:10]:
            print(" ", p.vid, p.variation, p.size, p.rost, p.reason)
        write_run_sheets(svc, ready, blockers, control)
        sys.exit(2)

    created = 0
    updated = 0
    create_budget = limit if (variants_only and limit > 0) else 10**9
    # CREATE требует уникальный code → временный CristalNNNN, потом batch code=вариация
    pending_code_to_variation: List[tuple] = []

    # 2) creates
    for p in ready:
        if p.status != "create":
            continue
        if created >= create_budget:
            break
        code = next_code
        chars = _variant_chars(p)
        desc = format_sostav_description(p.sostav)
        print(
            f"CREATE {code}: {p.vid} | {p.variation} | {p.size}/{p.rost} | "
            f"{p.color}/{p.uzor}"
        )
        created_v = ms.create_variant(
            parent_uuid=p.parent_uuid,
            code=code,
            characteristics=chars,
            description=desc,
        )
        p.uuid = created_v["id"]
        p.code = p.variation  # целевой код; в МС поставим batch-ом ниже
        p.status = "created"
        p.reason = "api_create"
        pending_code_to_variation.append((p.uuid, p.variation))
        created += 1
        next_code = ms.next_cristal_code()
        time.sleep(0.12)  # чуть медленнее — меньше SSL/обрывов API

    # добить code=вариация и для уже найденных (resume после обрыва create)
    seen_ids = {uid for uid, _ in pending_code_to_variation}
    for p in ready:
        if not p.uuid or not p.variation or p.uuid in seen_ids:
            continue
        cur = (p.code or "").strip()
        if cur != p.variation:
            pending_code_to_variation.append((p.uuid, p.variation))
            seen_ids.add(p.uuid)

    if pending_code_to_variation:
        print(
            f"BATCH code→variation n={len(pending_code_to_variation)} ...",
            flush=True,
        )
        ok_c, err_c = ms.batch_update_variant_codes(pending_code_to_variation)
        print(f"BATCH code→variation ok={ok_c} errors={len(err_c)}")
        for e in err_c[:10]:
            print(" ", e)

    # 3) update_attrs (все хар-ки + состав построчно)
    for p in ready:
        if p.status != "update_attrs":
            continue
        if not p.uuid:
            print(f"SKIP update без uuid: {p.vid} {p.variation} {p.size}/{p.rost}")
            continue
        chars = _variant_chars(p)
        desc = format_sostav_description(p.sostav)
        print(f"UPDATE {p.code or p.uuid}: {p.reason}")
        ms.update_variant_attrs(p.uuid, chars, description=desc)
        p.status = "updated"
        p.reason = (p.reason + ";api_update") if p.reason else "api_update"
        updated += 1
        time.sleep(0.05)

    enter_id = ""
    enter_name = ""
    if not variants_only:
        # все позиции с uuid и qty
        missing = [p for p in ready if not p.uuid]
        if missing:
            print(f"ABORT: нет uuid у {len(missing)} позиций — enter не создаём")
            write_run_sheets(svc, ready, blockers, control)
            sys.exit(2)
        org, store = ms.resolve_org_store()
        positions = [_enter_position(p.uuid, p.qty) for p in ready]
        print(
            f"ENTER positions={len(positions)} qty={sum(p.qty for p in ready)} "
            f"moment={RUN_MOMENT} externalCode={RUN_EXTERNAL_CODE}"
        )
        enter = ms.create_enter(
            org=org,
            store=store,
            positions=positions,
            external_code=RUN_EXTERNAL_CODE,
            description=RUN_DESCRIPTION,
            moment=RUN_MOMENT,
        )
        enter_id = enter.get("id", "")
        enter_name = enter.get("name", "")
        print(f"ENTER OK id={enter_id} name={enter_name}")

    write_run_sheets(svc, ready, blockers, control)
    st = Counter(p.status for p in ready)
    print("=== APPLY OK ===")
    print(f"created={created} updated={updated} status={dict(st)}")
    print("qty_control:", control)
    if variants_only:
        print("enter: skipped (--apply-variants)")
    else:
        print(f"enter: {enter_name or enter_id or 'n/a'}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply-variants", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    if args.apply:
        apply_to_ms(variants_only=False, limit=0)
        return
    if args.apply_variants:
        apply_to_ms(variants_only=True, limit=args.limit or 1)
        return
    dry_run()


if __name__ == "__main__":
    main()
