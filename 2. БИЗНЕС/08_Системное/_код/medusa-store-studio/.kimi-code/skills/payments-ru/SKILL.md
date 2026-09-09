---
name: payments-ru
description: Подключает российские платёжки к Medusa — ЮKassa, Т-Касса (Т-Банк), Robokassa — с фискальными чеками 54-ФЗ, СБП и вебхуками. Конфиги проверены по докам и исходникам.
whenToUse: Когда магазину нужен приём оплаты и фискальные чеки.
arguments: provider
---

Подключи оплату (**$provider** — если не указано, спроси или предложи ЮKassa) через плагины **Gorgo** (MIT, репозиторий `gorgojs/medusa-integrations`, активная разработка).

## Что выбрать
| Провайдер | Пакет | Когда брать |
|---|---|---|
| **ЮKassa** | `@gorgo/medusa-payment-yookassa` | Дефолт. Карты, **СБП**, SberPay, MirPay. Простое подключение для ИП/самозанятых |
| **Т-Касса** (Т-Банк) | `@gorgo/medusa-payment-tkassa` | Если расчётный счёт в Т-Банке — лучшие ставки. Карты, СБП, T-Pay, Apple/Google/SberPay/AlfaPay |
| **Robokassa** | `@gorgo/medusa-payment-robokassa` | Агрегатор, есть тестовый режим из коробки |

> **Одна интеграция = все способы оплаты.** СБП — это рельс ЦБ, он достаёт практически до любого банка. Не нужно подключать «все банки» по отдельности.

## 🧩 Магазин интеграций (рекомендуемый путь с 31.07.2026)

`@gorgo/medusa-integration` — расширение админки, через которое интеграции ставятся и настраиваются **из интерфейса**, а не правкой конфига и `.env`. Ключи хранятся зашифрованными (AES-256-GCM), мерчант вводит их сам в `Settings → Integrations`.

```bash
yarn add @gorgo/medusa-integration @gorgo/medusa-payment-tkassa@beta
# или: npm install @gorgo/medusa-integration @gorgo/medusa-payment-tkassa@beta
```

`medusa-config.ts` — Т-Касса как пример, остальные подключаются так же:
```ts
const TKASSA_INTEGRATION_ID = "tkassa-1"

module.exports = defineConfig({
  plugins: [
    {
      resolve: "@gorgo/medusa-integration",
      options: {
        encryptionKey: process.env.INTEGRATION_ENCRYPTION_KEY,
        providers: [
          {
            resolve: "@gorgo/medusa-payment-tkassa/providers/integration-tkassa",
            id: TKASSA_INTEGRATION_ID,
            options: {},
          },
        ],
      },
    },
    { resolve: "@gorgo/medusa-payment-tkassa", options: {} },
  ],
  modules: [
    {
      resolve: "@medusajs/medusa/payment",
      options: {
        providers: [
          {
            resolve: "@gorgo/medusa-payment-tkassa/providers/payment-tkassa",
            id: "tkassa",
            options: { id: TKASSA_INTEGRATION_ID },
          },
        ],
      },
    },
  ],
})
```

`.env`: `INTEGRATION_ENCRYPTION_KEY=<длинная случайная строка>` — ею шифруются ключи мерчанта в БД. Сгенерировать: `openssl rand -hex 32`. **Потеряешь ключ — расшифровать сохранённые доступы не сможешь.**

**Три места, где легко ошибиться:**
1. Провайдер регистрируется **дважды** — как `integration-tkassa` в плагине (админ-интерфейс) и как `payment-tkassa` в модуле оплаты (сам приём платежей). Оба нужны.
2. `TKASSA_INTEGRATION_ID` должен **совпадать** в обоих местах — через него платёжный провайдер находит сохранённые ключи.
3. Пакет ставится с тегом **`@beta`** — интеграционная версия ещё не в `latest`.

После старта: `Settings → Integrations` в админке → выбрать Т-Кассу → ввести `terminalKey` и пароль → включить. Дальше как обычно — привязать провайдера к региону.

Требует Medusa **≥2.17.2**. Пакеты вышли 31.07.2026 — свежие, на проде проверять до запуска продаж.

## ЮKassa (классический путь — ключи в конфиге)
```bash
yarn add @gorgo/medusa-payment-yookassa
```
`medusa-config.ts`:
```ts
modules: [{
  resolve: "@medusajs/medusa/payment",
  options: { providers: [{
    resolve: "@gorgo/medusa-payment-yookassa/providers/payment-yookassa",
    id: "yookassa",
    options: {
      shopId: process.env.YOOKASSA_SHOP_ID,
      secretKey: process.env.YOOKASSA_SECRET_KEY,
      capture: true,                 // true = одностадийно; false = холдирование
      useReceipt: true,              // ← чеки 54-ФЗ
      useAtolOnlineFFD120: true,     // ФФД 1.2 (иначе 1.05)
      taxSystemCode: 1,              // система налогообложения
      taxItemDefault: 1,             // ставка НДС для товаров
      taxShippingDefault: 1          // ставка НДС для доставки
    }
  }]}
}]
```
`.env`: `YOOKASSA_SHOP_ID`, `YOOKASSA_SECRET_KEY`.
**Вебхук:** `https://<домен>/hooks/payment/yookassa_yookassa` — зарегистрировать вручную в кабинете ЮKassa (Интеграция → HTTP-уведомления).

## Т-Касса
```bash
yarn add @gorgo/medusa-payment-tkassa
```
```ts
{ resolve: "@gorgo/medusa-payment-tkassa/providers/payment-tkassa", id: "tkassa",
  options: { terminalKey: process.env.TKASSA_TERMINAL_KEY, password: process.env.TKASSA_PASSWORD,
    capture: true, useReceipt: true, ffdVersion: "1.05",
    taxation: "osn", taxItemDefault: "none", taxShippingDefault: "none" } }
```
Вебхук: `/hooks/payment/tkassa_tkassa`.

## Robokassa
```bash
yarn add @gorgo/medusa-payment-robokassa
```
```ts
{ resolve: "@gorgo/medusa-payment-robokassa/providers/payment-robokassa", id: "robokassa",
  options: { merchantLogin: process.env.ROBOKASSA_MERCHANT_LOGIN,
    hashAlgorithm: process.env.ROBOKASSA_HASH_ALGORITHM,
    password1: process.env.ROBOKASSA_PASSWORD_1, password2: process.env.ROBOKASSA_PASSWORD_2,
    testPassword1: process.env.ROBOKASSA_TEST_PASSWORD_1, testPassword2: process.env.ROBOKASSA_TEST_PASSWORD_2,
    capture: false, isTest: true } }   // isTest: true — начинать с тестового режима
```
Вебхук: `/hooks/payment/robokassa_robokassa`.

## ⚠️ Обязательный шаг после установки
**Провайдер надо включить в регионе**, иначе на чекауте его не будет.

Через админку: **Settings → Regions → <регион> → Payment Providers**.
Через API (проверено на живом стенде):
```bash
# ID провайдера = pp_<id-из-конфига>_<id-из-конфига>, напр. pp_yookassa_yookassa
curl -X POST "$MEDUSA/admin/regions/$REGION_ID" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"payment_providers":["pp_yookassa_yookassa"]}'

# проверить — связь НЕ видна без явного запроса полей:
curl "$MEDUSA/admin/regions/$REGION_ID?fields=%2Bpayment_providers.*" -H "Authorization: Bearer $TOKEN"
```
⚠️ Две грабли, пойманные руками:
1. Эндпоинта `GET /admin/payment-providers` **не существует** (404). Список зарегистрированных провайдеров смотреть в БД (`select id, is_enabled from payment_provider;`) или в админке.
2. Ответ на обновление региона возвращает `payment_providers: []`, даже когда привязка прошла успешно — это не ошибка, просто связь не раскрывается без `fields=+payment_providers.*`. Не паникуй и не привязывай повторно.

**Проверено:** плагин ЮKassa 1.0.5 корректно загружается в Medusa **2.18.0**, провайдер появляется как `pp_yookassa_yookassa` (`is_enabled = true`), регион с валютой RUB создаётся и связывается без ошибок.

## 📜 Про 54-ФЗ (не пропускать)
Приём онлайн-оплат от ИП/юрлица требует фискальных чеков в ФНС. Хорошая новость: **отдельную кассу покупать/арендовать не нужно** — чеки формирует сам платёжный сервис при `useReceipt: true`. Настроить: систему налогообложения и ставки НДС (коды — в документации провайдера, зависят от режима налогообложения).
**Возвраты тоже фискализируются** — плагины это умеют. Не запускай продажи без проверенных чеков.

## Как устроена безопасность вебхука (проверено по исходникам)
Плагин **не верит телу вебхука**: берёт ID платежа и **перезапрашивает статус у API** провайдера авторизованным вызовом, сверяя результат. Подделать «оплачено» нельзя. Криптоподписи при этом нет — если нужен дополнительный слой, ставь IP-allowlist на уровне nginx.

## Проверка
1. Тестовый магазин провайдера → тестовый платёж на витрине.
2. Заказ появился в админке в статусе оплачен.
3. Чек пришёл покупателю и ушёл в ФНС.
4. Прогони возврат — проверь чек возврата.
5. Сабагент `store-qa`.

## Заметки
- Пакеты MIT, но у части в `package.json` не указано поле `license` (лицензия — в файле `LICENSE` пакета).
- Пинить точные версии, не `^`.
- Официальных плагинов от самих платёжных сервисов не существует — это единственные готовые варианты.
- Живой источник: [`GORGO.md`](../../../GORGO.md) и https://docs.gorgojs.ru. ЮKassa с 13.08.2026 ставится через `@gorgo/medusa-integration` (ключи в админке). Т-Касса в доке на 31.07.2026 ещё через `.env`. Перед стартом перечитать доки.
