# Git-синхронизация vault → GitHub → Cursor Cloud

Репозиторий: `git@github.com:maxshlykov24-creator/msp.git`  
Локальный vault: `/Users/max/CURSOR`  
Ветка для sync: **`main` только**

## Защита и диагностика после ремонта 10.11.2026

Общие проверки реализованы в `vault_sync.py` на Python 3 без сторонних библиотек. Shell-скрипты и хуки используют одну реализацию. Нужны Git, Python 3 и POSIX flock (доступны на этом Mac).

- `pre-commit` проверяет размер Git-индекса; `pre-push` проверяет новые объекты всей отправляемой истории, включая добавленные и затем удалённые файлы. Строго больше 95 MiB блокируется, строго больше 50 MiB вызывает предупреждение.
- Автосинк снимает слишком большой файл только с индекса. Рабочая копия остаётся на диске. Остальные изменения могут отправиться, но полный успех не отмечается; исключённые пути записываются в `logs/excluded-files.json`.
- Если remote является предком локального HEAD, rebase не нужен. Если нужна интеграция remote, а дерево грязное, синк оставляет pending. Autostash не используется. Реальные конфликты требуют ручного разбора.
- Auto, snapshot, stop, post-commit, wake и ручной pull учитывают `.vault-sync-off` и общую блокировку `logs/.sync.flock`. Живой lock не снимается по возрасту; после завершения владельца его освобождает ОС. Сам файл блокировки не удалять. Старый каталог `.sync.lock` требует проверки владельца вручную.
- Сетевые операции ограничены пятью минутами, SSH проверяет живость соединения. Ошибки и частичное сохранение оставляют `logs/.sync-pending`. Повторные уведомления об одной проблеме ограничены.
- `logs/last-push` означает подтверждённую отправку нового HEAD. `logs/last-success` означает полное совпадение с сервером при чистом дереве, в том числе после холостого прогона. Доказательство доставки: SHA `git ls-remote origin refs/heads/main` совпадает с `git rev-parse HEAD`.
- Коды engine: 0 — завершено, 1 — ошибка, 2 — отложено, пауза или частичное сохранение. Ответ `{}` от UI-хука не означает успешного push.
- Перед задачей выполняется локальный snapshot. Stop всегда сохраняет допустимые изменения, отправку ограничивает интервалом пять минут; таймер страхует раз в 15 минут. Wake проверяет входящие изменения при загрузке и раз в час.

ZIP `БРЕНД/MS-Product-web-kit.zip` исключён только из неопубликованной истории и остаётся локально. Страховочные refs и bundle не отправлять. Остальные коммиты сохранены; force-push не применялся. Синхронизация vault не является резервным копированием серверных баз клиентов.

Проверка вручную после снятия паузы: `VAULT_SYNC_NOW=1 sh "2. БИЗНЕС/08_Системное/Скрипты_vault/git/vault-sync-auto.sh"` из корня vault. Не использовать пустой тестовый коммит: проверять доставку следующего реального изменения. Локальные хуки не защищают от намеренного обхода через `--no-verify`, другой hooksPath или сторонний Git-клиент. Произвольные ручные операции с Git во время ремонта запрещены, даже при наличии lock.

## Быстрый старт (один раз)

```bash
cd /Users/max/CURSOR

# 1. Git hooks (автопуш после ручного commit)
bash 2. БИЗНЕС/08_Системное/Скрипты_vault/git/install-git-hooks.sh

# 2. Launchd (авто pull+commit+push каждые 15 мин + pull при login)
bash 2. БИЗНЕС/08_Системное/Скрипты_vault/git/install-launchagents.sh
```

## Скрипты

| Файл | Назначение |
|------|------------|
| `vault-sync-auto.sh` | commit → pull --rebase → push (каждые 15 мин) |
| `vault-sync-pull-wake.sh` | pull при включении Mac |
| `vault-sync-pull.sh` | ручной pull (второй Mac) |
| `install-git-hooks.sh` | post-commit → push в main |
| `install-launchagents.sh` | установка launchd |

## Пауза / конфликт

```bash
# Пауза авто-sync
touch /Users/max/CURSOR/.vault-sync-off

# Снять паузу
rm /Users/max/CURSOR/.vault-sync-off

# Конфликт rebase — файл + macOS-уведомление появятся автоматически
# Решить вручную, затем:
rm /Users/max/CURSOR/.vault-sync-conflict
git add -A && git rebase --continue  # если нужно
git push origin main
```

Лог: `2. БИЗНЕС/08_Системное/Скрипты_vault/git/logs/vault-sync.log`

## Что НЕ попадает в GitHub

- `**/ДОСТУПЫ*.md` — только на Mac
- `.env`, `*.m4a`, `*.webm`
- `.cursor/plans/` — локальный кэш

**В git остаются** `.cursor/rules/` — нужны Cloud Agents.

---

## Cursor Web: работа из браузера

### Mac включён → My Machines

1. Cursor Desktop → **Settings → Cloud Agents → My Machines → Enable**
2. В терминале (если доступна CLI `agent`):
   ```bash
   agent worker start --worker-dir /Users/max/CURSOR
   ```
3. [cursor.com/agents](https://cursor.com/agents) → New Agent → **My Machine**
4. Mac должен быть **online, не в sleep**

### Mac выключен → Cloud Agent

1. [cursor.com/dashboard](https://cursor.com/dashboard) → **Integrations → GitHub** → доступ к `msp`
2. [cursor.com/agents](https://cursor.com/agents) → **Cloud** → repo `msp`, branch **`main`**
3. **Обязательно:** push в **`main`**, не в `cursor/...`:
   - Cursor Dashboard → Cloud Agents → **push directly to branch** / base branch = `main`
   - в API: `workOnCurrentBranch: true`
   - не полагаться на PR без merge
   - stale-ветки `cursor/*` периодически вливать в `main` и удалять (иначе Mac не подтянет правки)

**Признак успеха:** свежий коммит на GitHub в ветке **`main`**, без новых `cursor/...` на origin.

**Готовность push (перед закрытием вкладки):**

- статус run = **Finished**
- в блоке Git ветка **`main`** (не `cursor/...`)
- или свежий коммит на GitHub → `msp` → Commits

### Mac включился после работы в browser

Автоматически: `vault-sync-pull-wake.sh` (login + каждый час).  
Доставка зависит от сети и состояния дерева; результат проверять по SHA и логу.

---

## iCloud Desktop (проверить)

Vault на `/Users/max/CURSOR`. Если раньше был `~/Desktop/CURSOR` или включена синхронизация **Desktop & Documents** в iCloud — git + iCloud могут конфликтовать.  
**Рекомендация:** перенести vault в `~/Projects/CURSOR` или отключить iCloud Desktop.

---

## После force-push history (второй Mac / старый clone)

```bash
git fetch origin
# Сначала сохранить локальные изменения и разобрать расхождение.
# Не применять reset --hard автоматически.
```

---

## DoD — чеклист «всё работает»

| # | Проверка | Как |
|---|----------|-----|
| 1 | Правка `.md` на Mac → GitHub | Проверить следующий реальный коммит и SHA на GitHub |
| 2 | My Machines | Agent на Mac меняет файл локально |
| 3 | Cloud Agent | Agent знает `2. БИЗНЕС/`, `3. АГЕНТЫ/` |
| 4 | Секреты | `git check-ignore -v 2. БИЗНЕС/08_Системное/ДОСТУПЫ.md` → .gitignore |
| 5 | post-commit | Реальный согласованный commit → подтверждённая доставка |
| 6 | Wake pull | правка на GitHub → включить Mac → файл на диске |
| 7 | Cloud → main | Finished + ветка main, не cursor/... |
| 8 | `.cursor/rules/` на GitHub | файл `.cursor/rules/сео.mdc` виден на github.com |
| 9 | Конфликт | `.vault-sync-conflict` + macOS-уведомление при failed rebase |
| 10 | Пауза | `.vault-sync-off` останавливает auto-sync |
| 11 | Lock | параллельные прогоны не дают `index.lock` (skip: locked в логе) |
| 12 | Порядок sync | commit → pull --rebase → push (без ложных CONFLICT на dirty tree) |

---

## Private repo

Проверить вручную: GitHub → `msp` → Settings → **Private**.  
Если repo когда-либо был Public — **сменить пароли** из локальных `ДОСТУПЫ*.md`.
