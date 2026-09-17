# Keris Club — пульс собственника

Live-дашборд модуля 3. Прототип и паспорт метрик:
`2. БИЗНЕС/04_Производство/Активные/Keris_Club/03_Проекты/Дашборд/`.

Прод: **https://kerisclub-analytics.ru/pulse/** на том же VPS, что онлайн-запись
(`194.87.118.214`). Корень домена уже отдаёт Mini App записи — пульс живёт
на `/pulse/`, чтобы салон не уронить. Collector — отдельный процесс, в
`keris-server` не вшит.

## Из чего состоит

| Файл | Что делает |
|---|---|
| `app/amocrm.py` | read-only amoCRM v4 |
| `app/bookings.py` | SELECT из Postgres записи |
| `app/collector.py` | полная перевыгрузка → `snapshot.json`, `validate_schema()` |
| `app/auth.py` | логин + bcrypt + cookie |
| `app/main.py` | `/login`, `/`, `/api/data`, `/health` |
| `build_web.py` | `web/index.html` из прототипа |
| `deploy.py` | выкладка systemd + nginx `/pulse/` |

Сбор каждые 15 минут. Срез старше 2 часов — красный баннер, цифры не «тихо устаревшие».

## Эксплуатация

```bash
python3 build_web.py
KERIS_DEPLOY_HOST=194.87.118.214 KERIS_DEPLOY_PASSWORD=... python3 deploy.py

curl -sS https://kerisclub-analytics.ru/pulse/health
curl -sS https://kerisclub-analytics.ru/health   # это по-прежнему keris-server
```

Кнопка «Пульс салона» — в боте Карины (`keris-admin-bot`). Пароль для Safari
один раз в тот же чат (логин `karina`). Docker-файлы — запасной контур по
образцу LicenseBridge; на этом VPS работает systemd.
