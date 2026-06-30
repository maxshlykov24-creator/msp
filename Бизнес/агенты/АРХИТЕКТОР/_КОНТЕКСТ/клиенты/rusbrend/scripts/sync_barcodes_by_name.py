"""
sync_barcodes_by_name.py — синхронизация штрихкодов RUSbrend → МойСклад.

Команды:
  preflight   — проверка txt (dup barcode/name)
  reconcile   — dry-run → CSV
  apply       — запись FIX_ARTICLE_BC, затем ADD_BARCODE
  verify      — сверка txt vs MS

  MS_TOKEN=… python3 sync_barcodes_by_name.py reconcile --input ../артикулы_штрихкоды_2026-06-29.txt
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

# ── Config ────────────────────────────────────────────────────────────────────
DEFAULT_TOKEN = "951f3b09451c02691686ac8077cc59d474685a04"
BASE = "https://api.moysklad.ru/api/remap/1.2"
DELAY = 0.35
ATTR_ORG = "e881075b-4f9d-11f1-0a80-156000141799"

SESS = requests.Session()

REPORT_FIELDS = [
    "txt_article", "txt_barcode", "txt_name",
    "ms_id", "ms_article", "ms_name", "ms_barcode",
    "match_method", "action", "reason",
]


def get_token() -> str:
    return os.environ.get("MS_TOKEN", DEFAULT_TOKEN)


def setup_session():
    SESS.headers.update({
        "Authorization": f"Bearer {get_token()}",
        "Content-Type": "application/json",
        "Accept-Encoding": "gzip",
    })


def req(method: str, path: str, **kw) -> requests.Response:
    kw.setdefault("timeout", 60)
    last = None
    for attempt in range(1, 13):
        try:
            r = SESS.request(method, BASE + path, **kw)
            return r
        except Exception as e:
            last = e
            time.sleep(min(2 * attempt, 12))
    raise RuntimeError(f"сеть не отвечает: {last}")


# ── Parsing (from load_products.py) ───────────────────────────────────────────

def clean(v: Any) -> str:
    return str(v).strip() if v else ""


def parse_barcode(raw: str) -> tuple[str | None, str | None]:
    v = clean(raw)
    if not v:
        return None, None
    if v.upper().startswith("OZN"):
        return "code128", v
    try:
        numeric = int(float(v))
        s = str(numeric)
        if len(s) in (8, 12, 13, 14):
            return "ean13", s
        return "code128", s
    except Exception:
        return "code128", v


def norm_name(s: str) -> str:
    s = s.lower().strip()
    s = s.replace("ё", "е")
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"\s*/\s*", " / ", s)
    for ch in "«»\"'":
        s = s.replace(ch, "")
    return s


def article_variants(art: str) -> set[str]:
    art = clean(art)
    if not art:
        return set()
    out = {art}
    stripped = art.lstrip("0") or "0"
    out.add(stripped)

    if art.startswith("1") and len(art) >= 4 and art[1:].isdigit():
        tail = art[1:]
        short = tail.lstrip("0") or "0"
        out.add(tail)
        out.add(short)
        for width in (3, 4, 5):
            out.add(tail.zfill(width))
            out.add(short.zfill(width))

    if art.isdigit() and len(art) <= 3:
        out.add("1" + art)
        out.add("10" + art.zfill(3))
        for width in (3, 4, 5):
            out.add(art.zfill(width))

    return {x for x in out if x}


def is_renumber_pair(txt_art: str, ms_art: str) -> bool:
    t = txt_art.lstrip("0") or "0"
    m = ms_art.lstrip("0") or "0"
    if txt_art.startswith("1") and t == m:
        return True
    if txt_art.startswith("10") and t == m:
        return True
    return False


def barcode_to_str(bc: dict) -> str:
    if not bc:
        return ""
    if bc.get("ean13"):
        return str(bc["ean13"])
    if bc.get("code128"):
        return str(bc["code128"])
    if bc.get("gtin"):
        return str(bc["gtin"])
    return str(bc)


def extract_barcodes(product: dict) -> list[str]:
    return [barcode_to_str(b) for b in (product.get("barcodes") or []) if barcode_to_str(b)]


def get_org_name(product: dict) -> str:
    for attr in product.get("attributes") or []:
        if attr.get("id") == ATTR_ORG:
            val = attr.get("value") or {}
            if isinstance(val, dict):
                return (val.get("name") or "").strip()
            return clean(val)
    pname = (product.get("name") or "").lower()
    if "gripon" in pname or "грипон" in pname:
        return "GripOn"
    return "RUSbrend"


@dataclass
class TxtRow:
    article: str
    barcode: str
    name: str
    bc_type: str | None = None
    bc_value: str | None = None

    def __post_init__(self):
        self.bc_type, self.bc_value = parse_barcode(self.barcode)


@dataclass
class MsProduct:
    id: str
    article: str
    name: str
    barcodes: list[str] = field(default_factory=list)
    org: str = ""
    norm: str = ""

    @classmethod
    def from_api(cls, p: dict) -> "MsProduct":
        art = clean(p.get("article"))
        name = clean(p.get("name"))
        return cls(
            id=p["id"],
            article=art,
            name=name,
            barcodes=extract_barcodes(p),
            org=get_org_name(p),
            norm=norm_name(name),
        )


def load_txt(path: Path) -> list[TxtRow]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        parts = line.split(" - ", 2)
        if len(parts) < 3:
            continue
        art, bc, name = parts[0].strip(), parts[1].strip(), parts[2].strip()
        if not art or not bc:
            continue
        rows.append(TxtRow(article=art, barcode=bc, name=name))
    return rows


def fetch_all_products() -> list[MsProduct]:
    out: list[MsProduct] = []
    offset = 0
    while True:
        r = req("GET", f"/entity/product?limit=1000&offset={offset}")
        r.raise_for_status()
        batch = r.json().get("rows", [])
        for p in batch:
            art = clean(p.get("article"))
            if art:
                out.append(MsProduct.from_api(p))
        if len(batch) < 1000:
            break
        offset += 1000
        time.sleep(0.1)
    return out


def build_indexes(products: list[MsProduct]):
    by_article: dict[str, list[MsProduct]] = defaultdict(list)
    by_norm: dict[str, list[MsProduct]] = defaultdict(list)
    by_bc: dict[str, str] = {}  # bc -> product id
    for p in products:
        by_article[p.article].append(p)
        by_norm[p.norm].append(p)
        for bc in p.barcodes:
            if bc and bc not in by_bc:
                by_bc[bc] = p.id
    return by_article, by_norm, by_bc


def names_match(txt: TxtRow, ms: MsProduct) -> bool:
    a, b = norm_name(txt.name), ms.norm
    if a == b:
        return True
    # MS часто без хвоста (118: «1,5М», 10320: «2 штуки в комплекте»)
    if len(a) >= 8 and len(b) >= 8 and (a.startswith(b) or b.startswith(a)):
        return True
    return False


def pick_org_tiebreaker(candidates: list[MsProduct], txt: TxtRow) -> MsProduct | None:
    """При нескольких кандидатах — предпочесть RUSbrend для не-GripOn имён."""
    if len(candidates) == 1:
        return candidates[0]
    txt_lower = txt.name.lower()
    prefer = "GripOn" if ("gripon" in txt_lower or "грипон" in txt_lower) else "RUSbrend"
    for c in candidates:
        if c.org == prefer:
            return c
    return candidates[0]


def match_product(
    txt: TxtRow,
    by_article: dict[str, list[MsProduct]],
    by_norm: dict[str, list[MsProduct]],
) -> tuple[MsProduct | None, str, str]:
    """Каскадное сопоставление. Возвращает (product, method, reason)."""
    nn = norm_name(txt.name)

    # Step 1: exact article
    exact = by_article.get(txt.article, [])
    if exact:
        p = exact[0]
        if len(exact) > 1:
            return None, "", f"AMBIGUOUS: {len(exact)} MS с артикулом {txt.article}"
        if names_match(txt, p):
            return p, "by_article", ""
        return None, "", "REVIEW_ARTICLE_NAME_MISMATCH"

    # Step 2: article variants
    for var in article_variants(txt.article):
        if var == txt.article:
            continue
        cands = by_article.get(var, [])
        if not cands:
            continue
        if len(cands) > 1:
            matched = [c for c in cands if names_match(txt, c)]
            if len(matched) == 1:
                return matched[0], "by_article_variant", f"variant {var}"
            continue
        p = cands[0]
        if names_match(txt, p):
            return p, "by_article_variant", f"variant {var}"

    # Step 3: unique norm name
    name_cands = by_norm.get(nn, [])
    if len(name_cands) == 1:
        return name_cands[0], "by_name_unique", ""

    # Step 5 (before step 4 per plan flow): strict renumber among name matches
    renumber_cands = [c for c in name_cands if is_renumber_pair(txt.article, c.article)]
    if len(renumber_cands) == 1 and names_match(txt, renumber_cands[0]):
        return renumber_cands[0], "by_renumber", ""

    # Step 4: several with same name — prefer article variant match
    if name_cands:
        variants = article_variants(txt.article)
        var_match = [c for c in name_cands if c.article in variants and names_match(txt, c)]
        if len(var_match) == 1:
            return var_match[0], "by_name_and_article", f"variant in {variants}"

        art_match = [c for c in name_cands if c.article == txt.article]
        if len(art_match) == 1:
            return art_match[0], "by_name_and_article", ""

        if len(renumber_cands) == 1:
            return renumber_cands[0], "by_renumber", ""

        if len(name_cands) > 1:
            return None, "", "AMBIGUOUS_NAME"

    # Step 5 fallback: renumber in full catalog
    all_renumber = []
    for p in by_article.values():
        for c in p:
            if is_renumber_pair(txt.article, c.article) and names_match(txt, c):
                all_renumber.append(c)
    if len(all_renumber) == 1:
        return all_renumber[0], "by_renumber", ""

    if not name_cands:
        return None, "", "ERROR_NO_MATCH"
    return None, "", "AMBIGUOUS_NAME"


def decide_action(
    txt: TxtRow,
    ms: MsProduct | None,
    by_article: dict[str, list[MsProduct]],
    by_bc: dict[str, str],
    force_barcode: bool = False,
) -> tuple[str, str]:
    if ms is None:
        return "ERROR_NO_MATCH", "no MS product"

    ms_bc = ms.barcodes[0] if ms.barcodes else ""
    txt_bc_norm = txt.bc_value or txt.barcode

    if len(ms.barcodes) > 1:
        return "REVIEW_MULTI_BC", f"{len(ms.barcodes)} barcodes in MS"

    # article differs → FIX
    if ms.article != txt.article:
        # pre-checks for FIX
        taken = by_article.get(txt.article, [])
        if taken and any(p.id != ms.id for p in taken):
            return "ERROR_ARTICLE_TAKEN", f"article {txt.article} on other id"
        if txt_bc_norm in by_bc and by_bc[txt_bc_norm] != ms.id:
            return "ERROR_BARCODE_TAKEN", f"barcode on {by_bc[txt_bc_norm]}"

        if ms_bc and ms_bc != txt_bc_norm:
            if not force_barcode:
                return "REVIEW_BC_MISMATCH", f"MS={ms_bc} txt={txt_bc_norm}"
        return "FIX_ARTICLE_BC", f"{ms.article} → {txt.article}"

    # article OK
    if not ms_bc:
        if txt_bc_norm in by_bc and by_bc[txt_bc_norm] != ms.id:
            return "ERROR_BARCODE_TAKEN", f"barcode on {by_bc[txt_bc_norm]}"
        return "ADD_BARCODE", ""

    if ms_bc == txt_bc_norm:
        return "SKIP_ALREADY_OK", ""

    if not force_barcode:
        return "REVIEW_BC_MISMATCH", f"MS={ms_bc} txt={txt_bc_norm}"
    if txt_bc_norm in by_bc and by_bc[txt_bc_norm] != ms.id:
        return "ERROR_BARCODE_TAKEN", f"barcode on {by_bc[txt_bc_norm]}"
    return "ADD_BARCODE", "force replace"


def preflight_txt(rows: list[TxtRow]) -> int:
    bc_map: dict[str, list[str]] = defaultdict(list)
    name_map: dict[str, list[str]] = defaultdict(list)
    errors = 0

    for r in rows:
        bc_map[r.barcode].append(r.article)
        name_map[norm_name(r.name)].append(r.article)

    print(f"Строк: {len(rows)}")
    for bc, arts in bc_map.items():
        if len(arts) > 1:
            print(f"ERROR_DUP_TXT_BARCODE: {bc} → арт. {arts}")
            errors += 1

    for nn, arts in name_map.items():
        if len(arts) > 1 and len(set(arts)) > 1:
            print(f"DUP NAME (OK if articles differ): {arts} → {nn[:60]}")

    if errors:
        print(f"\nPreflight FAILED: {errors} ошибок")
        return 1
    print("\nPreflight OK")
    return 0


def reconcile_rows(
    rows: list[TxtRow],
    products: list[MsProduct],
    force_barcode: bool = False,
) -> list[dict]:
    by_article, by_norm, by_bc = build_indexes(products)
    report = []

    for txt in rows:
        ms, method, match_reason = match_product(txt, by_article, by_norm)

        if match_reason.startswith("REVIEW_ARTICLE_NAME_MISMATCH"):
            action, reason = "REVIEW_ARTICLE_NAME_MISMATCH", match_reason
            method = "by_article"
        elif match_reason.startswith("AMBIGUOUS"):
            action = "AMBIGUOUS_NAME" if "NAME" in match_reason else "ERROR_NO_MATCH"
            reason = match_reason
            method = ""
        elif match_reason.startswith("ERROR"):
            action, reason = match_reason, match_reason
            method = ""
        elif ms is None:
            action, reason = "ERROR_NO_MATCH", match_reason or "no match"
            method = ""
        else:
            action, reason = decide_action(txt, ms, by_article, by_bc, force_barcode)
            if reason and action not in ("SKIP_ALREADY_OK", "ADD_BARCODE", "FIX_ARTICLE_BC"):
                pass
            elif match_reason and action in ("FIX_ARTICLE_BC",):
                reason = match_reason or reason

        ms_bc = ms.barcodes[0] if ms and ms.barcodes else ""
        report.append({
            "txt_article": txt.article,
            "txt_barcode": txt.barcode,
            "txt_name": txt.name,
            "ms_id": ms.id if ms else "",
            "ms_article": ms.article if ms else "",
            "ms_name": ms.name if ms else "",
            "ms_barcode": ms_bc,
            "match_method": method,
            "action": action,
            "reason": reason,
        })
    return report


def write_report(rows: list[dict], path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=REPORT_FIELDS)
        w.writeheader()
        w.writerows(rows)


def read_report(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def summarize_report(rows: list[dict]):
    counts: dict[str, int] = defaultdict(int)
    for r in rows:
        counts[r["action"]] += 1
    print("\n=== Сводка ===")
    for k in sorted(counts, key=lambda x: (-counts[x], x)):
        print(f"  {k}: {counts[k]}")
    print(f"  TOTAL: {len(rows)}")


def build_barcode_payload(txt_barcode: str) -> list[dict]:
    bc_type, bc_value = parse_barcode(txt_barcode)
    if not bc_type or not bc_value:
        raise ValueError(f"invalid barcode: {txt_barcode}")
    return [{bc_type: bc_value}]


def apply_report(
    report_rows: list[dict],
    articles_filter: set[str] | None = None,
    force_barcode: bool = False,
    dry_run: bool = False,
):
    actionable = {"FIX_ARTICLE_BC", "ADD_BARCODE"}
    if force_barcode:
        actionable.add("REVIEW_BC_MISMATCH")

    rows = [r for r in report_rows if r["action"] in actionable]
    if articles_filter:
        rows = [r for r in rows if r["txt_article"] in articles_filter]

    fix_rows = [r for r in rows if r["action"] == "FIX_ARTICLE_BC"]
    add_rows = [r for r in rows if r["action"] in ("ADD_BARCODE", "REVIEW_BC_MISMATCH")]

    ok = err = 0

    def do_put(row: dict, extra: dict | None = None):
        nonlocal ok, err
        pid = row["ms_id"]
        payload = dict(extra or {})
        if not payload:
            payload["barcodes"] = build_barcode_payload(row["txt_barcode"])
        art = row["txt_article"]
        name_preview = row["txt_name"][:40]
        if dry_run:
            print(f"  [DRY] PUT {pid} {art} {name_preview} {payload}")
            ok += 1
            return
        r = req("PUT", f"/entity/product/{pid}", json=payload)
        if r.status_code in (200, 201):
            print(f"  OK {art} {name_preview}")
            ok += 1
        else:
            print(f"  ERR {art} {r.status_code} {r.text[:200]}")
            err += 1
        time.sleep(DELAY)

    print(f"\n== FIX_ARTICLE_BC: {len(fix_rows)} ==")
    for row in fix_rows:
        payload = {
            "article": row["txt_article"],
            "code": row["txt_article"],
            "barcodes": build_barcode_payload(row["txt_barcode"]),
        }
        do_put(row, payload)

    if fix_rows and add_rows:
        print("  пауза 2с после FIX…")
        time.sleep(2)

    print(f"\n== ADD_BARCODE: {len(add_rows)} ==")
    for row in add_rows:
        do_put(row)

    print(f"\nApply: {ok} OK, {err} errors")
    return err


def verify_txt(rows: list[TxtRow], products: list[MsProduct]) -> int:
    by_article = {p.article: p for p in products}
    ok = fail = 0
    fails = []

    for txt in rows:
        p = by_article.get(txt.article)
        if not p:
            fail += 1
            fails.append((txt.article, "NOT_FOUND"))
            continue
        txt_bc = txt.bc_value or txt.barcode
        ms_bcs = p.barcodes
        if txt_bc in ms_bcs:
            ok += 1
        else:
            fail += 1
            fails.append((txt.article, f"MS={ms_bcs} txt={txt_bc}"))

    print(f"\nVerify: {ok}/{len(rows)} OK, {fail} fail")
    for art, msg in fails[:20]:
        print(f"  {art}: {msg}")
    if len(fails) > 20:
        print(f"  … ещё {len(fails) - 20}")
    return fail


def cmd_preflight(args):
    rows = load_txt(Path(args.input))
    return preflight_txt(rows)


def cmd_reconcile(args):
    rows = load_txt(Path(args.input))
    if args.limit:
        rows = rows[: args.limit]
    if args.articles:
        wanted = {a.strip() for a in args.articles.split(",") if a.strip()}
        rows = [r for r in rows if r.article in wanted]

    pf = preflight_txt(rows)
    if pf != 0:
        return pf

    print("Загрузка товаров MS…")
    products = fetch_all_products()
    print(f"MS: {len(products)} товаров")

    report = reconcile_rows(rows, products, force_barcode=args.force_barcode)
    out = Path(args.out)
    write_report(report, out)
    print(f"Отчёт: {out}")
    summarize_report(report)
    return 0


def cmd_apply(args):
    report_rows = read_report(Path(args.report))
    articles_filter = None
    if args.articles:
        articles_filter = {a.strip() for a in args.articles.split(",") if a.strip()}
    err = apply_report(
        report_rows,
        articles_filter=articles_filter,
        force_barcode=args.force_barcode,
        dry_run=args.dry_run,
    )
    return 1 if err else 0


def cmd_verify(args):
    rows = load_txt(Path(args.input))
    print("Загрузка товаров MS…")
    products = fetch_all_products()
    fail = verify_txt(rows, products)
    return 1 if fail else 0


def main():
    setup_session()
    ap = argparse.ArgumentParser(description="Sync barcodes RUSbrend → MoySklad")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_pre = sub.add_parser("preflight")
    p_pre.add_argument("--input", required=True)

    p_rec = sub.add_parser("reconcile")
    p_rec.add_argument("--input", required=True)
    p_rec.add_argument("--out", default="../reports/reconcile.csv")
    p_rec.add_argument("--limit", type=int)
    p_rec.add_argument("--articles")
    p_rec.add_argument("--force-barcode", action="store_true")

    p_app = sub.add_parser("apply")
    p_app.add_argument("--report", required=True)
    p_app.add_argument("--articles")
    p_app.add_argument("--force-barcode", action="store_true")
    p_app.add_argument("--dry-run", action="store_true")

    p_ver = sub.add_parser("verify")
    p_ver.add_argument("--input", required=True)

    args = ap.parse_args()
    handlers = {
        "preflight": cmd_preflight,
        "reconcile": cmd_reconcile,
        "apply": cmd_apply,
        "verify": cmd_verify,
    }
    rc = handlers[args.cmd](args)
    sys.exit(rc)


if __name__ == "__main__":
    main()
