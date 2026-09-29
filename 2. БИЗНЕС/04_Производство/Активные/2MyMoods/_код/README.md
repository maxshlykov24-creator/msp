# Скрипты 2MY

Секреты: `../06_Доступы/.env` (не git). Живую воронку Продажи `10193806` не трогают, кроме `cutover.py` и только с `CUTOVER=ДА` плюс `--apply`.

| Файл | Что |
|---|---|
| `setup_pipelines.py` | Новые воронки рядом |
| `webhook_server.py` | Один вебхук amo на все этапы новых воронок |
| `stage_plan.py` | Сроки задач от момента перехода |
| `sla_worker.py` | Задачи SLA на новых id. `--apply` |
| `route_stock.py` | Оплачен → сборка/производство. `--apply` |
| `sync_liveinform.py` | Трек → Получен. `--apply` |
| `assign_shift.py` | Открытые задачи по графику Кристины и Тани. Лист [График](https://docs.google.com/spreadsheets/d/17V1PO2k4BjFuOQPuXmXvq8ckvOmU8nqD9Q4YhusIwdM/edit). Утро 07:00 внутри `webhook_server.py` |
| `cutover.py` | Карта переноса. Без `CUTOVER=ДА` не двигает |
| `map_vitrina.py` | 48 позиций → номенклатура МС |
| `collect_dashboard.py` | Живой срез → `дашборд/` |
| `marking_audit.py` | Честный знак, только чтение |
| `dedup_phones.py` | Дубли +7/8 |
| `backfill_ms_amo.py` | Заказы МС без ссылки на amo |

Витрина: Telegram, чат AMO & МС | 2MY, сообщение 1334, 18.09.2026.
