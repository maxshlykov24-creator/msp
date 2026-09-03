# BLOCKERS — Van Pak night pipeline 2026-06-05

## BLOCKER-1: OLD MoySklad API недоступен (критичный)
---

## CLOSED 2026-06-05: shipmentAddress (Адрес доставки)

| | |
|---|---|
| **Симптом** | В NEW у заказов пусто «Адрес доставки»; в OLD заполнено |
| **Причина** | Поле `customerorder.shipmentAddress` не копировалось в `migrate._build_doc_body` / catchup |
| **Исправление** | `migrate.py` + catchup 4 июня (8/8 OK) + `sync_shipment_address_from_old.py` backfill 2026 |
| **Профилактика** | DoD дня: `shipmentAddress` OLD=NEW; backfill после catchup если дата < fix |


---

## CLOSED 2026-06-05: shipmentAddress (Адрес доставки)

| | |
|---|---|
| **Симптом** | В NEW у заказов пусто «Адрес доставки»; в OLD заполнено |
| **Причина** | Поле `customerorder.shipmentAddress` не копировалось в `migrate._build_doc_body` / catchup |
| **Исправление** | `migrate.py` + catchup 4 июня (8/8 OK) + `sync_shipment_address_from_old.py` backfill 2026 |
| **Профилактика** | DoD дня: `shipmentAddress` OLD=NEW; backfill после catchup если дата < fix |


| | |
|---|---|
| **Симптом** | HTTP 403, code 1061: «Отсутствует доступ к API для данного пользователя» |
| **Токен** | OLD (`…9ebe`) — тот же, что работал 2026-06-04 в `sync_demand_links` |
| **Проверка** | `GET /entity/organization?limit=1` → 403; `GET /entity/customerorder/{uuid}` → 403 |
| **Затронуто** | audit agent, sync agent/attrs, catchup 04.06, audit/reconcile stock, sync_demand_links, fix_payment_links |
| **Не затронуто** | NEW API (200), pair audit anchor-only, отчёты на NEW |

**Действие:** выдать новый API-токен OLD или включить доступ пользователю → `export OLD_TOKEN=...` → перезапустить `run_night_pipeline.sh`

---

## BLOCKER-2: Agent/attrs sync не выполнен (следствие BLOCKER-1)

| doc_type | NEW name | NEW id | OLD id | поле | причина |
|----------|----------|--------|--------|------|---------|
| customerorder | ТУ-00887 | f85b6c38-5e91-11f1-0a80-1eed00252120 | c693c3bf-5e58-11f1-0a80-0d5800161131 | agent | OLD API 403 — нельзя прочитать эталон |
| customerorder | ТУ-00887 | (same) | (same) | 4 атрибута заказа | OLD API 403 |

**NEW факт:** agent = ООО «ТЕХНО-ПОСМ»; атрибуты пустые. **Ожидание OLD:** agent = ООО «НПО ГЕОНАУКА»; Способ поставки = Доставка: Платная.

---

## BLOCKER-3: payedSum / shippedSum (из prior session, не закрыто)

| Тема | Факт | Причина |
|------|------|---------|
| payedSum май | ~11–12 mismatch после fix_payment_links live (1517 fixed, 186 skipped, 4 errors) | unmapped CO, linkedSum > payment sum |
| shippedSum май | 7 mismatch | unmapped demands, лишние NEW demands, unlink customerOrder не сработал (ВА-02552) |
| Лишние NEW заказы мая | 3 шт | fix_numbering — **не удалять** |

Повторная синхронизация **заблокирована** BLOCKER-1 (нужен OLD для demand/payment operations).

---

## BLOCKER-4: Catchup 2026-06-04

| | |
|---|---|
| **Статус** | Не выполнен |
| **Причина** | `catchup_today.py` → GET OLD entity/supply → 403 |
| **NEW-only инвентаризация** | Не проводилась в этом прогоне |

---

## BLOCKER-5: Stock reconcile

| | |
|---|---|
| **Статус** | `audit_stock_final.json` → BLOCKED_OLD_API |
| **Причина** | Нужен `report/stock/bystore` OLD |

---

## SUSPECT / UNMAPPED (июнь 2026, anchor-only audit)

- **UNMAPPED_NEW:** 23 документа — нет old_id (нет externalCode-uuid и нет записи в uuid_map). **10 из 23 — документы 2026-06-04** (catchup не выполнен): ВА-02640, ВА-02639, ЗБ-04414, ТУ-00890 и парные invoiceout; также ТУ-00889, АФ-00007 (июнь).
- **Moment+org auto-resolve:** пропущен (OLD list недоступен)

Детали: `audit_doc_pairs_suspect.csv`

---

## Артефакты прогона

| Файл | Статус |
|------|--------|
| `audit_doc_pairs_report.json` | Jun 2026: fixable=154, blockers=23; full H1 — см. log |
| `audit_agent_final.json` | BLOCKED_OLD_API |
| `sync_doc_agent_report.json` | blocked |
| `sync_order_fields_report.json` | blocked |
| `audit_stock_final.json` | BLOCKED_OLD_API |
| `catchup_jun04_report.json` | blocked (см. ниже) |
