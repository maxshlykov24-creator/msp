# Keris Club — booking Mini App bot

Демо Telegram Mini App для прототипа онлайн-записи на груминг.

- Бот: `@KerisClubTest_bot`
- Статика: nginx + Let’s Encrypt на VPS `213.165.44.164` → `https://213.165.44.164.sslip.io/`
- Не путать с витриной `@kerisclubbot` (`keris-bot`)

## Деплой

```bash
export KERIS_DEPLOY_PASSWORD='…'
export BOT_TOKEN='…'
export WEBAPP_URL='https://213.165.44.164.sslip.io/'
python3 deploy.py
```

Токен в git не коммитить — только `.env` на сервере.
