"""Fail-closed allocation preview. Never sends network writes."""
from datetime import datetime, timezone
from decimal import Decimal
from .service import DomainError, require


def plan_publication(*, available, safety_stock, desired, confirmed, orders_read_at,
                     max_age_seconds, mappings_verified, single_writer, reductions_confirmed):
    require(type(available) is int and available >= 0, 'Нужен известный целый доступный остаток')
    require(type(safety_stock) is int and 0 <= safety_stock <= available, 'Некорректный страховой запас')
    require(desired and set(desired) == set(confirmed), 'Площадки желаемого и подтверждённого остатка должны совпадать')
    require(all(type(v) is int and v >= 0 for v in desired.values()), 'Квоты должны быть целыми и неотрицательными')
    require(all(v is None or type(v) is int and v >= 0 for v in confirmed.values()), 'Некорректное подтверждение остатка')
    require(sum(desired.values()) <= available - safety_stock, 'Сумма квот превышает доступный запас')
    require(mappings_verified and single_writer, 'Сопоставления и единственный источник записи не подтверждены')
    increases = [p for p, value in desired.items() if confirmed[p] is None or value > confirmed[p]]
    decreases = [p for p, value in desired.items() if confirmed[p] is not None and value < confirmed[p]]
    if increases:
        require(orders_read_at is not None, 'Нет актуального чтения заказов')
        timestamp = datetime.fromisoformat(orders_read_at.replace('Z', '+00:00'))
        require(timestamp.tzinfo is not None, 'Время чтения без часового пояса')
        age = (datetime.now(timezone.utc) - timestamp).total_seconds()
        require(0 <= age <= max_age_seconds, 'Заказы устарели; увеличение запрещено')
        require(all(v is not None for v in confirmed.values()), 'Неизвестный остаток площадки; увеличение запрещено')
        require(not decreases or reductions_confirmed, 'Сначала подтвердить уменьшение прежних квот')
        require(sum(max(desired[p], confirmed[p]) for p in desired) <= available - safety_stock or reductions_confirmed,
                'Пока перенос квот не подтверждён, суммарное обещание превышает запас')
    return {'desired': desired, 'decrease_first': decreases, 'increase_after': increases,
            'publication_enabled': False, 'message': 'Проверен только расчёт. Живая запись требует пилота и настройки канала'}
