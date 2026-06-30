# Detensor

> Проект в работе. Дополни суть, контакт и статус по мере внедрения.

## Данные и снимки API

Снимки МойСклад (SQLite: заказы, системный канал `salesChannel`): [`../../../99_Системное/clients/Detensor/data/`](../../../99_Системное/clients/Detensor/data/)

Скрипты выгрузки канала (`salesChannel`):

- Заказы покупателя: [`../../../99_Системное/ms_snapshot_orders_sales_channel.py`](../../../99_Системное/ms_snapshot_orders_sales_channel.py) → `orders_sales_channel.sqlite` / `.txt`
- Отгрузки (`demand`): [`../../../99_Системное/ms_snapshot_demands_sales_channel.py`](../../../99_Системное/ms_snapshot_demands_sales_channel.py) → `demands_sales_channel.sqlite` / `.txt`

Файлы в `data/` — **архив снимка** «как было»; их не правим при массовых правках в МойСклад. Нормализация каналов в облаке: [`../../../99_Системное/ms_normalize_sales_channels_moysklad.py`](../../../99_Системное/ms_normalize_sales_channels_moysklad.py) (`--mode report`, затем `--mode apply`, нужен `MS_TOKEN`). Поэтапно, например только опт: `--only-target ОПТ`.

## amoCRM (spineshop)

Скрипт создания полей «Причина отказа»: [`../../../../detensor/amo_create_refusal_fields.py`](../../../../detensor/amo_create_refusal_fields.py)

Созданы **2026-05-20** (тип «Список», сделки):

| Поле | field_id | Воронка (UI) | pipeline_id |
|------|----------|--------------|-------------|
| Причина отказа (Обращения) | `3033068` | Обращения | `9020986` |
| Причина отказа (Пробная) | `3033070` | Пробная процедура | `9849838` |
| Причина отказа (Аренда) | `3033072` | Аренда | `9033890` |

Полный список enum id: [`../../../../detensor/amo_audit_out/refusal_fields_created.json`](../../../../detensor/amo_audit_out/refusal_fields_created.json)

**Вручную в amoCRM:** Настройки → Поля сделки → для каждого поля оставить видимость только в своей воронке.
