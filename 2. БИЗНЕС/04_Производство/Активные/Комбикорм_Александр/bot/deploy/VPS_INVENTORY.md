# Карта временного VPS (аудит перед деплоем)

> Снято при первичном аудите. Цель — деплой изолированно, не задев чужие боты.

| Параметр | Значение |
|---|---|
| IP | `213.165.44.164` |
| ОС | Ubuntu 26.04 LTS |
| Python (системный) | 3.14.4 (`/usr/bin/python3`) |
| RAM / Диск | 1.9 ГБ / 30 ГБ (свободно ~25 ГБ) |
| Действует до | ~05.08.2026 (временный) |

## Что уже крутится (НЕ ТРОГАТЬ)

| Сервис | Расположение | Примечание |
|---|---|---|
| `keris-bot.service` | `/root/keris-bot/` (user root) | Telegram-бот Keris Club, long polling, `python3 bot.py` |

- Docker: **не установлен**.
- Nginx / веб-серверы: **нет**.
- Слушающие порты: только `22` (SSH) и `53` (systemd-resolved). Веб-портов нет.
- Пользователи с uid>=1000: нет (всё под root).

## Наш периметр (изолирован)

| Ресурс | Путь |
|---|---|
| Linux user | `kombikorm` (создаётся) |
| Код | `/opt/kombikorm-bot/` |
| venv | `/opt/kombikorm-bot/.venv` |
| Данные (БД, бэкапы) | `/var/lib/kombikorm-bot/` |
| Логи | `/var/log/kombikorm-bot/` |
| systemd | `kombikorm-bot.service` |
| Сеть | long polling (исходящие к api.telegram.org), портов не слушаем |

Пересечений с `keris-bot` нет: другой user, другой каталог, другой юнит, свой venv.
