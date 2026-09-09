# Gorgo — живой источник интеграций Medusa под РФ

Хаб: [gorgojs.ru](https://gorgojs.ru/)  
Доки: [docs.gorgojs.ru](https://docs.gorgojs.ru)  
Каталог: [gorgojs.ru/medusa/plugins](https://gorgojs.ru/medusa/plugins) (на 2026-09-09: 145 плагинов, 9 стартеров, 2 кейса)  
Код: [github.com/gorgojs/medusa-integrations](https://github.com/gorgojs/medusa-integrations)  
Чат: [@gorgojs_chat](https://t.me/gorgojs_chat), [@medusajs_chat](https://t.me/medusajs_chat)

Снято в vault 2026-09-09. Перед стартом магазина **перечитать доки**, не этот файл: пакеты и путь установки меняются.

Среда, в которой лежит эта карта, пришла из ролика Николая Алексеева: [youtube.com/watch?v=w4L53M2xJjM](https://www.youtube.com/watch?v=w4L53M2xJjM).

## Как выбирать банк

Одна платёжка = все способы оплаты. СБП — рельс ЦБ, дотягивает почти до любого банка. Не подключать банки по одному.

| Если | Брать | Дока |
|---|---|---|
| Нет предпочтения, ИП / самозанятый | **ЮKassa** (дефолт скилла `payments-ru`) | [getting-started](https://docs.gorgojs.ru/medusa-plugins/yookassa/getting-started) |
| Расчётный счёт в Т-Банке | **Т-Касса** | [getting-started](https://docs.gorgojs.ru/medusa-plugins/t-kassa/getting-started) |
| Нужен агрегатор с тестовым режимом из коробки | **Robokassa** | [getting-started](https://docs.gorgojs.ru/medusa-plugins/robokassa/getting-started) |

Все три: карты, СБП, чеки 54-ФЗ (включить `useReceipt`), возвраты, вебхуки. Отдельную кассу покупать не нужно.

**Оптимальный путь на 2026-09-09:** ЮKassa через [Integration Module](https://docs.gorgojs.ru) — ключи в админке (`Settings → Integrations`), шифрование AES, без правки `.env` на каждый ключ. В живой доке ЮKassa это основной способ с 13.08.2026, пакеты `@gorgo/medusa-integration` + `@gorgo/medusa-payment-yookassa`, Medusa ≥ 2.17.2. Т-Касса и Robokassa в доке на 31.07.2026 ещё через переменные окружения — сверить на день старта.

Вебхуки (зарегистрировать в кабинете банка):

- ЮKassa: `/hooks/payment/yookassa_yookassa`
- Т-Касса: `/hooks/payment/tkassa_tkassa`
- Robokassa: `/hooks/payment/robokassa_robokassa`

После установки провайдера включить его в регионе, иначе на чекауте пусто. Тестовый платёж и живой чек обязательны до продаж.

## Что ещё есть у Gorgo

| Зона | Плагин | Статус в доке | Зачем |
|---|---|---|---|
| Доставка | [ApiShip](https://docs.gorgojs.ru/medusa-plugins/apiship) | готов, ≥ Medusa 2.14 | Одна интеграция → СДЭК, Почта, Boxberry, Яндекс, DPD и ещё ~40 служб |
| Маркет | [Yandex YML Feed](https://docs.gorgojs.ru/medusa-plugins/yandex-yml-feed/getting-started) | готов, ≥ 2.8 | Фиды в Яндекс.Маркет |
| 1С | [1C:Enterprise](https://docs.gorgojs.ru/medusa-plugins/1c-enterprise) | в разработке, дорожная карта от 25.08.2026 | Пока не обещать клиенту полную синхронизацию |
| Стартеры | [gorgojs.ru/medusa/starters](https://gorgojs.ru/medusa/starters) | 9 шт., DTC-стартер анонсирован 26.08.2026 | Готовый каркас витрины, не конструктор |
| Кейсы | [gorgojs.ru/medusa/cases](https://gorgojs.ru/medusa/cases) | 2 шт. | Живые магазины, не демо |
| Свой плагин | [create-medusa-plugin](https://docs.gorgojs.ru/en/tools/create-medusa-plugin) | CLI | Если готового пакета нет |

Это community-плагины (MIT), не продукты банков. Перед боем смотреть код и пинить версии.

## Как этим пользоваться в нашей заготовке

1. Магазин поднимать скиллами из этой папки (`RUNBOOK.md`).
2. Банк и доставку сверять с **этой страницей + живыми доками Gorgo**, не с памятью агента.
3. Скилл `payments-ru` в трёх копиях (`.agents/`, `.claude/`, `.kimi-code/`) — рабочий прогон. Хвост «integration не использовать» снят 2026-09-09: дока ЮKassa уже на модуле интеграций.
4. Каталог на gorgojs.ru шире трёх платёжек: перед кастомной интеграцией сначала искать готовый плагин.
