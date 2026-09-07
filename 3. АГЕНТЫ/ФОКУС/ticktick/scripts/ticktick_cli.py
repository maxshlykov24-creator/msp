#!/usr/bin/env python3
"""TickTick Open API CLI для агента @тиктик."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / ".config"
CREDENTIALS_FILE = CONFIG_DIR / "credentials.json"
TOKEN_FILE = CONFIG_DIR / "token.json"
PROJECTS_FILE = ROOT / "_КОНТЕКСТ" / "проекты.md"
API_BASE = "https://api.ticktick.com/open/v1"
TOKEN_URL = "https://ticktick.com/oauth/token"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def token_expired(token: dict[str, Any]) -> bool:
    obtained = token.get("obtained_at")
    expires_in = token.get("expires_in")
    if not obtained or not expires_in:
        return False
    try:
        start = datetime.fromisoformat(obtained)
    except ValueError:
        return False
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - start).total_seconds()
    return age >= float(expires_in) - 300


def refresh_access_token(credentials: dict[str, Any], token: dict[str, Any]) -> dict[str, Any]:
    refresh_token = token.get("refresh_token")
    if not refresh_token:
        raise RuntimeError("Токен истёк, refresh_token отсутствует — нужна повторная OAuth-авторизация.")

    payload = urllib.parse.urlencode(
        {
            "client_id": credentials["client_id"],
            "client_secret": credentials["client_secret"],
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        TOKEN_URL,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    data["obtained_at"] = datetime.now(timezone.utc).isoformat()
    save_json(TOKEN_FILE, data)
    return data


def get_access_token() -> str:
    if not CREDENTIALS_FILE.is_file() or not TOKEN_FILE.is_file():
        raise RuntimeError(f"Нет credentials/token в {CONFIG_DIR}")

    credentials = load_json(CREDENTIALS_FILE)
    token = load_json(TOKEN_FILE)
    if token_expired(token):
        token = refresh_access_token(credentials, token)
    access_token = token.get("access_token")
    if not access_token:
        raise RuntimeError("access_token не найден в token.json")
    return access_token


def api_request(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    token = get_access_token()
    url = f"{API_BASE}{path}"
    data = None
    headers = {"Authorization": f"Bearer {token}"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"TickTick API {exc.code}: {detail}") from exc


def list_projects(include_closed: bool = False) -> list[dict[str, Any]]:
    projects = api_request("GET", "/project")
    if not isinstance(projects, list):
        raise RuntimeError("Неожиданный ответ /project")
    if include_closed:
        return projects
    return [p for p in projects if not p.get("closed")]


def find_project(name_or_id: str, projects: list[dict[str, Any]]) -> dict[str, Any]:
    needle = name_or_id.strip().casefold()
    for project in projects:
        if project.get("id") == name_or_id:
            return project
    for project in projects:
        if project.get("name", "").casefold() == needle:
            return project
    for project in projects:
        if needle in project.get("name", "").casefold():
            return project
    raise RuntimeError(f"Проект не найден: {name_or_id}")


def get_project_data(project_id: str) -> dict[str, Any]:
    data = api_request("GET", f"/project/{project_id}/data")
    if not isinstance(data, dict):
        raise RuntimeError("Неожиданный ответ /project/{id}/data")
    return data


def collect_open_tasks(project_filter: str | None = None) -> list[dict[str, Any]]:
    projects = list_projects()
    if project_filter:
        projects = [find_project(project_filter, projects)]

    tasks: list[dict[str, Any]] = []
    for project in projects:
        data = get_project_data(project["id"])
        for task in data.get("tasks", []):
            if task.get("status", 0) != 0:
                continue
            task = dict(task)
            task["projectName"] = project.get("name", "")
            tasks.append(task)
    return tasks


def write_projects_cache(projects: list[dict[str, Any]]) -> None:
    today = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
    header = (
        "# Кэш проектов TickTick\n\n"
        f"> Обновлено: {today}\n"
        "> Источник: `python3 scripts/ticktick_cli.py projects --sync`\n\n"
        "| projectId | Название | Закрыт | viewMode |\n"
        "|-----------|----------|--------|----------|\n"
    )
    rows = []
    for project in sorted(projects, key=lambda p: p.get("name", "").casefold()):
        rows.append(
            f"| {project.get('id', '')} | {project.get('name', '')} | "
            f"{'да' if project.get('closed') else 'нет'} | {project.get('viewMode', '')} |"
        )
    PROJECTS_FILE.write_text(header + "\n".join(rows) + "\n", encoding="utf-8")


def cmd_projects(args: argparse.Namespace) -> int:
    projects = list_projects(include_closed=args.all)
    if args.sync:
        write_projects_cache(projects)
    if args.json:
        print(json.dumps(projects, ensure_ascii=False, indent=2))
        return 0

    for project in projects:
        closed = " [closed]" if project.get("closed") else ""
        print(f"{project.get('id')}\t{project.get('name', '')}{closed}")
    return 0


def cmd_tasks(args: argparse.Namespace) -> int:
    tasks = collect_open_tasks(args.project)
    if args.json:
        print(json.dumps(tasks, ensure_ascii=False, indent=2))
        return 0

    for task in tasks:
        project = task.get("projectName", task.get("projectId", ""))
        print(f"{task.get('id')}\t[{project}]\t{task.get('title', '')}")
    return 0


def cmd_project(args: argparse.Namespace) -> int:
    projects = list_projects(include_closed=True)
    project = find_project(args.name, projects)
    data = get_project_data(project["id"])
    columns = {c["id"]: c["name"] for c in data.get("columns", [])}

    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0

    print(f"Проект: {data['project'].get('name')} ({project['id']})")
    if columns:
        print("Колонки:", ", ".join(columns.values()))
    print()
    for task in data.get("tasks", []):
        col = columns.get(task.get("columnId"), "—")
        print(f"- [{col}] {task.get('title', '')}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TickTick CLI для @тиктик")
    sub = parser.add_subparsers(dest="command", required=True)

    projects = sub.add_parser("projects", help="Список проектов")
    projects.add_argument("--all", action="store_true", help="Включая закрытые")
    projects.add_argument("--sync", action="store_true", help="Обновить _КОНТЕКСТ/проекты.md")
    projects.add_argument("--json", action="store_true")
    projects.set_defaults(func=cmd_projects)

    tasks = sub.add_parser("tasks", help="Открытые задачи")
    tasks.add_argument("--project", help="ID или название проекта")
    tasks.add_argument("--json", action="store_true")
    tasks.set_defaults(func=cmd_tasks)

    project = sub.add_parser("project", help="Проект с задачами и колонками")
    project.add_argument("name", help="ID или название проекта")
    project.add_argument("--json", action="store_true")
    project.set_defaults(func=cmd_project)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
