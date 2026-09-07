# Деплой ms-p.ru (portable, без вреда соседям)

Сайт: статика из `site-ms/` → Docker Nginx на `127.0.0.1:19091` → хост-Nginx vhost `ms-p.ru`.

| Принцип | Как |
|--------|-----|
| Изоляция | compose `name: ms-p-site`, свой порт, свой vhost |
| Не ломает | не трогает `/root/dkacademy-bot`, чужие `sites-enabled`, чужие порты |
| Перенос | одна папка `/opt/ms-p-site` + 1 скрипт |

## Перед деплоем

1. **DNS (Timeweb → ms-p.ru → DNS):**
   - `A` `@` → `194.87.226.234`
   - `A` `www` → `194.87.226.234`
2. Дождаться: `dig +short ms-p.ru` → `194.87.226.234`
3. SSH на VPS (ключ `~/.ssh/dkacademy_analytics_deploy`)

## Деплой с Mac

```bash
# 1) залить код
rsync -avz --delete \
  -e "ssh -i ~/.ssh/dkacademy_analytics_deploy -o IdentitiesOnly=yes" \
  --exclude '.DS_Store' \
  "/Users/max/CURSOR/2. БИЗНЕС/08_Системное/_код/site-ms/" \
  root@194.87.226.234:/opt/ms-p-site/

# 2) поднять
ssh -i ~/.ssh/dkacademy_analytics_deploy -o IdentitiesOnly=yes root@194.87.226.234 \
  'cd /opt/ms-p-site && bash deploy/deploy.sh'
```

## Откат (только сайт)

```bash
ssh … 'docker compose -f /opt/ms-p-site/docker-compose.yml down'
ssh … 'rm -f /etc/nginx/sites-enabled/ms-p.ru && nginx -t && systemctl reload nginx'
# каталог /opt/ms-p-site можно оставить или удалить
```

## Смена сервера

1. На новом VPS: Docker + Nginx + certbot
2. DNS A → новый IP
3. `rsync` `/opt/ms-p-site/` на новый хост
4. `bash deploy/deploy.sh`
5. Старый: `docker compose down` + снять vhost

## Порты

| Сервис | Порт | Примечание |
|--------|------|------------|
| ms-p-site | `127.0.0.1:19091` | этот сайт |
| dkacademy-bot | `127.0.0.1:19082` | не трогаем |
