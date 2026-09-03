# Git-синхронизация vault → GitHub → Cursor Cloud

Репозиторий: `git@github.com:maxshlykov24-creator/msp.git`  
Локальный vault: `/Users/max/CURSOR`  
Ветка для sync: **`main` только**

## Быстрый старт (один раз)

```bash
cd /Users/max/CURSOR

# 1. Git hooks (автопуш после ручного commit)
bash Бизнес/99_Системное/Скрипты_vault/git/install-git-hooks.sh

# 2. Launchd (авто pull+commit+push каждые 2 мин + pull при login)
bash Бизнес/99_Системное/Скрипты_vault/git/install-launchagents.sh
```

## Скрипты

| Файл | Назначение |
|------|------------|
| `vault-sync-auto.sh` | commit → pull --rebase → push (каждые 2 мин) |
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

Лог: `Бизнес/99_Системное/Скрипты_vault/git/logs/vault-sync.log`

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

Автоматически: `vault-sync-pull-wake.sh` (login + каждые 5 мин).  
Локальные файлы обновятся за **~10–15 сек** после Wi‑Fi.

---

## iCloud Desktop (проверить)

Vault на `/Users/max/CURSOR`. Если раньше был `~/Desktop/CURSOR` или включена синхронизация **Desktop & Documents** в iCloud — git + iCloud могут конфликтовать.  
**Рекомендация:** перенести vault в `~/Projects/CURSOR` или отключить iCloud Desktop.

---

## После force-push history (второй Mac / старый clone)

```bash
git fetch origin
git reset --hard origin/main
```

---

## DoD — чеклист «всё работает»

| # | Проверка | Как |
|---|----------|-----|
| 1 | Правка `.md` на Mac → GitHub | Подождать 2–4 мин, смотреть Commits на GitHub |
| 2 | My Machines | Agent на Mac меняет файл локально |
| 3 | Cloud Agent | Agent знает `Бизнес/`, `агенты/` |
| 4 | Секреты | `git check-ignore -v Бизнес/99_Системное/ДОСТУПЫ.md` → .gitignore |
| 5 | post-commit | `git commit --allow-empty -m test` → push без ручного push |
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
