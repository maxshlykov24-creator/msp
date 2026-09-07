# Каталог бота (build-контекст)

`docker-compose.yml` собирает сервис `bot` из этой папки (`build: ./bot`).

Два способа подключить реального бота:

## Вариант A — скопировать код бота сюда

Положи в этот каталог код бота и `Dockerfile` (пример — `Dockerfile.example`).
Например, для `study-bot`:

```bash
cp -r ../../../../../1. ЛИЧНОЕ/1.\ Профиль/3.\ Разум/3.1.\ Изучение/3.1.4.\ Курсы/2.\ Практика/study-bot/* ./
cp Dockerfile.example Dockerfile   # или используй свой
```

## Вариант B — указать существующий проект

В `../docker-compose.yml` поменяй у сервиса `bot`:

```yaml
    build: ../../../../../1. ЛИЧНОЕ/3. Разум/3.1. Изучение/3.1.4. Курсы/2. Практика/study-bot
```

## Важно про сеть

Сервис `bot` работает в сетевом namespace контейнера `wg`
(`network_mode: "service:wg"`). Значит:

- Запросы к `api.telegram.org` идут через VPN (split-routing).
- К базе обращайся по хосту `db` (docker DNS), порт `5432` — трафик к ней НЕ в тоннеле.
- Если бот слушает HTTP (webhook/Web App) — порт публикуется через `caddy`
  (см. `../caddy/Caddyfile.example`, `reverse_proxy wg:<порт>`).
