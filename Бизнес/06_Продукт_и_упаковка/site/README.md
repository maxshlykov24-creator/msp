# Сайт-визитка

> **Создание нового «дорогого» сайта:** см. **`РЕГЛАМЕНТ_САЙТ_10K.md`** в этой папке (референсы, $10K-планка, 21st.dev, skills, YouTube-методология).

Одностраничный сайт на HTML + Tailwind CDN. Без сборки, без npm, без платформ.

## Структура

```
site/
├── index.html      ← весь сайт
├── content.md      ← шпаргалка по маркерам и что нужно заменить
├── assets/
│   ├── og.jpg      ← 1200×630 px — превью в Telegram/WhatsApp/VK (создать вручную)
│   └── favicon.svg ← иконка вкладки (опц., сейчас стоит emoji ⚡)
└── README.md
```

## Быстрые правки

Все редактируемые блоки в `index.html` обёрнуты маркерами:
```html
<!-- EDIT: имя-маркера -->
...контент...
<!-- /EDIT -->
```

**Говоришь в Cursor:**
- «поменяй `hero-title` на [текст]»
- «добавь кейс в секцию cases»
- «замени USERNAME на my_telegram_handle везде»

Список всех маркеров — в `content.md`.

## Перед деплоем

Заменить в `index.html` и `content.md`:

| Что | На что |
|-----|--------|
| `USERNAME` | твой Telegram @username (5 мест) |
| `YOUR@EMAIL.RU` | реальный email |
| `YOURDOMAIN.ru` | домен сайта |
| Цифры в кейсах | реальные данные из проектов |
| `assets/og.jpg` | картинка 1200×630 для превью |

## Деплой

### Вариант A: GitHub Pages (рекомендуется)

1. Создать репозиторий (например, `site-visitka`) или использовать текущий
2. Запушить папку `site/` в ветку `main`
3. Открыть Settings → Pages → Source: ветка `main`, папка `/site`
4. Через 1-2 минуты сайт доступен по `https://username.github.io/site-visitka`

**Свой домен:**
- Создать файл `site/CNAME` с текстом `yourdomain.ru`
- В DNS провайдера добавить CNAME-запись: `www` → `username.github.io`
- Или A-записи на IP GitHub Pages (185.199.108.153, .109, .110, .111)
- В Settings → Pages → Custom domain: ввести домен, включить HTTPS

**Обновление сайта:**
```bash
git add site/
git commit -m "update site"
git push
```
Через 1 минуту изменения на сайте автоматически.

### Вариант B: Свой VPS (nginx)

```nginx
server {
    listen 80;
    server_name yourdomain.ru www.yourdomain.ru;
    root /var/www/site;
    index index.html;
    location / { try_files $uri $uri/ /index.html; }
}
```

```bash
# Деплой одной командой
scp -r site/ user@yourserver:/var/www/site/

# Или через git на сервере
cd /var/www/site && git pull
```

HTTPS через certbot:
```bash
certbot --nginx -d yourdomain.ru -d www.yourdomain.ru
```

## Проверка после деплоя

- [ ] Открыть на телефоне (мобильная верстка)
- [ ] Проверить OG-превью: https://t.me/iv?url=https://yourdomain.ru
- [ ] Проверить скорость: https://pagespeed.web.dev
- [ ] Проверить все ссылки на Telegram/email
