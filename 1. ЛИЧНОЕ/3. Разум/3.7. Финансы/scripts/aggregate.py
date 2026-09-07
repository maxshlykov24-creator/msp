#!/usr/bin/env python3
"""Aggregate operations + debts + plan → dashboard/data.js"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

try:
    import yaml
except ImportError:
    print("Need PyYAML: pip3 install pyyaml", file=sys.stderr)
    sys.exit(1)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OPS_PATH = DATA / "operations.jsonl"
REVIEW_PATH = DATA / "review_queue.json"
DEBTS_PATH = ROOT / "debts.yaml"
TAXONOMY_PATH = ROOT / "taxonomy.yaml"
PLAN_DIR = ROOT / "plan"
OUT_PATH = ROOT / "dashboard" / "data.js"


def load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_ops(path: Path) -> list[dict]:
    if not path.exists():
        return []
    ops = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ops.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return ops


def money(x: float) -> float:
    return round(float(x), 2)


def debt_totals(debts: dict) -> dict:
    banks = debts.get("banks") or []
    projects = debts.get("projects") or []
    bank_sum = sum(float(b.get("balance") or 0) for b in banks)
    proj_sum = sum(float(p.get("balance") or 0) for p in projects if float(p.get("balance") or 0) > 0)
    return {
        "banks_total": money(bank_sum),
        "projects_total": money(proj_sum),
        "total": money(bank_sum + proj_sum),
        "banks": [
            {"id": b.get("id"), "name": b.get("name"), "balance": money(b.get("balance") or 0), "priority": b.get("priority")}
            for b in sorted(banks, key=lambda x: x.get("priority") or 99)
        ],
        "projects": [
            {"id": p.get("id"), "name": p.get("name"), "balance": money(p.get("balance") or 0), "priority": p.get("priority")}
            for p in sorted(projects, key=lambda x: x.get("priority") or 99)
            if float(p.get("balance") or 0) > 0
        ],
        "policy": debts.get("policy") or {},
        "updated": debts.get("updated"),
    }


def aggregate_month(ops: list[dict], month: str, taxonomy: dict, plan: dict) -> dict:
    month_ops = [o for o in ops if str(o.get("date", "")).startswith(month)]
    ignore_groups = set((taxonomy.get("rollup") or {}).get("ignore") or ["Прочие"])
    income_groups = set((taxonomy.get("rollup") or {}).get("income") or ["Доходы"])

    income = 0.0
    expense = 0.0
    debt_paid = 0.0
    by_group: dict[str, float] = defaultdict(float)
    by_article: dict[str, float] = defaultdict(float)
    by_merchant: dict[str, float] = defaultdict(float)
    transfers = 0.0

    for o in month_ops:
        amt = float(o.get("amount") or 0)
        group = o.get("group") or "Прочие"
        article = o.get("article") or "Неразмечено"
        transfer = bool(o.get("transfer"))
        desc = (o.get("description") or "").strip() or "—"

        if transfer or (group in ignore_groups and article == "Перевод между счетами"):
            transfers += abs(amt)
            continue

        # Неразмеченное (review) — не искажает план/факт, копится отдельно
        if article == "Неразмечено" or (group in ignore_groups and article == "Неразмечено"):
            continue

        # Tinkoff: negative = expense (outflow), positive = income
        if group in income_groups:
            if amt > 0:
                income += amt
                by_group[group] += amt
                by_article[article] += amt
            elif amt < 0:
                # возврат/корректировка в доходной статье
                income -= abs(amt)
            continue

        if amt < 0:
            spent = abs(amt)
            expense += spent
            by_group[group] += spent
            by_article[article] += spent
            by_merchant[desc] += spent
            if group == "Кредиты и займы" or article == "Платеж по кредиту":
                debt_paid += spent
        elif amt > 0:
            # положительная сумма вне доходов = возврат → уменьшает расход
            expense -= amt
            by_group[group] -= amt
            by_article[article] -= amt

    # plan vs fact
    limits = plan.get("limits") or {}
    plan_vs_fact = []
    for g, lim in limits.items():
        fact = money(by_group.get(g, 0))
        lim_f = money(lim)
        plan_vs_fact.append(
            {
                "group": g,
                "plan": lim_f,
                "fact": fact,
                "delta": money(lim_f - fact),
                "pct": money(100 * fact / lim_f) if lim_f else 0,
            }
        )
    plan_vs_fact.sort(key=lambda x: -x["fact"])

    article_caps = plan.get("article_caps") or {}
    cap_vs_fact = []
    for art, cap in article_caps.items():
        fact = money(by_article.get(art, 0))
        cap_f = money(cap)
        cap_vs_fact.append(
            {
                "article": art,
                "plan": cap_f,
                "fact": fact,
                "delta": money(cap_f - fact),
                "over": fact > cap_f,
            }
        )

    top_articles = sorted(
        [{"article": a, "amount": money(v)} for a, v in by_article.items() if v > 0],
        key=lambda x: -x["amount"],
    )[:12]
    top_merchants = sorted(
        [{"merchant": m, "amount": money(v)} for m, v in by_merchant.items() if v > 0],
        key=lambda x: -x["amount"],
    )[:12]

    # donut: expense groups only
    expense_groups = [
        {"name": g, "amount": money(v)}
        for g, v in sorted(by_group.items(), key=lambda x: -x[1])
        if g not in income_groups and v > 0
    ]

    debt_plan = money(plan.get("debt_payment_plan") or 0)
    free = money(income - expense)

    return {
        "month": month,
        "ops_count": len(month_ops),
        "income": money(income),
        "expense": money(max(0, expense)),
        "free": free,
        "transfers_volume": money(transfers),
        "debt_paid": money(debt_paid),
        "debt_payment_plan": debt_plan,
        "debt_progress_pct": money(100 * debt_paid / debt_plan) if debt_plan else 0,
        "income_plan": money(plan.get("income_plan") or 0),
        "by_group": {k: money(v) for k, v in by_group.items()},
        "plan_vs_fact": plan_vs_fact,
        "article_caps": cap_vs_fact,
        "top_articles": top_articles,
        "top_merchants": top_merchants,
        "expense_groups": expense_groups,
        "goal": plan.get("goal") or "",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--month", default=datetime.now().strftime("%Y-%m"), help="YYYY-MM")
    args = parser.parse_args()
    month = args.month

    taxonomy = load_yaml(TAXONOMY_PATH)
    debts = load_yaml(DEBTS_PATH)
    plan = load_yaml(PLAN_DIR / f"{month}.yaml")
    ops = load_ops(OPS_PATH)

    review_count = 0
    if REVIEW_PATH.exists():
        try:
            review_count = len(json.loads(REVIEW_PATH.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            review_count = 0

    month_data = aggregate_month(ops, month, taxonomy, plan)
    debt_data = debt_totals(debts)

    # ETA days hypothesis: if debt_paid this month > 0, extrapolate
    total_debt = debt_data["total"]
    paid = month_data["debt_paid"]
    days_in_month = 30
    day = datetime.now().day if datetime.now().strftime("%Y-%m") == month else days_in_month
    pace = paid / max(day, 1)
    eta_days = int(total_debt / pace) if pace > 0 else None

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "month": month,
        "review_count": review_count,
        "debts": debt_data,
        "month_summary": month_data,
        "eta_days_hypothesis": eta_days,
        "note": "eta_days — гипотеза по текущему темпу погашения в выбранном месяце",
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    js = "window.FINANCE_DATA = " + json.dumps(payload, ensure_ascii=False, indent=2) + ";\n"
    OUT_PATH.write_text(js, encoding="utf-8")
    print(f"Wrote {OUT_PATH}")
    print(
        f"{month}: income={month_data['income']} expense={month_data['expense']} "
        f"debt_paid={month_data['debt_paid']}/{month_data['debt_payment_plan']} "
        f"ops={month_data['ops_count']} review={review_count}"
    )
    print(f"Debts total: {debt_data['total']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
