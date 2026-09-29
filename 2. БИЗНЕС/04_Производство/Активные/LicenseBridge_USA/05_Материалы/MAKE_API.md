# Make API, как вызывать

Источник: [документация Make API](https://developers.make.com/api-documentation). Записано 2026-09-29 по зоне аккаунта Павла.

Ключ в файлы и в чат не копировать. Заголовок только `Authorization: Token <ключ>`. Схема `Bearer` для этого ключа не работает. Ключ MCP это другой доступ: им сценарии не читаются.

## База

`https://{зона}/api/v2/...`

Зону брать из адреса кабинета или из `GET /organizations` (`zone`). У Павла это `us2.make.com`, организация `8108062`, команда `2465749`. Регистр пути важен: `/organizations`, не `/Organizations`.

Лимит запросов зависит от тарифа (Core 60 в минуту, Pro 120, Teams 240, Enterprise 1000). Сверх лимита ответ `429`. Свой лимит виден в `GET /organizations/{id}`, поле `license.apiLimit`. Вызов `POST /scenarios/{id}/run` этим лимитом не ограничен. Если организация на паузе из-за лимита операций, `/run` вернёт `429` и код `IM310`.

Пагинация: `pg[limit]`, `pg[offset]`, `pg[sortBy]`, `pg[sortDir]` (`asc` или `desc`).

## Сценарии

| Действие | Вызов | Scope |
|---|---|---|
| Список | `GET /scenarios?teamId=` | `scenarios:read` |
| Карточка без blueprint | `GET /scenarios/{id}` | `scenarios:read` |
| Живой blueprint | `GET /scenarios/{id}/blueprint` | `scenarios:read` |
| Черновик | тот же вызов с `draft=true` | `scenarios:read` |
| Старая версия | `blueprintId` из `GET /scenarios/{id}/blueprints` | `scenarios:read` |
| Правка | `PATCH /scenarios/{id}` | `scenarios:write` |
| Создать | `POST /scenarios` | `scenarios:write` |
| Включить | `POST /scenarios/{id}/start` | `scenarios:write` |
| Выключить | `POST /scenarios/{id}/stop` | `scenarios:write` |
| Разовый запуск | `POST /scenarios/{id}/run` | `scenarios:run` |
| Повтор прогона | `POST /scenarios/{id}/replay` | `scenarios:run` |

`POST /start` не только включает сценарий. Если он по расписанию, Make сразу его запускает. `POST /stop` выключает. Для живого фидера Facebook эти два вызова, `/run` и `/replay` не дергать: повтор старых заявок уже случался.

В `PATCH` и `POST` поле `blueprint` это строка JSON, не объект. `scheduling` тоже строка. Чужие поля не присылать: что не передано, не меняется.

В `PATCH` класть `baseVersion`. Это `response.version` из `GET /blueprint`. Если сценарий уже сохранили, ответ `409` / `SC409`, запись не применяется. В ошибке будут `currentVersion` и `lastEdit`. Без `baseVersion` запись пройдёт поверх чужой правки.

`confirmed=true` нужен, только если Make просит подтвердить первую установку приложения. Иначе сценарий не обновится.

Версии blueprint старше 60 дней API не отдаёт.

## Логи

`GET /scenarios/{id}/logs`

`from` и `to` в миллисекундах. Статус: `1` успех, `2` предупреждение, `3` ошибка.

Пустые опросы триггера в ответ не входят. Чтобы увидеть их, нужен `showCheckRuns=true`. В интерфейсе History их тоже нет. Одна операция и `transfer: 0` значит триггер ничего нового не нашёл.

`GET /scenarios/{id}/logs/{executionId}` отдаёт сводку прогона, не поля заявки.

`GET /scenarios/{id}/executions/{executionId}` отдаёт статус (`SUCCESS`, `WARNING`, `ERROR`, `RUNNING`, `PAUSED`). Тела модулей там нет. Что записано в контакт, видно в blueprint модуля, не в логе прогона.

`GET /scenarios/{id}/modules/{moduleId}/logs` показывает, какой модуль реально выполнялся.

Незавершённые прогоны это отдельный ресурс `/dlqs`, scope `dlqs:read` и `dlqs:write`. Удаление всех сразу требует `confirmed=true`, иначе `406` / `IM004`.

## Facebook у Павла, состояние на 2026-09-29

Часы кабинета Make: `America/New_York`. В логах API время UTC.

| Сценарий | id | Форма | Состояние |
|---|---|---|---|
| FB Feeder D (Лицензия Bridge US LA) | `5887846` | `929695312760050`, объявление video 35sec | Включён, интервал 300 секунд. 29 сентября в модуль создания контакта на ветке «есть телефон» добавлена почта, поле `142534`, рядом с телефоном `142532`. Версия blueprint 8. |
| FB Feeder E (California ru, объявление Stop bnpl video) | `6443586` | `1103689772147390` | Создан 29 сентября выключенным, затем включён. Та же схема, что у D, включая почту. Клон через `POST /clone` API не принял, сценарий создан через `POST /scenarios`. |

Ветка «нет телефона» по-прежнему пишет только почту. Если контакт уже есть, сценарий ставит примечание и почту в карточку не дописывает. У Feeder E текст примечания с 29 сентября: «Повторная заявка Facebook, объявление Stop bnpl video».

Проверка в тот же день: после правки D пустой опрос прошёл успешно. E включён и берёт очередь по одной заявке за 5 минут, с самой старой. Первые три уже были в CRM, новая карточка не создавалась: Evgene Podgaiko `31393871`, Aleksandr Sergienko `31393855`, Леонид `31381661`. Модуль создания контакта с почтой на живой заявке ещё не срабатывал, потому что эти люди нашлись по телефону.
