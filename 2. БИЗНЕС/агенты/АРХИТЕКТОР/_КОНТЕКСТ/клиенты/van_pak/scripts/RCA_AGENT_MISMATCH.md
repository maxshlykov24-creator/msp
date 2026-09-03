# RCA: расхождение agent и атрибутов заказов (Van Pak NEW↔OLD)

**Дата:** 2026-06-04/05  
**Статус:** частично — полный аудит заблокирован OLD API 403

---

## Кейс ТУ-00887 (spot-check NEW, 2026-06-05)

| Поле | OLD (из плана/диагностики 04.06) | NEW (факт API) |
|------|-------------------------------------|----------------|
| name | ТУ-00887 | ТУ-00887 |
| moment | 2026-06-02 10:56 | 2026-06-02 10:56 |
| agent | ООО «НПО ГЕОНАУКА» | ООО «ТЕХНО-ПОСМ» |
| Способ поставки | Доставка: Платная | **пусто** |
| externalCode (NEW) | — | `c693c3bf-5e58-11f1-0a80-0d5800161131` (= OLD uuid) |
| uuid_map | `c693c3bf…` → `f85b6c38…` | **пара в map совпадает** |

**Вывод по паре:** якорь migrate (externalCode = OLD id + uuid_map) **корректен**. Agent и атрибуты на NEW **не соответствуют OLD** — ошибка переноса/синхронизации полей, не перепутанная пара по name.

---

## Механизм при миграции (`migrate.py`)

```python
new_agent = umap.get("counterparty", old_agent_uuid)
body["agent"] = meta(.../counterparty/{new_agent})
```

Agent на документе = counterparty из uuid_map для **конкретной пары OLD↔NEW**.

| # | Причина | Вероятность для ТУ-00887 |
|---|---------|--------------------------|
| A | Неверная пара в uuid_map | **Низкая** — externalCode + map сходятся |
| B | CP не в map | Низкая — agent не пустой, но **чужой** |
| C | Атрибуты не перенесены (attr/customentity) | **Подтверждено** — 4 поля пустые |
| D | fix_numbering / UI после migrate | Низкая для agent |
| E | catchup с другим телом | Возможна для июньских docs |

**Наиболее вероятно для agent:** при POST/PUT документа agent подставился из **другого контекста** (неверный old body при создании, или повторный catchup без перечитывания OLD agent). Для атрибутов — **C**: `attr_customerorder` / `customentity_value` не применены при migrate или sync.

---

## Масштаб (факт на момент прогона)

| Метрика | Значение | Источник |
|---------|----------|----------|
| OLD API | **403** (code 1061) с 2026-06-05 ~00:35 MSK | probe `entity/organization` |
| Июнь 2026 pairs (anchor-only) | fixable **154**, blockers **23** | `audit_doc_pairs_report.json` (Jun filter) |
| **H1 2026 pairs (anchor-only)** | fixable **7376**, blockers **23** (UNMAPPED_NEW) | `audit_doc_pairs_report.json` 2026-01-01..06-30 |
| Agent mismatch count | **не измерен** — OLD недоступен | `audit_agent_final.json` → BLOCKED |
| payedSum/shippedSum (ранее) | payedSum ~11–12 mismatch мая; shippedSum 7 | prior session / `sync_demand_links_report.json` |

**Оценка «везде ли agent»:** точный % возможен только после восстановления OLD API и `audit_doc_agent.py --final`. По механизму migrate — **кластеры риска:** customerorder, demand, paymentin, catchup-документы; не все 1748 заказов.

---

## Что сделано в этом прогоне

- Скрипты: `ms_common.py`, `audit_doc_pairs.py`, `audit_doc_agent.py`, `sync_doc_agent_from_old.py`, `sync_order_fields_from_old.py`, `audit_stock.py`, `reconcile_stock.py`, `run_night_pipeline.sh`
- Pair audit в **anchor-only** режиме (без OLD fetch) — класс TRUSTED по externalCode + uuid_map
- Sync agent/attrs/stock/catchup — **не выполнен** (BLOCKER OLD API)

---

## Действия после восстановления OLD API

1. `export OLD_TOKEN=...` (новый токен i.korneva@wangpack.ru)
2. `python3 audit_doc_pairs.py --from-date 2026-01-01 --to-date 2026-06-30` (полная верификация moment+org+sum)
3. `python3 audit_doc_agent.py --final` → mismatch count
4. `sync_doc_agent_from_old.py` + `sync_order_fields_from_old.py` dry→live (май+июнь → весь 2026)
5. `sync_demand_links.py` + `fix_payment_links.py` + catchup `2026-06-04`
6. `audit_stock.py` + `reconcile_stock.py`
