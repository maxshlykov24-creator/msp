# Деплой dg-questions-bot

## Сервер (с 2026-07-31)

| | |
|--|--|
| VPS | **DKAcademy / ai-msp** `194.87.226.234` |
| SSH | `ssh -i ~/.ssh/dkacademy_analytics_deploy root@194.87.226.234` (алиас `dkacademy-vps`) |
| Каталог | `/opt/dg-questions-bot` |
| Контейнер | `dg-questions-bot-bot-1`, volume `dg-questions-bot_dg_data` |
| Лимит RAM | 180 МБ |

**Было до 31.07.2026:** LicenseBridge-хаб `72.56.123.137` — оттуда снято (не хватало памяти 1 ГБ вместе с хабом/дашбордом). На хабе тома-страховка ещё лежат как `dg-questions-bot_dg_data` до ручной очистки.

На том же VPS живут `dkacademy-bot`, `dkacademy-analytics`, `ms-p-site` — бот **не открывает порты** (long polling), с ними не конфликтует.

---

## Первый деплой / обновление кода

```bash
cd "1. ЛИЧНОЕ/2. Дух/2.3. Служение/2.3.2. Церковь/1. Мои служения/dg-questions-bot"
cp .env.example .env   # один раз, вписать BOT_TOKEN
python build_questions.py   # если колода менялась
python scripts/_deploy_to_vps.py
# или явно:
# DEPLOY_SSH_KEY="$HOME/.ssh/dkacademy_analytics_deploy" python scripts/_deploy_to_vps.py
```

БД в Docker volume и при редеплое не затирается.

---

## Ручное управление

```bash
ssh dkacademy-vps
cd /opt/dg-questions-bot
docker compose logs -f
docker compose ps
docker compose restart
```

## Бэкап БД

```bash
ssh dkacademy-vps 'docker run --rm -v dg-questions-bot_dg_data:/from -v /tmp:/to alpine \
  tar czf /to/dg-data.tgz -C /from .'
scp dkacademy-vps:/tmp/dg-data.tgz ./backup-dg.tgz
```
