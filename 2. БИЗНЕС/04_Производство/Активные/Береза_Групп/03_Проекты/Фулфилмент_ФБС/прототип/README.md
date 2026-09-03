# БЕРЕЗА ГРУПП — прототип кабинета склада

## Что отправлять клиенту в Telegram

Один файл (всё внутри: стили, скрипт, логотип):

`БЕРЕЗА_ГРУПП_Портал.html`  
(дубль ASCII: `BEREZA_GRUPP_Portal.html`)

Открыть в браузере — страница входа → панели.

### Демо-доступ

`manager` / `demo2026` — вход руководителя. После входа два раздела: **Руководитель** и **Кабинет клиента**.

## Что сделано

- Светлая / тёмная тема (как LicenseBridge USA)
- Диапазон дат + пресеты 7 / 30 / 90 дней — цифры пересчитываются
- Палитра синий Ozon (`#005BFF`)
- Донат «доля клиентов» — hover как у DIVO (масштаб, tip, dim остальных)
- График = **линия остатка** (не столбцы грузооборота) — цифры совпадают с линией
- Бренд: **БЕРЕЗА ГРУПП** (без «фулфилмент»)
- Убраны: синк-бейдж, «Открытые вопросы», блок «Важное», длинный footer про МойСклад
- Поиск по артикулам в кабинете клиента
- Итого в биллинге увеличено
- Формула начисления сокращена

## Исходники для правок

- `portal.src.html` — разметка
- `assets/portal.css` — стили
- `assets/portal.js` — логика и демо-данные

Сборка одного файла:

```bash
python3 - <<'PY'
from pathlib import Path
import base64
ROOT = Path('.')
src = (ROOT/'portal.src.html').read_text(encoding='utf-8')
css = (ROOT/'assets/portal.css').read_text(encoding='utf-8')
js  = (ROOT/'assets/portal.js').read_text(encoding='utf-8')
uri = 'data:image/png;base64,' + base64.b64encode((ROOT/'assets/logo-ms-transparent.png').read_bytes()).decode()
html = src.replace('<link rel="stylesheet" href="assets/portal.css">', f'<style>\n{css}\n</style>')
html = html.replace('<script src="assets/portal.js"></script>', f'<script>\n{js}\n</script>')
html = html.replace('src="assets/logo-ms-transparent.png"', f'src="{uri}"')
(ROOT/'БЕРЕЗА_ГРУПП_Портал.html').write_text(html, encoding='utf-8')
(ROOT/'BEREZA_GRUPP_Portal.html').write_text(html, encoding='utf-8')
print('OK')
PY
```

Старые файлы `Руководитель_Фулфилмент.html` / `Кабинет_клиента.html` — предыдущая версия; актуальный прототип — портал выше.
