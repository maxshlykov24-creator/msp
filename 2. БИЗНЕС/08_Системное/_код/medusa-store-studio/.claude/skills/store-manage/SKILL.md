---
name: store-manage
description: Управление магазином прямо из агента через Admin API — товары, варианты, цены, категории, склад, заказы. Массовое наполнение каталога из прайса/таблицы/описания.
whenToUse: Когда нужно завести/изменить товары, категории, цены, посмотреть заказы — без ручного клика в админке.
arguments: task
---

Выполни задачу по магазину: «**$task**». Работаешь через **Admin API** — все команды ниже проверены на живом Medusa v2.

## 1. Авторизация (получить токен)
```bash
TOKEN=$(curl -s -X POST http://localhost:9000/auth/user/emailpass \
  -H 'Content-Type: application/json' \
  -d '{"email":"<admin-email>","password":"<password>"}' \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['token'])")
```
Дальше во всех запросах: `-H "Authorization: Bearer $TOKEN"`.
🔒 Пароль/токен **не хардкодить** в файлы проекта и не коммитить — брать из окружения или спрашивать у пользователя.

## 2. Товары
**Прочитать:**
```bash
curl -s "http://localhost:9000/admin/products?limit=50" -H "Authorization: Bearer $TOKEN"
```
**Создать** (опции и варианты обязательны, цена — в **major-единицах**: 1990 = 1990 ₽):
```bash
curl -s -X POST http://localhost:9000/admin/products \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{
    "title": "Название товара",
    "description": "Описание",
    "status": "published",
    "options": [{"title": "Размер", "values": ["S","M","L"]}],
    "variants": [
      {"title":"M","options":{"Размер":"M"},"prices":[{"amount":1990,"currency_code":"rub"}]}
    ]
  }'
```
**Обновить:** `POST /admin/products/{id}` · **Удалить:** `DELETE /admin/products/{id}`
Картинки: поле `images: [{"url": "https://..."}]` и `thumbnail`.

## 3. Категории и коллекции
```bash
curl -s -X POST http://localhost:9000/admin/product-categories \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"Кроссовки","is_active":true}'
```
Привязка товара к категории — `category_ids` при создании/обновлении товара.

## 4. Массовое наполнение (главный сценарий)
Пользователь даёт прайс/таблицу/описание ассортимента → ты:
1. **Разбери** входные данные в список товаров (название, описание, опции, варианты, цены, категория).
2. **Покажи план** пользователю: сколько товаров, какие категории, диапазон цен — и дождись подтверждения. Массовое создание — необратимо-ish, не лей молча.
3. Создавай **пачками**, логируй прогресс («12/40 создано»).
4. При ошибке на товаре — не падай, собери список проблемных и покажи в конце.
5. В конце — сводка + ссылка на админку для проверки.

## 5. Заказы, склад, цены
- Заказы: `GET /admin/orders` (фильтры по статусу/дате), детали `GET /admin/orders/{id}`.
- Склад: `GET/POST /admin/inventory-items`, уровни остатков — `inventory-levels`.
- Регионы и валюты: `GET/POST /admin/regions`, `GET /admin/stores` (валюты магазина).

## 6. Правила
- **Цены в major-единицах** (1990 = 1990 ₽) — самая частая ошибка v2.
- Для рублёвых цен нужен **регион с валютой RUB** (см. `store-setup`), иначе цена не покажется на витрине.
- Перед массовыми/разрушительными операциями (удаление, перезапись цен) — **подтверждение пользователя**.
- Проверяй результат: после создания сделай `GET` и покажи, что реально появилось.
- Полный справочник эндпоинтов: `https://docs.medusajs.com/api/admin`.
