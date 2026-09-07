# Установка в Cursor, Codex и Claude Code

Копировать всю папку `business-system-checkup`, а не один `SKILL.md`.

## В один проект

```text
Codex / Cursor: <проект>/.agents/skills/business-system-checkup/
Claude Code:    <проект>/.claude/skills/business-system-checkup/
```

Если Cursor не видит `.agents/skills`, использовать:

```text
<проект>/.cursor/skills/business-system-checkup/
```

## Для всех проектов

```text
Cursor:      ~/.agents/skills/business-system-checkup/ или ~/.cursor/skills/business-system-checkup/
Codex:       ~/.agents/skills/business-system-checkup/
Claude Code: ~/.claude/skills/business-system-checkup/
```

После копирования открыть новый чат или перезапустить приложение/CLI. Проверить, что файл находится как `<skills>/business-system-checkup/SKILL.md`.

## Запуск

- Cursor: `/business-system-checkup` или «проверь мою систему».
- Codex: `$business-system-checkup`.
- Claude Code: `/business-system-checkup`.

Не вставлять пароль, токен, cookie, приватный ключ или код MFA в чат. Начинать с текущей рабочей папки в read-only режиме.
