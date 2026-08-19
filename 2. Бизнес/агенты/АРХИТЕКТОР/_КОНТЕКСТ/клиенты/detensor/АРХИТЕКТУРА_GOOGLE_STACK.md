# DETENSOR Analytics — Архитектура на Google Stack

**Клиент:** Волков (DETENSOR / Spine-Shop)  
**Версия:** 1.0 · Май 2026  
**Статус:** Проектирование  

---

## 0. ВЫБОР СТЕКА — ПОЧЕМУ GOOGLE

| Критерий | PHP/MySQL/VPS (ТЗ v2.7) | **Google Stack** |
|---|---|---|
| Инфраструктура | VPS, CI/CD, бэкапы, SSL | **Ноль. Google держит всё.** |
| Стоимость | ~2–5 k₽/мес за VPS | **Бесплатно** (в пределах лимитов GA) |
| Dashboard | Статический HTML → рендер вручную | **Looker Studio — нативно, drag-and-drop** |
| Надёжность доставки | Нужна очередь + idempotency вручную | **Apps Script + Sheets — транзакционно** |
| Доступ заказчика к данным | Только через дашборд | **Google Sheets — читает/редактирует сам** |
| Время до MVP | 4–6 нед | **2–3 нед** |
| Масштаб Detensor | ~100–200 сделок/мес | **Sheets до 500k строк — запас на 20 лет** |

**Вывод:** Google Stack закрывает 100% задач Detensor без единой строчки инфра-кода.

---

## 1. ЦЕЛЕВАЯ СХЕМА (TO-BE)

```
amoCRM ──webhook (POST)──────► Apps Script Web App
                                       │  dedup + validate + transform
МойСклад ──Apps Script trigger──►──────┤  (каждые 15 мин — pull API)
                                       │
                                       ▼
                            ┌─────────────────────────┐
                            │   Google Sheets          │
                            │  (единственная БД)       │
                            │                          │
                            │  📄 leads                │
                            │  📄 lead_items           │
                            │  📄 contacts             │
                            │  📄 products             │
                            │  📄 stock                │
                            │  📄 plans  ← заказчик   │
                            │  📄 webhook_log          │
                            └────────────┬────────────┘
                                         │ нативный коннектор
                                         ▼
                              ┌──────────────────────┐
                              │   Looker Studio       │
                              │   (дашборд)           │
                              │                       │
                              │  📊 Сводная           │
                              │  📊 Обращения         │
                              │  📊 Заказы            │
                              │  📊 Аренда            │
                              │  📊 Пробная           │
                              │  📊 Товары            │
                              │  📊 Профили           │
                              │  📊 Отказы            │
                              │  📊 Менеджеры         │
                              └──────────┬───────────┘
                                         │
                              analytics.detensor.ru
                              (iframe embed или прямая ссылка)
```

---

## 2. СЛОЙ ДАННЫХ — GOOGLE SHEETS

### 2.1. Структура книги (один Spreadsheet, 8 листов)

#### `leads` — все сделки amoCRM
```
lead_id | pipeline_id | pipeline_name | stage_id | stage_methodology
old_stage_id | responsible_user_id | price | budget_amo
route | route_id | parent_lead_id | deal_type
is_rental | is_buyout | buyout_amount | rental_amount
field_age | field_gender | field_weight | field_height
field_diagnosis | field_spine | field_symptom | field_for_whom
field_source | channel_id | channel_name | utm_source | utm_campaign
tags | segment | created_at | updated_at | closed_at | synced_at
```

#### `lead_items` — товары в сделках (связка с МС)
```
lead_id | ms_order_id | article | product_name
category | lineup | stiffness | length_cm
qty | price_sale | price_cost | sum_sale | sum_cost
```

#### `contacts` — контакты amoCRM
```
contact_id | lead_ids | name | phone | email | created_at
```

#### `products` — каталог МойСклад (128 SKU)
```
ms_id | article | name | category | lineup
stiffness | length_cm | price_sale | price_cost | archived | synced_at
```

#### `stock` — остатки МойСклад
```
article | ms_id | qty | synced_at
```

#### `plans` — план продаж (**заполняет заказчик руками**)
```
year | month | manager_id | manager_name
plan_new | plan_qualified | plan_proposed | plan_assembly | plan_paid
plan_revenue | week_dist_json
```

#### `webhook_log` — idempotency + дебаг
```
event_id | lead_id | event_type | pipeline_id | stage_id
received_at | processed | error
```
> Формирует `event_id = sha256(lead_id + updated_at + event_type)` — дубли отклоняются.

#### `ms_sync_log` — лог синхронизации каталога и остатков
```
sync_type | started_at | finished_at | rows_updated | error
```

---

## 3. СЛОЙ ПРИЁМА — APPS SCRIPT

### 3.1. Webhook от amoCRM (`doPost`)

```javascript
// Точка входа: POST https://script.google.com/…/exec?key=SECRET
function doPost(e) {
  const SECRET = PropertiesService.getScriptProperties().getProperty('WEBHOOK_SECRET');
  if (e.parameter.key !== SECRET) return respond(403, 'Forbidden');

  const body = parseAmoCRMWebhook(e.postData.contents);
  const lead = body?.leads?.status?.[0];
  if (!lead) return respond(200, 'no lead');

  // Фильтры (из ТЗ раздел 6.3)
  if (!isRetailChannel(lead)) return respond(200, 'skipped: channel');
  if (!isAllowedManager(lead)) return respond(200, 'skipped: manager');
  if (!isAllowedPipeline(lead)) return respond(200, 'skipped: pipeline');
  if (isOwnerDeal(lead)) return respond(200, 'skipped: owner_deal');

  // Idempotency
  const eventId = sha256(lead.id + lead.updated_at + body.event_type);
  if (isAlreadyProcessed(eventId)) return respond(200, 'duplicate');

  upsertLead(lead);         // → лист leads
  markProcessed(eventId);   // → лист webhook_log

  return respond(200, 'ok');
}
```

### 3.2. Pull МойСклад (триггер каждые 15 мин)

```javascript
function syncMoySklad() {
  const token = PropertiesService.getScriptProperties().getProperty('MS_TOKEN');
  const headers = { Authorization: `Bearer ${token}` };

  // Каталог — 1 раз в сутки (23:00)
  if (isNightSync()) {
    const products = fetchMS('/api/remap/1.2/entity/product?filter=archived=false', headers);
    upsertProducts(products); // → лист products
  }

  // Остатки — каждые 15 мин
  const stock = fetchMS('/api/remap/1.2/report/stock/all', headers);
  replaceStock(stock); // → лист stock

  // Заказы — каждые 15 мин (для маржи по demand)
  const orders = fetchMS('/api/remap/1.2/entity/customerorder?filter=state=Completed', headers);
  upsertOrderItems(orders); // → лист lead_items
}
```

### 3.3. Backfill amoCRM (триггер каждые 30 мин)

Страховка от потери webhook — догоняет последние изменённые сделки:

```javascript
function backfillLeads() {
  const lastSync = getLastSyncTs(); // из webhook_log
  const leads = fetchAmo(`/api/v4/leads?filter[updated_at][from]=${lastSync}`);
  leads.forEach(lead => {
    if (passesFilters(lead)) upsertLead(lead);
  });
}
```

---

## 4. DASHBOARD — LOOKER STUDIO

### 4.1. Источники данных

| Лист Sheets | Data source в Looker Studio | Назначение |
|---|---|---|
| `leads` | `DS_leads` | Воронки, конверсии, менеджеры |
| `lead_items` | `DS_items` | Товарная аналитика, маржа |
| `products` | `DS_products` | Каталог, остатки |
| `stock` | `DS_stock` | Остатки в реальном времени |
| `plans` | `DS_plans` | План vs факт |
| `contacts` | `DS_contacts` | Профили клиентов |

### 4.2. Вычисляемые поля (Calculated Fields) в Looker Studio

```
conversion_qual = COUNTIF(stage_methodology = "Квалифицировано") / COUNTIF(stage_methodology = "Заявка получена")

conversion_paid = COUNTIF(stage_id = 142) / COUNTIF(stage_methodology = "Заявка получена")

revenue_direct = SUM(IF(route_id IN [1810685, 1810687, 1810689], price, 0))

deal_margin = SUM(sum_sale - sum_cost) / SUM(sum_sale)

avg_check = SUM(IF(stage_id = 142, price, 0)) / COUNTIF(stage_id = 142)
```

### 4.3. Вкладки дашборда (→ страницы отчёта Looker Studio)

| Страница | Источники | Ключевые блоки |
|---|---|---|
| **Сводная** | DS_leads + DS_plans | KPI 6 метрик, воронка методологии, план/факт, источники заявок |
| **Обращения** | DS_leads | Этапы, маршрутизация (Покупка / Аренда / Пробная / ОПТ), конверсия по менеджерам |
| **Заказы** | DS_leads | Аналог Обращений для Spine-Shop |
| **Аренда** | DS_leads | Аренда / выкупы / продления, длина аренды |
| **Пробная** | DS_leads | Запись → явка → покупка после |
| **Товары** | DS_items + DS_products + DS_stock | 11 категорий, жёсткость/длина, ABC-анализ, остатки |
| **Профили** | DS_leads + DS_contacts | Анкета: возраст/пол/вес/диагноз/источник |
| **Отказы** | DS_leads | 17 причин отказа, по воронке, по менеджеру |
| **Менеджеры** | DS_leads + DS_plans | Plan vs Fact каждого, активность, конверсия |

### 4.4. Публикация

- Looker Studio Report → **«Опубликовать в интернете»** → встроить iframe на `analytics.detensor.ru`
- Или: CNAME → прямая ссылка Looker Studio (без iframe, браузерная аутентификация)
- Доступ: заказчик получает Google-аккаунт с ролью **Viewer** — нет нужды в логин-системе

---

## 5. КРИТИЧЕСКИЕ РЕШЕНИЯ

### 5.1. Источник правды по деньгам
**Принято:** МойСклад `customerorder` → `demand` (закрытая отгрузка) через поле `ID Заказа МС` (Field ID 3007551 в amoCRM).  
`lead_items` содержит `sum_sale` и `sum_cost` → маржа точная, не из `buyPrice` карточки.

### 5.2. Аренда + Выкуп
Детектируем в Apps Script:
```javascript
function detectDealType(leadItems) {
  const hasService = leadItems.some(i => i.article.startsWith('rent28_'));
  const hasGoods   = leadItems.some(i => i.article.startsWith('D18H'));
  if (hasService && hasGoods) return 'rent_with_buyout';
  if (hasService) return 'rent_only';
  return 'direct_sale';
}
```
Поле `deal_type` в листе `leads` → фильтр в Looker Studio.

### 5.3. Маршрутизация без задвоения
- В amoCRM: при создании дочерней сделки (Аренда / Пробная) — прописывается поле `parent_lead_id`.
- В Sheets: `revenue_direct` считается только по сделкам где `route IN (Покупка, Рассрочка)` **ИЛИ** `parent_lead_id IS NULL`.
- Дочерняя воронка считает свою выручку независимо.

### 5.4. Технические этапы → маппинг (100% покрытие)

| Stage ID | Pipeline | → Методология |
|---|---|---|
| 72685162, 82112312 | Обращения, Заказы | Заявка получена |
| 72685174, 72685514, … | Обращения | Предложение отправлено |
| 72799278, 72800674 | Обращения, Заказы | Передан на сборку |
| 142 | все | Оплата получена |
| 143 | все | Закрыто (потеря) |
| 72685410, 73111134 | Обращения, Заказы | Потеря: Недозвон |
| 73111074, 73111130 | Обращения, Заказы | Потеря: Бот |

Хранится в листе `stage_map` — обновляется без деплоя.

### 5.5. Безопасность
- webhook URL содержит `?key=SECRET` (secret 32 символа)
- Apps Script `doPost` сразу возвращает 403 без обработки при неверном key
- IP whitelist amoCRM не нужен (Apps Script не умеет) — заменяется secret
- Токены amoCRM и МС — в `PropertiesService` (зашифровано Google), не в коде
- Ротация токенов — напоминание в Google Calendar каждые 90 дней

---

## 6. СКРИПТ ЗАГРУЗКИ КАТАЛОГА МС (CSV → Sheets)

Вместо ручного заполнения 128 SKU:

```
1. МойСклад → Товары → Экспорт CSV
2. Открыть в Excel/Sheets → заполнить 4 доп. колонки:
   Жёсткость | Длина, см | Категория для аналитики | Линейка
3. Загрузить обратно скриптом:
   python scripts/import_catalog_to_ms.py --file catalog_filled.csv
   (аналог import_full_loyalty_csv_v2.py у MartaChe)
4. Apps Script подтягивает каталог в Sheets в ту же ночь
```

Скрипт за ~100 строк Python → `PATCH /api/remap/1.2/entity/product/{id}`.

---

## 7. ФАЗЫ ПРОЕКТА

### Фаза 0 — Подготовка (1–2 дня)
- [ ] Создать Google Workspace аккаунт или использовать личный аккаунт заказчика
- [ ] Создать Spreadsheet «DETENSOR Analytics DB» → 8 листов по структуре §2
- [ ] Создать Apps Script проект, привязать к Spreadsheet
- [ ] Вписать секреты: `WEBHOOK_SECRET`, `AMO_TOKEN`, `MS_TOKEN`
- [ ] Настроить триггеры: `doPost` → webhook; `syncMoySklad` → каждые 15 мин; `backfillLeads` → каждые 30 мин

### Фаза 1 — Каталог МС (2–3 дня)
- [ ] Специалист МС: создать 4 доп. поля (Жёсткость, Длина, Категория, Линейка)
- [ ] Экспорт → заполнить CSV → импорт скриптом
- [ ] Проверить: 128 SKU в листе `products` с полными атрибутами
- [ ] Миграция полей анкеты: проверить старые ID (§4.4 ТЗ), скрипт `backfill_old_fields.py`

### Фаза 2 — Backend MVP (3–4 дня)
- [ ] Задеплоить Apps Script Web App → получить URL webhook
- [ ] Настроить webhook в amoCRM (5 событий, URL + `?key=SECRET`)
- [ ] Тест: создать тестовую сделку → убедиться что строка появилась в `leads`
- [ ] Тест backfill: изменить сделку, убить webhook → убедиться что backfill догнал
- [ ] Проверить dedup: отправить один webhook дважды → одна строка в БД

### Фаза 3 — Looker Studio MVP (3–4 дня)
- [ ] Создать отчёт Looker Studio → подключить 6 data sources
- [ ] Построить 9 страниц по §4.3
- [ ] Вычисляемые поля §4.2
- [ ] Встроить iframe или CNAME на analytics.detensor.ru

### Фаза 4 — Точная маржа (1–2 дня)
- [ ] Apps Script: pull `demand` → `lead_items.price_cost` по реальным документам МС
- [ ] Looker Studio: добавить виджет P&L с gross margin

### Фаза 5 — Polish (1 день)
- [ ] Проверить все 7 воронок × тестовые сделки
- [ ] Передать заказчику доступ Viewer
- [ ] Обучение: как читать дашборд (30 мин Google Meet)

**Итого: 11–16 рабочих дней (2–3 нед)**

---

## 8. РОЛИ И КОМУ ЧТО

| Исполнитель | Задачи |
|---|---|
| **Специалист amoCRM** | Webhook настройка, 13 полей, токен, тест |
| **Специалист МС** | 4 доп. поля, переименование 50 SKU, архив 7 SKU, API-токен |
| **Разработчик (Apps Script)** | Скрипт webhook + pull МС + backfill + dedup + CSV-импортёр |
| **Разработчик (Looker Studio)** | 9 страниц отчёта, calculated fields, embed |
| **Заказчик** | Заполняет `plans`, даёт токены, финальная приёмка |

> Разработчик = один человек, ~16–20 часов работы суммарно.

---

## 9. OPEN QUESTIONS (до старта)

| # | Вопрос | Влияет на |
|---|---|---|
| 1 | Деньги: `lead.price` или `МС demand`? | Фаза 4, точность маржи |
| 2 | Аренда+Выкуп: оставить «один лид» или парная сделка? | §5.2, сложность backend |
| 3 | Профиль клиента: по последней сделке или агрегат по всем? | Вкладка «Профили» |
| 4 | Дашборд доступен всем по ссылке или только с Google-аккаунтом? | Настройка доступа Looker Studio |
| 5 | Заказчик хочет брендирование домена `analytics.detensor.ru`? | iframe vs CNAME |

---

*Автор: @архитектор (MS Product) · Май 2026*
