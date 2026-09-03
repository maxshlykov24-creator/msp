# amo-self

Локальный CLI: amoCRM на чтение, Wazzup на отправку в уже открытые чаты.

## Что положить в `.env`

1. `AMOCRM_SUBDOMAIN` и `AMOCRM_LONG_LIVED_TOKEN` — как раньше.
2. `WAZZUP_API_KEY` — ключ из Wazzup. В чат не копировать.
3. `WAZZUP_CHANNEL_ID` — только если каналов несколько.

Ключ Wazzup: [app.wazzup24.com](https://app.wazzup24.com) → Интеграция с CRM → API → Подключить.  
Если amo уже подключена, тот же раздел → вкладка Дополнительно.

## Команды

```bash
cd "2. БИЗНЕС/08_Системное/_код/amo-self"
python3 amo.py whoami
python3 amo.py channels
python3 amo.py send --to "Моя любовь" --text "Как дела? Когда домой?"
python3 amo.py history --to "Моя любовь" --days 30
```

Сообщение уходит с выбранного канала Wazzup, не из личного Telegram-клиента.
