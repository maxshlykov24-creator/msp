# keris-amocrm-scripts

Разовые скрипты настройки amoCRM Keris Club. Не прод-сервис.

| Файл | Назначение |
|------|------------|
| `setup_amocrm.py` | первичная сборка аккаунта |
| `setup_grooming_pipeline.py` | воронка «Груминг», её этапы, поля визита, поля питомца на Компании, метрики на Контакте |
| `inspect_amocrm.py` | осмотр воронок и полей |
| `create_test_deals.py` | тестовые сделки |
| `actualize_sales.py` | разовый проход: переписки → этапы и поля «Продажи» |

`actualize_sales.py` (ключ в `.env`, дампы в `_data/`, оба вне git):

```bash
python3 actualize_sales.py dump          # сделки, беседы, попытка Wazzup CSV
python3 actualize_sales.py ingest-csv файл.csv   # если кабинет Wazzup отдал выгрузку
python3 actualize_sales.py classify
python3 actualize_sales.py report
python3 actualize_sales.py apply --dry-run
python3 actualize_sales.py apply --pack   # только после «да» на первый пакет
```

Текст сообщений amoCRM не отдаёт (403 Invalid scope). Выгрузка Wazzup `POST /v2/messages/messages_dump` на этом ключе отвечает 404. Пока нет CSV из кабинета — классификатор помечает сделки как `no_text_has_talk` и **в воронку не пишет**.

`setup_grooming_pipeline.py` идемпотентен (повторный прогон ничего не дублирует)
и берёт токен из окружения, а не из файла:

```bash
AMOCRM_TOKEN=<долгосрочный токен> python3 setup_grooming_pipeline.py
```

Палитру этапов amoCRM не документирует и произвольный hex отклоняет 400
`NotSupportedChoice` — включая цвета из `setup_amocrm.py`. В скрипте только те
значения, что живут в аккаунте; `#D5D8DB` и `#c1c1c1` заняты системными Провалом
и Неразобранным. Системные этапы 142/143 API переименовывать не даёт — только
руками в интерфейсе.

Токены — в `Keris_Club/06_Доступы/ДОСТУПЫ.md`, не в этом каталоге.
Контекст ТЗ: `04_Производство/Активные/Keris_Club/03_Проекты/Питомник/`.
