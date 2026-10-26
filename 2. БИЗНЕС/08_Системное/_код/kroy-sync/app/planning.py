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
    if any(type(v) is not int or v < 0 or v > 1000000000 for v in
           (sales, period_days, horizon_days, eligible_stock, expected_remaining)):
        raise ValueError('Периоды и количества должны быть целыми неотрицательными числами')
    if period_days <= 0 or horizon_days <= 0:
        raise ValueError('Проверь период и количества')
    target = ceil(Decimal(str(sales)) * Decimal(str(horizon_days)) / Decimal(str(period_days)))
    return {'target': target, 'need': max(0, target - eligible_stock - expected_remaining),
            'included_stock': eligible_stock, 'included_future': expected_remaining}


def grouped_layout_preview(model, needs_by_color, ratios, available_by_color):
    """Shared pattern selection across colours with a bounded deterministic search.

    Each colour has separate whole-roll availability. Patterns are shared only inside
    a model; the caller must still confirm fabric/technology compatibility.
    """
    if model not in NORMS:
        raise ValueError('Нет нормы модели')
    if not needs_by_color or set(needs_by_color) != set(available_by_color):
        raise ValueError('Потребность и доступная ткань должны включать одинаковые цвета')
    if len(needs_by_color) > 12 or not 1 <= len(ratios) <= 8:
        raise ValueError('Предварительный поиск: до 12 цветов и 8 раскладок')
    sizes = set(next(iter(needs_by_color.values())))
    if not sizes or len(sizes) > 12:
        raise ValueError('Нужно от 1 до 12 размеров')
    for color, need in needs_by_color.items():
        if set(need) != sizes or any(type(v) is not int or v < 0 for v in need.values()):
            raise ValueError('Нужны одинаковые размеры и целая неотрицательная потребность')
        rolls = available_by_color[color]
        if type(rolls) is not int or not 0 <= rolls <= 30:
            raise ValueError('Доступность: от 0 до 30 целых рулонов')
    for ratio in ratios:
        if set(ratio) != sizes:
            raise ValueError('Размеры раскладки должны совпадать с потребностью')
        allocate(NORMS[model], ratio)
    from itertools import combinations
    colors = sorted(needs_by_color)
    checked = 0
    for pattern_count in range(1, len(ratios) + 1):
        options = []
        for indices in combinations(range(len(ratios)), pattern_count):
            checked += 1
            results = {}
            for color in colors:
                if not any(needs_by_color[color].values()):
                    results[color] = {'rolls': 0, 'patterns': [], 'output': dict(needs_by_color[color]), 'deviation_percent': {s: None for s in needs_by_color[color]}}
                    continue
                if available_by_color[color] == 0:
                    break
                candidate = layout_preview(model, needs_by_color[color], [ratios[i] for i in indices], available_by_color[color])
                if candidate['search_limited']:
                    return {'preliminary': True, 'search_limited': True, 'result': None,
                            'message': 'Лимит поиска. Нельзя утверждать, что решения нет'}
                if not candidate['result']:
                    break
                row = candidate['result']
                row['patterns'] = [indices[i] for i in row['patterns']]
                results[color] = row
            if len(results) == len(colors):
                extra = sum(max(row['output'][s] - needs_by_color[color][s], 0)
                            for color, row in results.items() for s in row['output'])
                options.append((extra, sum(r['rolls'] for r in results.values()), indices, results))
        if options:
            best = min(options, key=lambda x: (x[0], x[1], x[2]))
            return {'preliminary': True, 'search_limited': False, 'patterns_used': list(best[2]),
                    'result': best[3], 'norm': NORMS[model], 'norm_source': NORM_SOURCE,
                    'message': 'Совместимость ткани и раскладок должна быть подтверждена закройщиком'}
    return {'preliminary': True, 'search_limited': False, 'result': None,
            'message': 'Решение в заданных раскладках и допуске ±20% не найдено'}
