# site-ms — сайт-визитка MS Product

Премиальная одна страница на чистом HTML+CSS+JS. Один файл `index.html`, без сборки.

## Запуск локально

```bash
cd "2. БИЗНЕС/08_Системное/_код/site-ms/public" && python3 -m http.server 8080
# открой http://localhost:8080
```

Можно открыть `public/index.html` двойным кликом (WebGL-фон работает и так; шрифты — с Google Fonts CDN).

## Структура

```
site-ms/
  public/                       # ← то, что отдаётся в веб (docroot)
    index.html                  # вся визитка (inline <style> + <script>)
    privacy.html                # политика конфиденциальности (152-ФЗ)
    consent.html                # согласие на обработку ПДн
    offer.html                  # публичная оферта
    js/cookie-consent.js        # баннер cookie / Яндекс.Метрика
    assets/logo-ms-transparent.png
  deploy/
    deploy.sh                   # изолированный деплой на VPS
    container-conf/default.conf # nginx внутри контейнера
    nginx/ms-p.ru.conf          # vhost на хосте
    README.md                   # инструкция деплоя/переноса
  docker-compose.yml            # проект ms-p-site, 127.0.0.1:19091
  README.md                     # этот файл
```

## Что внутри

- Тёмная тема, палитра магента → фиолет → циан (DNA ms-glass)
- **Фон «текучий шёлк»** — WebGL-шейдер (`#silk`): двойной domain-warp fbm + анизотропия
  вдоль складки, световая жилка по гребню, бренд-градиент по X. Референс — лоадер Яндекс.Почты.
  Рендер в 0.7 от DPR, пауза на скрытом табе, фолбэк на CSS conic-mesh при отсутствии WebGL (`body.no-gl`)
- Виньетка + SVG noise поверх шёлка — держат читаемость текста
- Glass-карточки с gradient-edge
- 3D-tilt + mouse-follow spotlight на карточках (как в DIVO)
- Логотип в hero как 3D-объект с tilt по курсору + orb-glow
- Shimmer на CTA, magnetic-кнопки, cursor spotlight на hero
- Scroll reveal + stagger (IntersectionObserver)
- Live-dot pulse, progress bar, smooth scroll
- Адаптив: мобилка 1 колонка, `@media(hover:none)` отключает tilt, `prefers-reduced-motion` глушит анимации
- OG-теги, favicon, JSON-LD (Organization)

## Деплой

### Vercel
```bash
npm i -g vercel
cd "2. БИЗНЕС/08_Системное/_код/site-ms" && vercel --prod
```

### GitHub Pages
1. Залей папку `site-ms/` в репозиторий (или содержимое в корень branch `gh-pages`).
2. Settings → Pages → Source: ветка/папка.
3. Сайт появится на `https://<user>.github.io/<repo>/`.

### Любой статический хостинг
Netlify, Cloudflare Pages, свой VPS — просто положи содержимое `public/`.

### Текущий прод
VPS `194.87.226.234`, каталог `/opt/ms-p-site`, compose-проект `ms-p-site`, порт `127.0.0.1:19091`.
Подробно и про перенос на другой сервер — `deploy/README.md`.

## Cookie / Яндекс.Метрика

- Баннер согласия: `public/js/cookie-consent.js` (подключён на `index.html` и `privacy.html`).
- Аналитика **не грузится** без согласия «Принять аналитику».
- Когда будет счётчик — вписать ID в `window.MSP_METRIKA_ID` в конце `public/index.html` (и при желании в `privacy.html`).

## Чеклист перед публикацией (плейсхолдеры)

- [x] Подтвердить `@MSP_msproduct` и `info@msproduct.ru` как каноничные (сейчас зашиты в CTA/footer)
- [x] Домен публикации: `ms-p.ru` (OG/JSON-LD обновлены). Почта в CTA пока `info@msproduct.ru`
- [x] Телефон +7 926 309-74-58 в юрстраницах и `02_Финансы/РЕКВИЗИТЫ_ИП.md`
- [ ] `MSP_METRIKA_ID` после создания счётчика Яндекс.Метрики
- [ ] Деплой: `deploy/README.md` → VPS `194.87.226.234`, compose `ms-p-site`, порт `127.0.0.1:19091`
- [ ] Реальные цифры в кейсах (сейчас черновик из vault `content.md`)
- [ ] Показывать ли «ИП Шлыков М. А.» в футере (сейчас показано)
- [ ] `og.jpg` 1200×630 для превью в мессенджерах (сейчас OG-картинка — логотип)
- [ ] При желании нарезать apple-touch-icon 180×180 из логотипа

## Референсы вида (перед правкой)

Живой список: `2. БИЗНЕС/07_Продукт/site/2026-07-08_РЕФЕРЕНСЫ_сайт.md`.  
Регламент сборки: `2. БИЗНЕС/07_Продукт/site/РЕГЛАМЕНТ_САЙТ_10K.md`.

**2026-08-20:** владелец отметил [Traffic Masters](https://traffic-masters.ru/) — «нравится как выглядит», разбор в том файле §5. Брать каркас и CTA, не светлую палитру.  
**2026-09-03:** [Yoolip AI](https://yoolip.ai/#faq) — «нравится как сделан», пример на потом, §6. Не доделывать.

## Редактирование

Контент правится прямо в `public/index.html`. Секции помечены комментариями-якорами (`<section id="...">`).
Палитра — в `:root` в начале `<style>`. Контакты — поиском `MSP_msproduct` и `info@msproduct.ru`.
