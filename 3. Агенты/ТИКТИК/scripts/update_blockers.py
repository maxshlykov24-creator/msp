#!/usr/bin/env python3
"""
Обновляет _КОНТЕКСТ/блокеры.md на основе открытых задач из TickTick.

Используется агентом @тиктик при команде 'обзор':
    python3 scripts/update_blockers.py <tasks_json_file_or_stdin>

Принимает JSON-массив задач из ticktick_cli.py tasks --status open
через stdin или как аргумент (путь к файлу).

Логика:
  - Если taskId из API есть в блокерах → Появлений +1, обновить дату
  - Если taskId новый → добавить строку с Появлений=1
  - Если строка в блокерах, но taskId НЕТ в API → удалить (задача закрыта)

Вывод:
  JSON со списком блокеров (Появлений >= 2) в stdout.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
BLOCKERS_FILE = ROOT / "_КОНТЕКСТ" / "блокеры.md"
PROJECTS_FILE = ROOT / "_КОНТЕКСТ" / "проекты.md"

SEP = "|"
TODAY = datetime.now(timezone.utc).strftime("%d.%m.%Y")


def parse_blockers_md() -> dict[str, dict[str, Any]]:
    """Парсит блокеры.md в dict taskId → строка данных."""
    if not BLOCKERS_FILE.is_file():
        return {}
    lines = BLOCKERS_FILE.read_text(encoding="utf-8").splitlines()
    result: dict[str, dict[str, Any]] = {}
    for line in lines:
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.split(SEP)]
        if len(cells) < 8:
            continue
        task_id = cells[1]
        if not task_id or task_id in ("taskId", "---"):
            continue
        try:
            count = int(cells[4])
        except ValueError:
            count = 1
        result[task_id] = {
            "task_id": task_id,
            "project": cells[2],
            "title": cells[3],
            "count": count,
            "reason": cells[5],
            "updated": cells[6],
        }
    return result


def write_blockers_md(blockers: dict[str, dict[str, Any]]) -> None:
    """Записывает обновлённый блокеры.md."""
    header = (
        "# Блокер-трекер @тиктик\n\n"
        "> Задачи, которые появляются в обзоре **2 раза и более** без закрытия.\n"
        "> Агент обновляет таблицу при каждом `@тиктик обзор`.\n"
        "> При закрытии задачи строка удаляется.\n\n"
    )
    table_header = "| taskId | Проект | Название | Появлений | Последняя причина | Обновлено |\n"
    table_sep = "|--------|--------|----------|-----------|-------------------|-----------|\n"
    rows = ""
    for b in sorted(blockers.values(), key=lambda x: -x["count"]):
        rows += (
            f"| {b['task_id']} | {b['project']} | {b['title']} "
            f"| {b['count']} | {b['reason']} | {b['updated']} |\n"
        )
    if not rows:
        rows = "|        |        |          |           |                   |           |\n"
    BLOCKERS_FILE.write_text(header + table_header + table_sep + rows, encoding="utf-8")


def get_project_name(project_id: str) -> str:
    """Ищет имя проекта в проекты.md по projectId."""
    if not PROJECTS_FILE.is_file():
        return project_id
    text = PROJECTS_FILE.read_text(encoding="utf-8")
    for line in text.splitlines():
        if project_id in line:
            cells = [c.strip() for c in line.split("|")]
            if len(cells) >= 3:
                return cells[1] or project_id
    return project_id


def main() -> int:
    # Читаем задачи из stdin или файла
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
        if not path.is_file():
            print(f"Файл не найден: {path}", file=sys.stderr)
            return 1
        raw = path.read_text(encoding="utf-8")
    else:
        raw = sys.stdin.read()

    try:
        tasks: list[dict[str, Any]] = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"Ошибка парсинга JSON: {e}", file=sys.stderr)
        return 1

    if not isinstance(tasks, list):
        print("Ожидается JSON-массив задач", file=sys.stderr)
        return 1

    # Текущий state блокеров
    blockers = parse_blockers_md()

    # ID всех открытых задач из API
    open_ids: set[str] = set()
    for task in tasks:
        if not isinstance(task, dict):
            continue
        task_id = task.get("id", "")
        if not task_id:
            continue
        open_ids.add(task_id)
        project_id = task.get("projectId", "")
        project_name = get_project_name(project_id)
        title = task.get("title", "")
        if task_id in blockers:
            blockers[task_id]["count"] += 1
            blockers[task_id]["updated"] = TODAY
        else:
            blockers[task_id] = {
                "task_id": task_id,
                "project": project_name,
                "title": title,
                "count": 1,
                "reason": "",
                "updated": TODAY,
            }

    # Удалить закрытые
    closed = [tid for tid in blockers if tid not in open_ids]
    for tid in closed:
        del blockers[tid]

    write_blockers_md(blockers)

    # Вывести список активных блокеров (count >= 2)
    active = [b for b in blockers.values() if b["count"] >= 2]
    active.sort(key=lambda x: -x["count"])
    print(json.dumps(active, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
