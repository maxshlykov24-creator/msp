#!/usr/bin/env python3
"""Показать сыгранные вопросы из БД на VPS.

Запуск:
    DEPLOY_SSH_PASSWORD='…' python3 scripts/show_played.py
"""

from __future__ import annotations

import json
import os
import pathlib
import sys

import paramiko

HOST = "72.56.123.137"
USER = "root"
ROOT = pathlib.Path(__file__).resolve().parents[1]
QUESTIONS = json.loads((ROOT / "questions.json").read_text(encoding="utf-8"))
BY_NUM = {q["number"]: q for q in QUESTIONS}


def connect() -> paramiko.SSHClient:
    password = os.environ.get("DEPLOY_SSH_PASSWORD", "").strip()
    if not password:
        print("Set DEPLOY_SSH_PASSWORD", file=sys.stderr)
        sys.exit(1)
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        HOST, username=USER, password=password,
        timeout=60, banner_timeout=90, allow_agent=False, look_for_keys=False,
    )
    return client


def fetch_numbers(client: paramiko.SSHClient) -> list[int]:
    cmd = r"""docker run --rm -v dg-questions-bot_dg_data:/data alpine sh -c 'apk add --no-cache sqlite >/dev/null 2>&1; sqlite3 /data/dg.sqlite3 "SELECT sq.question_number FROM session_questions sq JOIN game_sessions gs ON sq.session_id=gs.id WHERE sq.status=\"done\" ORDER BY sq.order_index;"; echo ---; sqlite3 /data/dg.sqlite3 "SELECT question_number FROM deck_ledger ORDER BY used_at;"'"""
    _, stdout, stderr = client.exec_command(cmd, timeout=120)
    out = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    if err:
        print(err, file=sys.stderr)
    nums: list[int] = []
    section = "done"
    for line in out.splitlines():
        if line.strip() == "---":
            section = "ledger"
            continue
        line = line.strip()
        if not line or not line.isdigit():
            continue
        n = int(line)
        if section == "done":
            nums.append(n)
    if nums:
        return nums
    # fallback: deck_ledger
    for line in out.splitlines():
        line = line.strip()
        if line.isdigit():
            nums.append(int(line))
    return nums


def main() -> int:
    client = connect()
    try:
        nums = fetch_numbers(client)
    finally:
        client.close()

    if not nums:
        print("Сыгранных вопросов в БД не найдено.")
        return 0

    print(f"Ответили: {len(nums)} вопросов\n")
    for i, n in enumerate(nums, 1):
        q = BY_NUM.get(n)
        if not q:
            print(f"{i}. #{n} — (нет в колоде)")
            continue
        print(f"{i}. #{n} [{q['tag']}] {q['text']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
