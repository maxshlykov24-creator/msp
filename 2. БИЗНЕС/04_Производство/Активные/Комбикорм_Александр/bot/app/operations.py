"""Бизнес-операции товароучёта: продажи, приёмка, списание, инвентаризация, деньги.

Все изменения остатка идут через ``movements`` (журнал). Здесь — сборка сессий и
подсчёт производных величин (выручка, скидка, расхождения, светофор).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date as date_cls
from typing import Optional

from . import db


@dataclass
class LineItem:
    """Подтверждённая позиция операции (единицы — как сказал пользователь)."""
    product_id: int
    name: str
    qty: float
    unit: str  # bag | kg
    bag_size_kg: Optional[float] = None
    price: Optional[float] = None
    line_sum: Optional[float] = None
    reason: Optional[str] = None

    def qty_kg(self) -> float:
        if self.unit == "kg":
            return self.qty
        return self.qty * (self.bag_size_kg or 0)


def _today() -> str:
    return date_cls.today().isoformat()


# --- продажи -------------------------------------------------------------

def commit_sales(
    conn: sqlite3.Connection,
    items: list[LineItem],
    total_spoken: Optional[float] = None,
    raw_text: Optional[str] = None,
) -> dict:
    lines_sum = sum((i.line_sum or 0) for i in items)
    revenue = total_spoken if total_spoken is not None else lines_sum
    discount = max(0.0, lines_sum - revenue) if total_spoken is not None else 0.0

    with db.transaction(conn):
        cur = conn.execute(
            "INSERT INTO sales_sessions(date, total_spoken, discount, revenue, status) "
            "VALUES(?,?,?,?, 'confirmed')",
            (_today(), total_spoken, discount, revenue),
        )
        session_id = int(cur.lastrowid)
        for i in items:
            conn.execute(
                "INSERT INTO sale_lines(session_id, product_id, qty_kg, unit_spoken, price, line_sum) "
                "VALUES(?,?,?,?,?,?)",
                (session_id, i.product_id, i.qty_kg(), i.unit, i.price, i.line_sum),
            )
            db.record_movement(
                conn,
                type_="sale",
                product_id=i.product_id,
                qty_kg=-abs(i.qty_kg()),
                unit_spoken=i.unit,
                price=i.price,
                raw_text=raw_text,
                session_id=session_id,
            )
    return {
        "session_id": session_id,
        "lines_sum": lines_sum,
        "revenue": revenue,
        "discount": discount,
    }


# --- приёмка / списание --------------------------------------------------

def commit_receipt(conn: sqlite3.Connection, items: list[LineItem], raw_text: Optional[str] = None) -> int:
    with db.transaction(conn):
        for i in items:
            db.record_movement(
                conn,
                type_="receipt",
                product_id=i.product_id,
                qty_kg=abs(i.qty_kg()),
                unit_spoken=i.unit,
                raw_text=raw_text,
            )
    return len(items)


def commit_writeoff(conn: sqlite3.Connection, items: list[LineItem], raw_text: Optional[str] = None) -> int:
    with db.transaction(conn):
        for i in items:
            db.record_movement(
                conn,
                type_="writeoff",
                product_id=i.product_id,
                qty_kg=-abs(i.qty_kg()),
                unit_spoken=i.unit,
                note=i.reason,
                raw_text=raw_text,
            )
    return len(items)


# --- инвентаризация ------------------------------------------------------

def build_inventory_report(conn: sqlite3.Connection, counted: list[LineItem]) -> dict:
    """Считает expected/counted/diff без записи (для показа перед подтверждением)."""
    lines = []
    for i in counted:
        expected = db.stock_kg(conn, i.product_id)
        counted_kg = i.qty_kg()
        diff = counted_kg - expected
        prod = db.product_by_id(conn, i.product_id)
        cost = (prod["cost"] if prod else None) or 0
        bag = (prod["bag_size_kg"] if prod else None) or 0
        lines.append(
            {
                "product_id": i.product_id,
                "name": i.name,
                "expected_kg": expected,
                "counted_kg": counted_kg,
                "diff_kg": diff,
                "diff_bags": (diff / bag) if bag else None,
                "diff_cost": diff * (cost / bag) if bag and cost else 0,
            }
        )
    total_diff_cost = sum(l["diff_cost"] for l in lines)
    return {"lines": lines, "total_diff_cost": total_diff_cost}


def commit_inventory(conn: sqlite3.Connection, counted: list[LineItem], raw_text: Optional[str] = None) -> dict:
    report = build_inventory_report(conn, counted)
    with db.transaction(conn):
        cur = conn.execute(
            "INSERT INTO inventory_sessions(date, status) VALUES(?, 'confirmed')",
            (_today(),),
        )
        session_id = int(cur.lastrowid)
        for l in report["lines"]:
            conn.execute(
                "INSERT INTO inventory_lines(session_id, product_id, expected_kg, counted_kg, diff_kg) "
                "VALUES(?,?,?,?,?)",
                (session_id, l["product_id"], l["expected_kg"], l["counted_kg"], l["diff_kg"]),
            )
            if abs(l["diff_kg"]) > 1e-6:
                db.record_movement(
                    conn,
                    type_="inv_adj",
                    product_id=l["product_id"],
                    qty_kg=l["diff_kg"],
                    note="инвентаризация",
                    raw_text=raw_text,
                    session_id=session_id,
                )
    report["session_id"] = session_id
    return report


# --- деньги: свёрка дня --------------------------------------------------

def bot_revenue_for_date(conn: sqlite3.Connection, day: str) -> float:
    row = conn.execute(
        "SELECT COALESCE(SUM(revenue), 0) FROM sales_sessions WHERE date = ?",
        (day,),
    ).fetchone()
    return float(row[0] or 0)


def commit_money(
    conn: sqlite3.Connection,
    *,
    day: str,
    cash: float,
    transfer: float,
    expense: float,
    closing_cash: Optional[float] = None,
) -> dict:
    vitalik_revenue = cash + transfer
    bot_rev = bot_revenue_for_date(conn, day)
    diff = vitalik_revenue - bot_rev
    base = max(vitalik_revenue, bot_rev, 1)
    ratio = abs(diff) / base
    if ratio <= 0.02:
        light = "🟢"
    elif ratio <= 0.07:
        light = "🟡"
    else:
        light = "🔴"

    with db.transaction(conn):
        conn.execute(
            """
            INSERT INTO daily_money(date, cash, transfer, expense, closing_cash, bot_revenue, status)
            VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(date) DO UPDATE SET
                cash=excluded.cash, transfer=excluded.transfer, expense=excluded.expense,
                closing_cash=excluded.closing_cash, bot_revenue=excluded.bot_revenue,
                status=excluded.status
            """,
            (day, cash, transfer, expense, closing_cash, bot_rev, light),
        )
    return {
        "light": light,
        "vitalik_revenue": vitalik_revenue,
        "bot_revenue": bot_rev,
        "diff": diff,
    }
