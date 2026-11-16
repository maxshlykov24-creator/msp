"""Deterministic preview. A technology-approved ratio set is required for execution."""
from decimal import Decimal, ROUND_FLOOR
from itertools import combinations_with_replacement
from math import ceil

NORMS = {'Лонг скимс': 75, 'Лонг Пилатес': 72, 'Лонг бюс': 72,
         'Лонг Стойка': 83, 'Костюм двойка Поло': 27, 'Штаны с манжетами': 45,
         'Костюм двойка Плечо': 23, 'Костюм Тройка': 14,
         'Футболка Гейша': 120, 'Футболка Сердца': 120, 'Футболка скимс': 110}
NORM_SOURCE = 'Женя, Telegram, 23.10.2026; применимость к ткани и размерам требует подтверждения'


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


def layout_preview(model, need, ratios, max_rolls, confirmed=False, _budget=None):
    if model not in NORMS:
        raise ValueError('Нет нормы; доступно ручное задание')
    if not need or any(type(v) is not int or v < 0 for v in need.values()):
        raise ValueError('Потребность должна быть целой и неотрицательной')
    if not 1 <= max_rolls <= 1000 or not 1 <= len(ratios) <= 12:
        raise ValueError('Поиск: до 1000 рулонов и 12 допустимых раскладок')
    if any(set(r) != set(need) for r in ratios):
        raise ValueError('Состав размеров раскладок и потребности должен совпадать')
    # Candidate is one roll, with integral garment output; never rounds each size up.
    per_roll = [allocate(NORMS[model], r) for r in ratios]
    budget = _budget if _budget is not None else {'remaining': 50000}
    best = None
    checked = 0
    limited = False
    for rolls in range(max(1, ceil(sum(need.values()) * .8 / NORMS[model])), max_rolls + 1):
        if rolls * NORMS[model] > sum(need.values()) * Decimal('1.2'):
            break
        for picks in combinations_with_replacement(range(len(ratios)), rolls):
            checked += 1
            budget['remaining'] -= 1
            if checked > 50000 or budget['remaining'] < 0:
                limited = True
                break
            output = {s: sum(per_roll[i][s] for i in picks) for s in need}
            if not all((v == 0 and output[s] == 0) or
                       (v > 0 and Decimal('.8') * v <= output[s] <= Decimal('1.2') * v)
                       for s, v in need.items()):
                continue
            score = (len(set(picks)), sum(abs(output[s] - need[s]) for s in need), sum(max(need[s]-output[s],0) for s in need), rolls)
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


def grouped_layout_preview(model, needs_by_color, ratios, available_by_color, allow_purchase=False):
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
        if type(rolls) is not int or not 0 <= rolls <= 1000:
            raise ValueError('Доступность: от 0 до 1000 целых рулонов')
    for ratio in ratios:
        if set(ratio) != sizes:
            raise ValueError('Размеры раскладки должны совпадать с потребностью')
        allocate(NORMS[model], ratio)
    actual_available = dict(available_by_color)
    if allow_purchase:
        available_by_color = {c:max(actual_available[c], min(1000, ceil(sum(need.values())*1.2/NORMS[model]))) for c,need in needs_by_color.items()}
    from itertools import combinations
    colors = sorted(needs_by_color)
    checked = 0
    budget = {'remaining': 100000}
    limited = False
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
                candidate = layout_preview(model, needs_by_color[color], [ratios[i] for i in indices], available_by_color[color], _budget=budget)
                limited = limited or candidate['search_limited']
                if not candidate['result']:
                    break
                row = candidate['result']
                row['patterns'] = [indices[i] for i in row['patterns']]
                results[color] = row
            if len(results) == len(colors):
                extra = sum(abs(row['output'][s] - needs_by_color[color][s])
                            for color, row in results.items() for s in row['output'])
                options.append((extra, sum(r['rolls'] for r in results.values()), indices, results))
            if limited: break
        if options:
            best = min(options, key=lambda x: (x[0], sum(max(needs_by_color[c][s]-row['output'][s],0) for c,row in x[3].items() for s in row['output']), x[1], x[2]))
            for color,row in best[3].items(): row['purchase_rolls'] = max(0,row['rolls']-actual_available[color])
            return {'preliminary': True, 'search_limited': limited, 'patterns_used': list(best[2]),
                    'result': best[3], 'pattern_outputs': [allocate(NORMS[model], r) for r in ratios], 'norm': NORMS[model], 'norm_source': NORM_SOURCE,
                    'message': ('Достигнут лимит поиска. Показан найденный допустимый вариант, оптимальность не доказана. ' if limited else '') + 'Совместимость ткани и раскладок должна быть подтверждена закройщиком'}
        if limited:
            return {'preliminary':True,'search_limited':True,'result':None,'message':'Лимит поиска. Нельзя утверждать, что решения нет'}
    return {'preliminary': True, 'search_limited': False, 'result': None,
            'message': 'Решение в заданных раскладках и допуске ±20% не найдено'}
