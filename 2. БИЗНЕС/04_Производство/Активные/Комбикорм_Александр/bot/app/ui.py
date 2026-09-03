"""Построение текста и клавиатур (независимо от мессенджера).

Клавиатура — список рядов, ряд — список кнопок ``(label, callback_data)``.
Telegram-адаптер (и позже MAX) рендерит эту спецификацию в свой формат.
"""
from __future__ import annotations

from typing import Optional

from .pipeline import DraftLine

Keyboard = list[list[tuple[str, str]]]

OP_TITLES = {
    "sales": "📥 Продажи за день",
    "receipt": "📦 Приёмка",
    "writeoff": "➖ Списание",
    "inventory": "🧮 Инвентаризация",
    "stock": "📊 Остаток",
    "money": "💰 Свёрка дня",
}


def main_menu() -> tuple[str, Keyboard]:
    text = "Главное меню. Выберите действие:"
    kb: Keyboard = [
        [("📥 Продажи за день", "op:sales")],
        [("📦 Приёмка", "op:receipt"), ("➖ Списание", "op:writeoff")],
        [("🧮 Инвентаризация", "op:inventory")],
        [("📊 Остаток", "op:stock")],
        [("💰 Свёрка дня (Виталик)", "op:money")],
        [("❓ Помощь", "op:help")],
    ]
    return text, kb


def op_prompt(op: str) -> str:
    prompts = {
        "sales": (
            "📥 <b>Продажи за день</b>\n\n"
            "Наговорите голосом или напишите текстом. По одной позиции, например:\n"
            "«Раменский ПК-2, три мешка, тысяча двести»\n"
            "Между позициями говорите «дальше».\n"
            "Если была скидка — в конце скажите «итого» и общую сумму."
        ),
        "receipt": (
            "📦 <b>Приёмка</b>\n\n"
            "Наговорите: наименование и сколько мешков пришло. Например:\n"
            "«Раменский ПК-1, десять мешков. Дальше пшеница, пять мешков»"
        ),
        "writeoff": (
            "➖ <b>Списание</b>\n\n"
            "Наговорите: наименование, сколько мешков и причину. Например:\n"
            "«Пшеница, два мешка, отсырела»"
        ),
        "inventory": (
            "🧮 <b>Инвентаризация</b>\n\n"
            "Наговорите фактический остаток: наименование и сколько мешков.\n"
            "«Раменский ПК-2, восемь мешков. Дальше пшеница, двенадцать»"
        ),
        "money": (
            "💰 <b>Свёрка дня</b>\n\n"
            "Пришлите отчёт Виталика текстом в формате:\n"
            "<code>нал 15000 перевод 8000 расход 2000 касса 5000</code>\n"
            "(порядок и лишние слова не важны — я найду числа)"
        ),
    }
    return prompts.get(op, "Наговорите голосом или напишите текстом.")


def _fmt_qty(d: DraftLine) -> str:
    if d.qty is None:
        return "?"
    unit = "меш." if d.unit == "bag" else "кг"
    q = f"{d.qty:g}"
    return f"{q} {unit}"


def render_draft(op: str, drafts: list[DraftLine], *, total_spoken: Optional[float] = None) -> tuple[str, Keyboard]:
    """Нумерованный список позиций + кнопки подтверждения/правки."""
    title = OP_TITLES.get(op, "Операция")
    lines = [f"<b>{title}</b> — проверьте:\n"]
    kb: Keyboard = []
    unresolved = 0

    for d in drafts:
        if d.status == "ok":
            sum_part = f" — {d.line_sum:g} ₽" if d.line_sum else ""
            reason_part = f" ({d.reason})" if d.reason else ""
            lines.append(f"{d.index}. {d.name} — {_fmt_qty(d)}{sum_part}{reason_part}")
        else:
            unresolved += 1
            lines.append(f"{d.index}. ❓ «{d.raw}» — уточните позицию")
            # кнопки-кандидаты под нераспознанной строкой
            row = []
            for c in d.candidates[:3]:
                short = c.name if len(c.name) <= 24 else c.name[:22] + "…"
                row.append((f"{d.index}: {short}", f"pick:{d.index}:{c.product_id}"))
            if row:
                for btn in row:
                    kb.append([btn])
            kb.append([(f"❌ Убрать {d.index}", f"remove:{d.index}")])

    if op == "sales":
        lines_sum = sum((d.line_sum or 0) for d in drafts if d.status == "ok")
        lines.append(f"\nСумма позиций: <b>{lines_sum:g} ₽</b>")
        if total_spoken is not None and total_spoken < lines_sum:
            lines.append(f"Итого сказано: <b>{total_spoken:g} ₽</b> (скидка {lines_sum - total_spoken:g} ₽)")

    footer = []
    if unresolved == 0:
        footer.append(("✅ Подтвердить", "confirm:ok"))
    footer.append(("❌ Отмена", "confirm:cancel"))
    kb.append(footer)

    return "\n".join(lines), kb


def render_stock(rows: list[dict], *, mode: str = "all", low_threshold_bags: float = 3) -> str:
    lines = ["📊 <b>Остаток</b>\n"]
    money_in_goods = 0.0
    shown = [r for r in rows if abs(r["qty_kg"]) > 1e-6]
    if mode == "low":
        shown = [r for r in shown if r["bags"] is not None and r["bags"] <= low_threshold_bags]
    for r in shown:
        if r["bags"] is not None:
            qty_str = f"{r['bags']:.1f} меш."
        else:
            qty_str = f"{r['qty_kg']:g} кг"
        alert = " ⚠️" if (r["bags"] is not None and r["bags"] <= low_threshold_bags) else ""
        lines.append(f"• {r['name']}: {qty_str}{alert}")
        if r.get("price_bag") and r["bags"]:
            money_in_goods += r["bags"] * r["price_bag"]
    if not shown:
        lines.append("Нет позиций с остатком." if mode != "low" else "Нет позиций с низким остатком.")
    lines.append(f"\n💵 Денег в товаре (по цене продажи): <b>{money_in_goods:,.0f} ₽</b>".replace(",", " "))
    return "\n".join(lines)


def render_inventory_report(report: dict) -> str:
    lines = ["🧮 <b>Расхождения инвентаризации</b>\n"]
    any_diff = False
    for l in report["lines"]:
        if abs(l["diff_kg"]) < 1e-6:
            continue
        any_diff = True
        diff_bags = l["diff_bags"]
        db = f"{diff_bags:+.1f} меш." if diff_bags is not None else f"{l['diff_kg']:+g} кг"
        lines.append(
            f"• {l['name']}: было {l['expected_kg']:g} кг → факт {l['counted_kg']:g} кг ({db})"
        )
    if not any_diff:
        lines.append("Расхождений нет — остаток совпал.")
    lines.append(f"\nИтого расхождение по сумме: <b>{report['total_diff_cost']:+,.0f} ₽</b>".replace(",", " "))
    return "\n".join(lines)


def render_money(result: dict) -> str:
    return (
        f"💰 <b>Свёрка дня</b> {result['light']}\n\n"
        f"Выручка по Виталику: <b>{result['vitalik_revenue']:g} ₽</b>\n"
        f"Выручка по боту: <b>{result['bot_revenue']:g} ₽</b>\n"
        f"Расхождение: <b>{result['diff']:+g} ₽</b>"
    )


HELP_TEXT = (
    "❓ <b>Как пользоваться</b>\n\n"
    "1. Нажмите нужную кнопку в меню.\n"
    "2. Наговорите голосом (или напишите) по образцу.\n"
    "3. Я покажу список — проверьте.\n"
    "4. Нераспознанное можно поправить кнопкой-подсказкой или убрать.\n"
    "5. Нажмите «Подтвердить».\n\n"
    "Порядок в позиции: <b>наименование → количество → (сумма)</b>.\n"
    "Единицу можно не говорить — по умолчанию мешки.\n"
    "Команда /menu — вернуться в меню."
)
