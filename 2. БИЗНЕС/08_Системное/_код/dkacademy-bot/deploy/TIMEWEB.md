# Размещение DKAcademy-bot на Timeweb VPS

## Важно про «сервер из ДОСТУПЫ»

В `2. БИЗНЕС/08_Системное/ДОСТУПЫ.md` для SSH/VPS явно указан **клиентский** хост (МартАче, каталог loyalty). **Не рекомендуется** ставить DKAcademy туда же без вашего решения смешивать инфру клиента и свой проект. Лучше: отдельный VPS или ваш Timeweb-инстанс MS Product и **отдельная строка в ДОСТУПЫ** (FQDN / IP / только ключ SSH, без пароля в файле после настройки ключа).

Пароли из ДОСТУПЫ **не использовать в командной строке** на чужих машинах; для деплоя — **SSH-ключ** и вход `ssh deploy@...</`.

## Что понадобится

1. VPS с Docker и Docker Compose plugin, открытые **80/443** (для Let’s Encrypt) или SSL в панели Timeweb.
2. Поддомен `*.twc1.net` (или свой домен) с **A-записью** на публичный IPv4 этой VPS.
3. На сервере каталог с проектом, например `/root/dkacademy-bot`, и заполненный `.env` (см. корневой `.env.example`).

## Команды на сервере

```bash
cd /root/dkacademy-bot   # или ваш путь
cp -n .env.example .env
nano .env                # TELEGRAM_BOT_TOKEN, amo, LIVEINFORM_API_ID, field_id, …

bash deploy/timeweb-up.sh
```

После успешного health:

```bash
export DKACADEMY_FQDN=dkacademy-bot-ВАШ.twc1.net
# опционально: export CERTBOT_EMAIL=you@example.com
# если проект не в /root/dkacademy-bot: export DKACADEMY_ROOT=/путь/к/dkacademy-bot
bash deploy/timeweb-nginx-subdomain.sh
```

Проверка снаружи: `https://$DKACADEMY_FQDN/health` → `{"status":"ok"}`.

## Сервисы и URL

| Назначение | Путь |
|------------|------|
| Health | `GET /health` |
| OAuth amo (редирект) | `GET /oauth/callback` |
| Ссылка для авторизации | `GET /internal/oauth-link` |
| LiveInform | `POST /webhooks/liveinform` |

В кабинете LiveInform укажите полный URL вебхука и тот же секрет, что `LIVEINFORM_WEBHOOK_SECRET` / заголовок `LIVEINFORM_WEBHOOK_HEADER`.

Скрипт `timeweb-nginx-subdomain.sh` прописывает в `.env` **`REDIRECT_URI=https://<поддомен>/oauth/callback`**. Тот же URL должен быть в настройках интеграции amo. После правки `.env` перезапустите: `docker compose up -d`.

## Лимиты Let’s Encrypt на twc1.net

Как в других проектах vault: при rate limit — подождать окно из лог certbot или включить **бесплатный SSL в панели Timeweb** для поддомена.

## Как залить код на сервер

Варианты: `git clone` с вашего репозитория, `rsync` с локальной машины, CI. Пример rsync (подставьте пользователя и хост):

```bash
rsync -avz --exclude '.env' --exclude '__pycache__' \
  ./dkacademy-bot/ user@YOUR_VPS:/root/dkacademy-bot/
```

На сервере после rsync не забудьте `.env` (он не копируется умышленно).
