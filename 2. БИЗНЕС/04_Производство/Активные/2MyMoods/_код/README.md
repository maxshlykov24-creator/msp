# Скрипты 2MY

Секреты: `../06_Доступы/.env` (не git). Живую воронку Продажи `10193806` не трогают, кроме `cutover.py` и только с `CUTOVER=ДА` плюс `--apply`.

| Файл | Что |
|---|---|
| `setup_pipelines.py` | Новые воронки рядом |
| `sla_worker.py` | Задачи SLA на новых id. `--apply` |
| `route_stock.py` | Оплачен → сборка/производство. `--apply` |
| `sync_liveinform.py` | Трек → Получен. `--apply` |
| `assign_shift.py` | Ответственный по CSV смен |
| `cutover.py` | Карта переноса. Без `CUTOVER=ДА` не двигает |
| `map_vitrina.py` | 48 позиций → номенклатура МС |
| `collect_dashboard.py` | Живой срез → `дашборд/` |
| `marking_audit.py` | Честный знак, только чтение |
| `dedup_phones.py` | Дубли +7/8 |
| `backfill_ms_amo.py` | Заказы МС без ссылки на amo |

Витрина: Telegram, чат AMO & МС | 2MY, сообщение 1334, 18.09.2026.
