"""Deterministic preview. A technology-approved ratio set is required for execution."""
from decimal import Decimal, ROUND_FLOOR
from itertools import combinations_with_replacement
from math import ceil

NORMS = {'Лонг скимс': 75, 'Лонг Пилатес': 72, 'Лонг бюс': 72,
         'Лонг Стойка': 83, 'Костюм двойка Поло': 27, 'Штаны с манжетами': 45,
         'Костюм двойка Плечо': 23, 'Костюм Тройка': 14,
         'Футболка Гейша': 120, 'Футболка Сердца': 120, 'Футболка скимс': 110}
NORM_SOURCE = 'Максим, Codex, 24.10.2026; применимость не подтверждена'


def allocate(total, weights):
    if type(total) is not int or total < 0 or not weights:
        raise ValueError('Нужны целое количество и раскладка')
    weights = {k: Decimal(str(v)) for k, v in weights.items()}
    if any(not v.is_finite() or v < 0 for v in weights.values()) or sum(weights.values()) <= 0:
        raise ValueError('Некорректные доли размеров')
    exact = {k: total * v / sum(weights.values()) for k, v in weights.items()}
    out = {k: int(v.to_integral_value(rounding=ROUND_FLOOR)) for k, v in exact.items()}
    for k in sorted(out, key=lambda k: (-(exact[k] - out[k]), k))[:total - sum(out.values())]:
        out[k] += 1
    return out


def layout_preview(model, need, ratios, max_rolls, confirmed=False):
    if model not in NORMS:
        raise ValueError('Нет нормы; доступно ручное задание')
    if not need or any(type(v) is not int or v < 0 for v in need.values()):
        raise ValueError('Потребность должна быть целой и неотрицательной')
    if not 1 <= max_rolls <= 30 or not 1 <= len(ratios) <= 12:
        raise ValueError('Поиск: до 30 рулонов и 12 допустимых раскладок')
    if any(set(r) != set(need) for r in ratios):
        raise ValueError('Состав размеров раскладок и потребности должен совпадать')
    # Candidate is one roll, with integral garment output; never rounds each size up.
    per_roll = [allocate(NORMS[model], r) for r in ratios]
    best = None
    checked = 0
    limited = False
    for rolls in range(1, max_rolls + 1):
        if rolls * NORMS[model] > sum(need.values()) * Decimal('1.2'):
            break
        for picks in combinations_with_replacement(range(len(ratios)), rolls):
            checked += 1
            if checked > 50000:
                limited = True
                break
            output = {s: sum(per_roll[i][s] for i in picks) for s in need}
            if not all((v == 0 and output[s] == 0) or
                       (v > 0 and Decimal('.8') * v <= output[s] <= Decimal('1.2') * v)
                       for s, v in need.items()):
                continue
            score = (len(set(picks)), sum(max(output[s] - need[s], 0) for s in need), rolls)
            if best is None or score < best[0]:
                best = (score, {'rolls': rolls, 'patterns': list(picks), 'output': output,
                               'deviation_percent': {s: round((output[s] / v - 1) * 100, 2) if v else None for s, v in need.items()}})
        if limited:
            break
    return {'model': model, 'need': need, 'norm': NORMS[model], 'norm_source': NORM_SOURCE,
            'preliminary': True, 'technology_confirmed': confirmed, 'search_limited': limited,
            'result': best[1] if best else None,
            'message': 'Предварительный вариант; норма требует подтверждения' if best else
                       'В заданных раскладках и доступной ткани решения в допуске ±20% не найдено'}


def demand(sales, period_days, horizon_days, eligible_stock, expected_remaining):
    if period_days <= 0 or horizon_days <= 0 or min(sales, eligible_stock, expected_remaining) < 0:
        raise ValueError('Проверь период и количества')
    target = ceil(Decimal(str(sales)) * Decimal(str(horizon_days)) / Decimal(str(period_days)))
    return {'target': target, 'need': max(0, target - eligible_stock - expected_remaining),
            'included_stock': eligible_stock, 'included_future': expected_remaining}
