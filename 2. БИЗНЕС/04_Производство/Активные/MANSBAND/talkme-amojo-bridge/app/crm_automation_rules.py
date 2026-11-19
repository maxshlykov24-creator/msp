"""Approved task policy, sourced from the Digital UI on 16/17 November 2026.

SLA and task deadlines are separate. Control-only rules never create a second
shop execution task. Their output is a manager exception after the original SLA.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.crm_automation import (
    SALES, COMPLAINTS, OFFLINE, ONLINE, RENTAL, COMPANY, Catalog,
    MSK, deadline, field_date, field_values, stamp,
)


@dataclass(frozen=True)
class TaskRule:
    id: str
    source_id: str
    pipelines: tuple[int, ...]
    stage: str
    task_type: int
    text: str
    due_mode: str
    due_amount: int = 0
    delay_hours: int = 0
    date_field: int | None = None
    control_only: bool = False
    condition_field: int | None = None
    condition_values: tuple[str, ...] = ()


SELL = (SALES, OFFLINE, ONLINE, RENTAL, COMPANY)
SHOP = (SALES, OFFLINE, ONLINE, RENTAL, COMPANY)
VISIT = (SALES, OFFLINE, RENTAL, COMPANY)
DELIVERY = (SALES, ONLINE)

RULES = (
    TaskRule("new", "32844779", SELL, "Новая заявка", 3946566,
             "1. Ответить клиенту 2. Зафиксировать тип клиента и потребность 3. Поменять этап на Взято в работу", "first_response"),
    TaskRule("qualify", "31634565", SELL, "Взято в работу", 3946570,
             "1. Уточнить потребность клиента.\n2. Согласовать следующий шаг: встречу или доставку.\n"
             "3. Перевести сделку на этап, соответствующий договорённости.", "native_tomorrow"),
    TaskRule("promise", "32155653", SELL, "Дано обещание", 3946574,
             "1. Выполнить обещание в согласованный с клиентом срок.\n2. Зафиксировать результат и согласовать следующий шаг.\n"
             "3. Перевести сделку на соответствующий этап, если договорённость изменилась.", "native_week"),
    TaskRule("wants_visit", "32160721", VISIT, "Хочет прийти", 2,
             "1. Назначить примерную дату встречи 2. Поменять этап", "native_tomorrow"),
    TaskRule("wants_order", "32160947", DELIVERY, "Хочет заказать", 3946594,
             "1. Согласовать дату и адрес отправки 2. Оформить накладную 3. Передать на сборку", "native_week"),
    TaskRule("transfer_control", "31590601", SHOP, "Ждет товар", 3946582,
             "Просрочено перемещение. Выяснить причину в кассе, согласовать срок поступления и проконтролировать исполнителя.", "days", 4, control_only=True),
    TaskRule("hold_control", "31591467", SHOP, "Товар в магазине", 3946582,
             "Товар поступил, но отложка не завершена. Проверить исполнение задачи консультантом в кассе.", "end_of_day", control_only=True),
    TaskRule("hold_visit", "32160943", VISIT, "Товар отложен", 3946586,
             "1. Напомнить о сроках отложки 2. Согласовать дату визита 3. Перевести на этап Встреча назначена", "end_of_day",
             condition_field=3134039, condition_values=("Оффлайн",)),
    TaskRule("visit_followup", "31590571", (SALES, OFFLINE, RENTAL), "Встреча назначена", 2,
             "1. Выяснить, приехал ли клиент 2. Назначить дату встречи повторно, если не приехал", "end_of_day", delay_hours=24, date_field=3382311),
    TaskRule("rental_return", "31590341", (SALES, RENTAL), "В аренде", 3946590,
             "1. Проверить возврат аренды 2. Связаться при задержке 3. Отметить подтверждённый возврат в кассе", "end_of_day", delay_hours=10, date_field=3322895),
    TaskRule("assembly_control", "32160949", DELIVERY, "Передан на сборку", 3946594,
             "Сборка просрочена. Проверить задачу консультанта в кассе и устранить причину задержки.", "hours", 22, control_only=True),
    TaskRule("courier", "32160951", DELIVERY, "Собран", 3946594,
             "1. Вызвать курьера и согласовать передачу заказа в СДЭК.\n2. Закрыть задачу после вызова курьера.\n"
             "3. После фактической передачи консультант отмечает отправку в кассе. "
             "Если заказ ведётся только в amoCRM, перевести сделку на этап «Отправлен» после передачи.", "hours", 1),
    TaskRule("shipping_control", "32160955", DELIVERY, "Отправлен", 3946594,
             "1. Выяснить, почему заказ ещё не прибыл 2. Взять доставку на контроль", "native_three_days", delay_hours=286),
    TaskRule("pickup_control", "32160957", DELIVERY, "Доставлен", 3946594,
             "1. Выяснить, почему клиент ещё не забрал заказ 2. Взять получение на контроль", "end_of_day", delay_hours=286),
    TaskRule("not_purchased", "31635073", DELIVERY, "Не выкуплен", 3946578,
             "1. Выяснить причину невыкупа 2. Согласовать дальнейшие действия", "end_of_day"),
    TaskRule("failure_feedback", "31591403", SELL, "Провал", 3946602,
             "Выяснить причину отказа и зафиксировать обратную связь", "days", 2,
             condition_field=3134051, condition_values=("Не было размера", "Нет в ассортименте", "Много перешивать", "Не устроило качество", "Не нравится крой", "Другое")),
    TaskRule("complaint_qualify", "32353893", (COMPLAINTS,), "Взято в работу", 3946570,
             "1. Уточнить детали проблемы 2. Прийти к договорённостям 3. Назначить встречу или следующий шаг", "end_of_day"),
    TaskRule("complaint_visit", "32353895", (COMPLAINTS,), "Хочет прийти", 2,
             "1. Назначить встречу 2. Сменить этап", "days", 5),
    TaskRule("complaint_hold", "32990523", (COMPLAINTS,), "Товар отложен", 1,
             "1. Проверить срок резерва 2. Назначить встречу или отправку 3. Поменять этап", "days", 14),
    TaskRule("complaint_success", "32353897", (COMPLAINTS,), "Успех", 2,
             "Проверить результат решения проблемы и собрать обратную связь", "native_tomorrow"),
    TaskRule("complaint_failure", "32353899", (COMPLAINTS,), "Провал", 2,
             "Разобрать причину закрытия обращения без решения, зафиксировать результат", "native_tomorrow"),
    TaskRule("company_invoice", "approved-20261117", (COMPANY,), "Счет запрошен", 1,
             "Проконтролировать подготовку и отправку счёта; существующую задачу кассы не дублировать", "native_tomorrow", control_only=True),
    TaskRule("company_documents_ready", "approved-20261117", (COMPANY,), "Документы готовы", 1,
             "Передать готовые документы клиенту и зафиксировать результат", "native_tomorrow"),
    TaskRule("company_documents_sent", "approved-20261117", (COMPANY,), "Документы переданы", 1,
             "Проверить получение документов и завершение необходимых действий", "native_tomorrow"),
)


def applicable(rule: TaskRule, lead: dict, catalog: Catalog) -> bool:
    if lead.get("pipeline_id") not in rule.pipelines or catalog.stage(lead) != rule.stage:
        return False
    if rule.condition_field:
        return any(v in rule.condition_values for v in field_values(lead, rule.condition_field))
    return True


def task_times(rule: TaskRule, lead: dict, entry: datetime) -> tuple[datetime, datetime]:
    base = field_date(lead, rule.date_field) if rule.date_field else entry
    if base is None:
        raise ValueError("missing_required_date")
    starts = stamp(base + timedelta(hours=rule.delay_hours))
    if rule.id == "failure_feedback":
        starts += timedelta(minutes=1)
    due = deadline(starts, rule.due_mode, rule.due_amount)
    # An exception is raised after its SLA; it is already actionable/overdue.
    return (due, due) if rule.control_only else (starts, due)


def semantic_occurrence(rule: TaskRule, lead: dict, entry_event_id: str) -> str:
    if rule.date_field:
        date_value = field_date(lead, rule.date_field)
        if date_value is None:
            raise ValueError("missing_required_date")
        return f"date:{rule.date_field}:{int(date_value.timestamp())}"
    if not entry_event_id:
        raise ValueError("missing_entry_event")
    return "entry:" + entry_event_id


STAGE_DATE_FIELDS = {
    "Дано обещание": 3815335, "Товар отложен": 3810739,
    "Встреча назначена": 3382311, "Аренда оплачена": 3815337,
    "В аренде": 3322895, "Счет выставлен": 3134989,
    "Сертификат оплачен": 3169911,
}
