# Third-party credits & licenses

Среда опирается на стороннее ПО и материалы. Все — с разрешающей лицензией; при раздаче атрибуцию сохраняем.

## Ядро
- **Medusa v2** — MIT. Бэкенд, админка, официальный Next.js-стартер (`medusajs/dtc-starter`).
  https://github.com/medusajs/medusa
- **Next.js**, **Tailwind CSS** — MIT.

## Плагины для российского рынка (Gorgo)
Хаб: [gorgojs.ru](https://gorgojs.ru/) · доки: [docs.gorgojs.ru](https://docs.gorgojs.ru) · каталог плагинов, стартеры, кейсы.
Репозиторий `gorgojs/medusa-integrations` — **MIT** (лицензия в файле `LICENSE` каждого пакета; в части `package.json` поле `license` не заполнено).
https://github.com/gorgojs/medusa-integrations
Что брать под РФ и как выбирать банк — [`GORGO.md`](GORGO.md).

| Пакет | Назначение |
|---|---|
| `@gorgo/medusa-payment-yookassa` | ЮKassa: карты, СБП, SberPay, MirPay + чеки 54-ФЗ |
| `@gorgo/medusa-payment-tkassa` | Т-Касса (Т-Банк) + чеки 54-ФЗ |
| `@gorgo/medusa-payment-robokassa` | Robokassa |
| `@gorgo/medusa-fulfillment-apiship` | ApiShip: СДЭК, Почта России, Boxberry и др. |
| `@gorgo/medusa-feed-yandex` | YML-фид для Яндекс.Маркета |
| `@gorgo/medusa-1c` | Импорт товаров из 1С (🚧 в разработке) |

⚠️ Это **community-плагины**, не официальные продукты платёжных сервисов. Перед боевым запуском рекомендуется просмотреть код.

## Дизайн-референсы
- **Брендовые токены** в `store-design/references/brand-tokens/` — из [xjli360/awesome-design-md-ecommerce](https://github.com/xjli360/awesome-design-md-ecommerce) (**MIT**). Отобрано 8 файлов из 1832. Лицензия — в папке.
  Названия брендов и их фирменный стиль принадлежат правообладателям; файлы приведены как референс уровня и структуры токенов, а не как разрешение использовать чужую айдентику.
- Правила ecom-UX частично опираются на публичные исследования Baymard Institute (публичные выдержки) и требования **WCAG 2.2**.

## Прочее
Названия ЮKassa, Т-Банк, Robokassa, СДЭК, Почта России, Яндекс, 1С — товарные знаки их владельцев. Среда не аффилирована с ними.
