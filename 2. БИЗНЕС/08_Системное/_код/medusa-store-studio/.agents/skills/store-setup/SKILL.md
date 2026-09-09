---
name: store-setup
description: Разворачивает интернет-магазин на Medusa v2 с нуля — БД, бэкенд, админка, Next.js-витрина, рублёвый регион, русская админка. Все шаги проверены на живой установке.
whenToUse: Когда нужно поднять новый магазин или починить установку.
arguments: store_name
---

Разверни магазин «**$store_name**» на Medusa v2. Ниже — **проверенная** последовательность (не по докам, а по реальному прогону). Показывай результат после каждого блока.

## 0. Проверь окружение
Нужны: **Node 20–24** (не выше 24 — с Next.js-стартером требование узкое), **PostgreSQL 15+**, git.
```bash
node -v && psql --version && pg_isready
```
⚠️ **macOS/Homebrew — postgresql часто не стартует из коробки.** Симптом: `pg_isready` молчит, в логе `could not open directory ".../lib/postgresql@17"`. Лечение (подставь свою версию):
```bash
V=17; REL=$(ls /opt/homebrew/Cellar/postgresql@$V | head -1)
ln -sfn /opt/homebrew/Cellar/postgresql@$V/$REL/lib/postgresql   /opt/homebrew/lib/postgresql@$V
ln -sfn /opt/homebrew/Cellar/postgresql@$V/$REL/share/postgresql /opt/homebrew/share/postgresql@$V
initdb --locale=C -E UTF-8 /opt/homebrew/var/postgresql@$V
brew services restart postgresql@$V
```
Не хочешь возиться — в стартере есть Docker Compose (postgres+redis+backend+storefront).

## 1. Создай проект
```bash
npx create-medusa-app@latest <store-slug> --with-nextjs-starter --skip-db
```
⏱ Реально занимает **~15 минут** (в доках «a few minutes»), итог ~1.1 ГБ. Предупреди пользователя и не считай зависанием.
Получится монорепо (Turborepo): `apps/backend` (Medusa + админка) и `apps/storefront` (Next.js).

## 2. Подключи базу
```bash
createdb <store-slug>
```
Впиши в `apps/backend/.env` **отдельной строкой**:
```
DATABASE_URL=postgres://<user>@localhost:5432/<store-slug>
```
⚠️ **Грабля:** файл `.env` может быть без перевода строки в конце — `echo "..." >> .env` приклеит переменную к предыдущей строке, и миграции упадут простынёй «No database clientUrl provided». Проверь, что `DATABASE_URL` реально с начала строки (`grep -n '^DATABASE_URL' .env`).
Redis в деве не нужен — Medusa сама поднимет fake-redis (в проде обязателен, см. `deploy-store`).

## 3. Миграции, админ, запуск
```bash
cd apps/backend
npx medusa db:migrate                      # ~4 сек, 143 таблицы + демо-данные
npx medusa user -e <email> -p <password>   # админ
npx medusa develop                         # :9000, админка :9000/app  (старт ~2 сек)
```

## 4. Витрина
`apps/storefront/.env.local`:
```
NEXT_PUBLIC_MEDUSA_PUBLISHABLE_KEY=<pk_... из БД или админки>
NEXT_PUBLIC_MEDUSA_BACKEND_URL=http://localhost:9000
NEXT_PUBLIC_DEFAULT_REGION=<код страны из региона, напр. ru>
```
Ключ достаётся из БД: `psql -d <store-slug> -tAc "select token from api_key where type='publishable' limit 1;"`
Запуск: `npm run dev` в `apps/storefront` → **:8000** (Next.js 15 + Turbopack, старт ~3 сек).
⚠️ `NEXT_PUBLIC_DEFAULT_REGION` должен совпадать со страной существующего региона, иначе витрина не найдёт регион.

## 5. 🇷🇺 Рубли и русская админка
**Рубль в системе есть** (проверено: `rub / ₽. / Russian Ruble`, одна из 126 валют). Но по умолчанию сеется регион **Europe/EUR** — нужно добавить свой:
1. Админка → **Settings → Store → Currencies** → добавить **RUB** (при желании сделать дефолтной).
2. **Settings → Regions** → создать регион «Россия», валюта RUB, страна Russia, включить нужных платёжных провайдеров.
3. Обнови `NEXT_PUBLIC_DEFAULT_REGION=ru` в витрине.

**Русский интерфейс:** в админке есть **33 локали**, включая русскую — переключается в настройках профиля/магазина. Покрытие перевода **~78%**, часть пунктов останется на английском. Скажи об этом пользователю честно.

## 6. Проверка
- `curl localhost:9000/health` → `OK`
- Админка `:9000/app` открывается, логин работает
- Витрина `:8000` — товары, фильтры, корзина
- Цены в рублях после создания RU-региона

## Важное про v2
- **Цены в major-единицах**: 10 = 10 ₽ (в v1 было 1000). Частая ошибка при переносе.
- Плагины Medusa **v1 несовместимы** с v2.
- В стартер уже вложены `AGENTS.md` (карта проекта) и `CLAUDE.md` — агенту есть на что опереться.

Дальше: `скилл payments-ru` (оплата и чеки), `скилл store-design` (дизайн), `скилл store-manage` (наполнение каталога).
