---
name: shipping-ru
description: Подключает доставку и внешние интеграции для РФ — ApiShip (СДЭК, Почта России, Boxberry, Яндекс и ещё 40+ служб), YML-фид для Яндекс.Маркета, импорт товаров из 1С.
whenToUse: Когда магазину нужна доставка по России или выгрузка/импорт во внешние системы.
arguments: task
---

Подключи доставку/интеграцию: «**$task**». Плагины **Gorgo** (MIT).

## 🚚 ApiShip — доставка (одна интеграция = 40+ служб)
Покрывает СДЭК, Почту России, Boxberry, Яндекс Доставку, Деловые Линии, ПЭК, СберЛогистику, DPD и другие.
```bash
yarn add @gorgo/medusa-fulfillment-apiship
```
`medusa-config.ts`:
```ts
modules: [{
  resolve: "@medusajs/medusa/fulfillment",
  options: { providers: [{
    resolve: "@gorgo/medusa-fulfillment-apiship/providers/fulfillment-apiship",
    id: "apiship", options: {}
  }]}
}],
plugins: [{ resolve: "@gorgo/medusa-fulfillment-apiship", options: {} }]
```
⚠️ **Env-переменных нет** — вся настройка после установки **в админке**: `Settings → ApiShip` (API-токен, режим, адрес отправителя, наложенный платёж, ставки НДС, габариты по умолчанию, подключение конкретных служб).
⚠️ В README пакета **опечатка в команде установки** (`medusa-payment-apiship`) — правильно `medusa-fulfillment-apiship`.

После настройки: включить провайдера в **Settings → Regions → <регион> → Shipping** и завести варианты доставки.

## 🛒 YML-фид для Яндекс.Маркета
```bash
yarn add @gorgo/medusa-feed-yandex
```
```ts
modules: [
  { resolve: "@gorgo/medusa-feed-yandex/modules/feed" },
  { resolve: "@medusajs/medusa/file", options: { providers: [{
      resolve: "@medusajs/medusa/file-local", id: "local",
      options: { upload_dir: "static", backend_url: "http://localhost:9000/static" } }]}}
],
plugins: [{ resolve: "@gorgo/medusa-feed-yandex", options: {} }]
```
⚠️ **Единственный плагин со своей миграцией** — после установки обязательно:
```bash
yarn db:migrate
```
В проде локальный файловый провайдер заменить на S3-совместимый.

## 🏭 1С:Предприятие — импорт товаров
```bash
yarn add @gorgo/medusa-1c
```
```ts
plugins: [{ resolve: "@gorgo/medusa-1c", options: {} }]
```
🚧 **Плагин в разработке (v0.1.x).** Умеет только **импорт товаров и предложений** (`import.xml`, `offers.xml`) из 1С в Medusa.
**Пока НЕ умеет:** синхронизацию остатков и цен, импорт/экспорт заказов, админ-интерфейс.
→ Обещать клиенту «полную интеграцию с 1С» **нельзя**. Честно: «односторонний импорт каталога, остальное дорабатывается».

## Проверка
- Доставка появилась на чекауте, стоимость считается, ПВЗ выбирается.
- Заказ уходит в службу доставки, трек-номер возвращается.
- YML-фид отдаётся по URL и проходит валидацию Яндекс.Маркета.
- Затем — сабагент `store-qa`.
