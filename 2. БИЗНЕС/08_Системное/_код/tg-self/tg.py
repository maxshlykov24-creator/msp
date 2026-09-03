#!/usr/bin/env python3
"""Личный Telegram-клиент. Писать в чужой чат — только по явной просьбе."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ACCOUNTS = {
    "self": ROOT / "tg_self",
    "work": ROOT / "tg_work",
    "learn": ROOT / "tg_learn",
}
ACCOUNT_LABELS = {
    "self": "личный",
    "work": "рабочий",
    "learn": "обучение",
}
PHONE_ENV = {
    "self": "TELEGRAM_PHONE",
    "work": "TELEGRAM_PHONE_WORK",
    "learn": "TELEGRAM_PHONE_LEARN",
}
PASSWORD_ENV = {
    "self": "TELEGRAM_PASSWORD",
    "work": "TELEGRAM_PASSWORD_WORK",
    "learn": "TELEGRAM_PASSWORD_LEARN",
}
ACCOUNTS_FILE = ROOT / "accounts.json"


def load_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        os.environ.setdefault(key, value)


def credentials() -> tuple[int, str]:
    load_env()
    api_id_raw = os.environ.get("TELEGRAM_API_ID", "").strip()
    api_hash = os.environ.get("TELEGRAM_API_HASH", "").strip()
    if not api_id_raw or not api_hash:
        sys.exit(
            "Нет TELEGRAM_API_ID или TELEGRAM_API_HASH.\n"
            "Скопируй .env.example в .env и вставь данные с my.telegram.org"
        )
    try:
        return int(api_id_raw), api_hash
    except ValueError:
        sys.exit("TELEGRAM_API_ID должен быть числом.")


def session_stem(account: str) -> Path:
    if account not in ACCOUNTS:
        sys.exit("account должен быть self, work или learn")
    return ACCOUNTS[account]


def session_file(account: str) -> Path:
    return Path(str(session_stem(account)) + ".session")


def make_client(account: str = "self", session_path: Path | None = None):
    from telethon import TelegramClient
    from telethon.network.connection.tcpabridged import ConnectionTcpAbridged

    api_id, api_hash = credentials()
    return TelegramClient(
        str(session_path if session_path is not None else session_stem(account)),
        api_id,
        api_hash,
        connection=ConnectionTcpAbridged,
        use_ipv6=False,
        connection_retries=2,
        timeout=12,
        device_model="Cursor Mac",
        system_version="macOS",
        app_version="tg-self",
        flood_sleep_threshold=24,
    )


def require_session(account: str = "self") -> None:
    path = session_file(account)
    if not path.exists():
        sys.exit(f"Сессии нет. Сначала: python3 tg.py login --account {account}")


async def open_authorized(account: str = "self"):
    require_session(account)
    client = make_client(account)
    try:
        await client.connect()
    except sqlite3.OperationalError:
        try:
            await client.disconnect()
        except Exception:
            pass
        sys.exit(
            f"Сессия {account} занята другим процессом. Подожди и повтори, "
            "не копируй файл сессии: второй вход с тем же ключом Telegram отзывает."
        )
    if not await client.is_user_authorized():
        await client.disconnect()
        sys.exit(
            f"Сессия {account} не авторизована. Telegram мог отозвать "
            f"сеанс (Завершить другие сеансы) или слетел .env с номером. "
            f"Сначала: python3 tg.py login --account {account}"
        )
    return client


def label_of(value) -> str:
    if value is None:
        return ""
    if hasattr(value, "text"):
        inner = value.text
        return inner if isinstance(inner, str) else label_of(inner)
    return str(value)


def _is_migrated(dialog) -> bool:
    return bool(getattr(getattr(dialog, "entity", None), "migrated_to", None))


async def find_dialog(client, needle: str):
    raw = (needle or "").strip()
    if raw in {"me", "saved", "избранное"}:
        return await client.get_entity("me")
    if raw.lstrip("-").isdigit():
        return await client.get_entity(int(raw))
    needle_l = raw.lower()
    matches = []
    async for dialog in client.iter_dialogs():
        name = dialog.name or ""
        if needle_l == name.lower() or needle_l in name.lower():
            matches.append(dialog)
    if not matches:
        sys.exit(f"Чат не найден: {raw}")
    alive = [d for d in matches if not _is_migrated(d)]
    if not alive:
        sys.exit(f"Чат «{raw}» только в архиве миграции, живой не найден.")
    exact = [d for d in alive if (d.name or "").lower() == needle_l]
    if len(exact) == 1:
        return exact[0]
    if len(alive) == 1:
        return alive[0]
    listing = "\n".join(f"  {d.id}  {d.name}" for d in alive[:20])
    sys.exit(f"Несколько чатов, уточни id или название:\n{listing}")


def is_poll(message) -> bool:
    from telethon.tl.types import MessageMediaPoll

    return isinstance(getattr(message, "media", None), MessageMediaPoll)


def poll_answers(message) -> list[tuple[str, bytes]]:
    return [(label_of(ans.text), ans.option) for ans in message.media.poll.answers]


def chosen_answers(message) -> list[str]:
    results = message.media.results
    if not results or not results.results:
        return []
    by_opt = {opt: text for text, opt in poll_answers(message)}
    return [
        by_opt[row.option]
        for row in results.results
        if getattr(row, "chosen", False) and row.option in by_opt
    ]


def match_option(message, needle: str) -> bytes:
    needle_l = needle.strip().lower()
    answers = poll_answers(message)
    hits = [(text, opt) for text, opt in answers if needle_l in text.lower()]
    starts = [(text, opt) for text, opt in hits if text.lower().startswith(needle_l)]
    pool = starts or hits
    if len(pool) == 1:
        return pool[0][1]
    listing = ", ".join(text for text, _ in answers)
    if not pool:
        sys.exit(f"Вариант не найден: {needle}. Есть: {listing}")
    sys.exit(f"Несколько вариантов на «{needle}»: {listing}")


async def find_poll(client, entity, msg_id: int | None, limit: int):
    if msg_id:
        message = await client.get_messages(entity, ids=msg_id)
        if message is None or not is_poll(message):
            sys.exit(f"Сообщение {msg_id} — не опрос.")
        return message
    async for message in client.iter_messages(entity, limit=limit):
        if is_poll(message):
            return message
    sys.exit("Опрос в последних сообщениях не найден.")


def _phone_for(account: str) -> str | None:
    load_env()
    raw = os.environ.get(PHONE_ENV[account], "").strip()
    return raw or None


def _password_for(account: str) -> str | None:
    load_env()
    raw = os.environ.get(PASSWORD_ENV[account], "").strip()
    return raw or None


def _login_hash_path(account: str) -> Path:
    return ROOT / f".login_{account}.json"


def _me_payload(me) -> dict:
    name = " ".join(part for part in (me.first_name, me.last_name) if part)
    return {
        "id": me.id,
        "username": me.username or "",
        "name": name,
    }


def write_account_record(account: str, me) -> None:
    data = {}
    if ACCOUNTS_FILE.exists():
        try:
            data = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
    payload = _me_payload(me)
    payload["label"] = ACCOUNT_LABELS.get(account, account)
    data[account] = payload
    ACCOUNTS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def format_me(me) -> str:
    name = " ".join(part for part in (me.first_name, me.last_name) if part)
    username = f" @{me.username}" if me.username else ""
    return f"{name}{username} (id {me.id})"


async def cmd_login(account: str, code: str | None = None) -> None:
    from telethon.errors import SessionPasswordNeededError

    phone = _phone_for(account)
    if not phone:
        sys.exit(f"Нет номера. Задай {PHONE_ENV[account]} в .env")

    client = make_client(account)
    await client.connect()
    hash_path = _login_hash_path(account)

    if not code:
        sent = await client.send_code_request(phone)
        hash_path.write_text(
            json.dumps(
                {
                    "phone": phone,
                    "phone_code_hash": sent.phone_code_hash,
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"Код отправлен в Telegram аккаунта {account}.")
        print(f"Потом: python3 tg.py login --account {account} --code XXXXX")
        await client.disconnect()
        return

    if not hash_path.exists():
        await client.disconnect()
        sys.exit("Сначала запроси код: python3 tg.py login --account " + account)

    data = json.loads(hash_path.read_text(encoding="utf-8"))
    try:
        await client.sign_in(
            phone,
            code.strip(),
            phone_code_hash=data["phone_code_hash"],
        )
    except SessionPasswordNeededError:
        password = _password_for(account)
        if not password:
            await client.disconnect()
            sys.exit(f"Нужен облачный пароль: {PASSWORD_ENV[account]} в .env")
        await client.sign_in(password=password)

    me = await client.get_me()
    write_account_record(account, me)
    hash_path.unlink(missing_ok=True)
    print(f"Ок. Вошёл как {format_me(me)}.")
    print(f"Сессия: {session_file(account)}")
    await client.disconnect()


async def cmd_whoami(account: str) -> None:
    client = await open_authorized(account)
    me = await client.get_me()
    write_account_record(account, me)
    print(format_me(me))
    await client.disconnect()


async def cmd_dialogs(account: str, limit: int) -> None:
    client = await open_authorized(account)
    print(f"{'id':<16} {'тип':<10} имя")
    async for dialog in client.iter_dialogs(limit=limit):
        if dialog.is_user:
            kind = "user"
        elif dialog.is_group:
            kind = "group"
        else:
            kind = "channel"
        print(f"{dialog.id:<16} {kind:<10} {dialog.name}")
    await client.disconnect()


async def cmd_send_saved(account: str, text: str) -> None:
    body = text.strip()
    if not body:
        sys.exit("Пустой текст.")
    client = await open_authorized(account)
    await client.send_message("me", body)
    print("Отправлено в Избранное.")
    await client.disconnect()


def _chat_title(entity, fallback: str) -> str:
    return getattr(entity, "title", None) or getattr(entity, "first_name", fallback)


async def cmd_send(
    account: str,
    chat: str,
    text: str,
    reply: int | None = None,
    silent: bool = False,
) -> None:
    body = text.strip()
    if not body:
        sys.exit("Пустой текст.")
    client = await open_authorized(account)
    entity = await find_dialog(client, chat)
    sent = await client.send_message(
        entity, body, reply_to=reply, silent=silent
    )
    print(f"Отправлено в {_chat_title(entity, chat)} (msg {sent.id}).")
    await client.disconnect()


async def cmd_history(account: str, chat: str, limit: int) -> None:
    client = await open_authorized(account)
    entity = await find_dialog(client, chat)
    n = 0
    async for msg in client.iter_messages(entity, limit=limit):
        n += 1
        who = "я" if msg.out else (getattr(msg.sender, "first_name", None) or "?")
        text = (msg.message or "").replace("\n", " ")[:120]
        print(f"{msg.id} {msg.date} {who}: {text}")
    print(f"сообщений: {n}")
    await client.disconnect()


async def cmd_search(account: str, chat: str, query: str, limit: int) -> None:
    client = await open_authorized(account)
    entity = await find_dialog(client, chat)
    n = 0
    async for msg in client.iter_messages(entity, search=query, limit=limit):
        n += 1
        text = (msg.message or "").replace("\n", " ")[:120]
        print(f"{msg.id} {msg.date}: {text}")
    print(f"найдено: {n}")
    await client.disconnect()


async def cmd_edit(account: str, chat: str, msg_id: int, text: str) -> None:
    body = text.strip()
    if not body:
        sys.exit("Пустой текст.")
    client = await open_authorized(account)
    entity = await find_dialog(client, chat)
    await client.edit_message(entity, msg_id, body)
    print(f"Правлено {msg_id}.")
    await client.disconnect()


async def cmd_delete(account: str, chat: str, msg_id: int) -> None:
    client = await open_authorized(account)
    entity = await find_dialog(client, chat)
    await client.delete_messages(entity, [msg_id])
    print(f"Удалено {msg_id}.")
    await client.disconnect()


async def cmd_react(account: str, chat: str, msg_id: int, emoji: str | None, clear: bool) -> None:
    from telethon.tl.functions.messages import SendReactionRequest
    from telethon.tl.types import ReactionEmoji

    client = await open_authorized(account)
    entity = await find_dialog(client, chat)
    reaction = [] if clear else [ReactionEmoji(emoji or "👍")]
    await client(SendReactionRequest(peer=entity, msg_id=msg_id, reaction=reaction))
    print("Реакция снята." if clear else f"Реакция {emoji or '👍'}.")
    await client.disconnect()


async def cmd_pin(account: str, chat: str, msg_id: int, unpin: bool) -> None:
    client = await open_authorized(account)
    entity = await find_dialog(client, chat)
    if unpin:
        await client.unpin_message(entity, msg_id)
        print(f"Откреплено {msg_id}.")
    else:
        await client.pin_message(entity, msg_id, notify=False)
        print(f"Закреплено {msg_id} без уведомления.")
    await client.disconnect()


async def cmd_read(account: str, chat: str, unread: bool) -> None:
    from telethon.tl.functions.messages import MarkDialogUnreadRequest

    client = await open_authorized(account)
    entity = await find_dialog(client, chat)
    if unread:
        await client(MarkDialogUnreadRequest(peer=entity, unread=True))
        print("Помечено непрочитанным.")
    else:
        await client.send_read_acknowledge(entity)
        print("Прочитано.")
    await client.disconnect()


async def cmd_draft(account: str, chat: str, text: str | None, clear: bool) -> None:
    from telethon.tl.functions.messages import SaveDraftRequest

    client = await open_authorized(account)
    entity = await find_dialog(client, chat)
    body = "" if clear else (text or "").strip()
    await client(SaveDraftRequest(peer=entity, message=body))
    print("Черновик очищен." if clear else "Черновик записан.")
    await client.disconnect()


async def cmd_file(account: str, chat: str, path: str, caption: str, silent: bool) -> None:
    client = await open_authorized(account)
    entity = await find_dialog(client, chat)
    sent = await client.send_file(
        entity, path, caption=caption or None, silent=silent
    )
    mid = sent.id if not isinstance(sent, list) else sent[0].id
    print(f"Файл в {_chat_title(entity, chat)} (msg {mid}).")
    await client.disconnect()


async def cmd_forward(account: str, chat: str, msg_id: int, to: str) -> None:
    client = await open_authorized(account)
    entity = await find_dialog(client, chat)
    dest = await find_dialog(client, to)
    sent = await client.forward_messages(dest, msg_id, entity)
    n = len(sent) if isinstance(sent, list) else 1
    print(f"Переслано {n} → {_chat_title(dest, to)}.")
    await client.disconnect()


async def cmd_polls(account: str, chat: str, limit: int) -> None:
    client = await open_authorized(account)
    entity = await find_dialog(client, chat)
    found = 0
    async for message in client.iter_messages(entity, limit=limit):
        if not is_poll(message):
            continue
        found += 1
        answers = [text for text, _ in poll_answers(message)]
        chosen = chosen_answers(message)
        print(
            f"id={message.id} {message.date} "
            f"q={label_of(message.media.poll.question)!r} "
            f"answers={answers} chosen={chosen}"
        )
    print(f"опросов: {found}")
    await client.disconnect()


async def cmd_vote(account: str, chat: str, option: str, msg_id: int | None, scan: int) -> None:
    from telethon.tl.functions.messages import SendVoteRequest

    client = await open_authorized(account)
    entity = await find_dialog(client, chat)
    message = await find_poll(client, entity, msg_id, scan)
    before = chosen_answers(message)
    target = match_option(message, option)
    await client(
        SendVoteRequest(peer=entity, msg_id=message.id, options=[target])
    )
    after_msg = await client.get_messages(entity, ids=message.id)
    after = chosen_answers(after_msg)
    print(
        f"опрос {message.id} q={label_of(message.media.poll.question)!r} "
        f"{before} → {after}"
    )
    await client.disconnect()


async def cmd_poll(account: str, chat: str, question: str, answers: list[str]) -> None:
    from telethon.tl.types import Poll, PollAnswer, TextWithEntities

    options = [item.strip() for item in answers if item.strip()]
    if len(options) < 2:
        sys.exit("Нужно минимум два варианта.")
    client = await open_authorized(account)
    entity = await find_dialog(client, chat)
    poll = Poll(
        id=random.getrandbits(63),
        question=TextWithEntities(question.strip(), []),
        answers=[
            PollAnswer(TextWithEntities(text, []), str(i).encode("ascii"))
            for i, text in enumerate(options)
        ],
        hash=0,
    )
    sent = await client.send_file(entity, poll)
    title = getattr(entity, "title", None) or getattr(entity, "first_name", chat)
    print(f"Опрос создан в {title} (msg {sent.id}): {question!r} / {options}")
    await client.disconnect()


def _add_account(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--account",
        choices=("self", "work", "learn"),
        default="self",
        help="self — личный, work — рабочий, learn — обучение",
    )


def _add_chat(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--chat", required=True, help="название, id или me")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Личный Telegram CLI. Писать в чужой чат — только по просьбе владельца."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    login = sub.add_parser("login", help="вход по номеру и коду из Telegram")
    _add_account(login)
    login.add_argument("--code", help="код из Telegram, второй шаг входа")

    whoami = sub.add_parser("whoami", help="кто залогинен")
    _add_account(whoami)

    dialogs = sub.add_parser("dialogs", help="последние диалоги")
    _add_account(dialogs)
    dialogs.add_argument("--limit", type=int, default=20)

    send_saved = sub.add_parser("send-saved", help="отправить себе в Избранное")
    _add_account(send_saved)
    send_saved.add_argument("text", help="текст сообщения")

    send = sub.add_parser("send", help="отправить текст в чат")
    _add_account(send)
    _add_chat(send)
    send.add_argument("--text", required=True)
    send.add_argument("--reply", type=int, help="id сообщения для ответа")
    send.add_argument("--silent", action="store_true")

    history = sub.add_parser("history", help="последние сообщения чата")
    _add_account(history)
    _add_chat(history)
    history.add_argument("--limit", type=int, default=20)

    search = sub.add_parser("search", help="поиск в чате")
    _add_account(search)
    _add_chat(search)
    search.add_argument("--query", required=True)
    search.add_argument("--limit", type=int, default=20)

    edit = sub.add_parser("edit", help="править своё сообщение")
    _add_account(edit)
    _add_chat(edit)
    edit.add_argument("--msg", type=int, required=True)
    edit.add_argument("--text", required=True)

    delete = sub.add_parser("delete", help="удалить сообщение")
    _add_account(delete)
    _add_chat(delete)
    delete.add_argument("--msg", type=int, required=True)

    react = sub.add_parser("react", help="реакция на сообщение")
    _add_account(react)
    _add_chat(react)
    react.add_argument("--msg", type=int, required=True)
    react.add_argument("--emoji", default="👍")
    react.add_argument("--clear", action="store_true")

    pin = sub.add_parser("pin", help="закрепить без уведомления")
    _add_account(pin)
    _add_chat(pin)
    pin.add_argument("--msg", type=int, required=True)

    unpin = sub.add_parser("unpin", help="открепить сообщение")
    _add_account(unpin)
    _add_chat(unpin)
    unpin.add_argument("--msg", type=int, required=True)

    read = sub.add_parser("read", help="пометить чат прочитанным")
    _add_account(read)
    _add_chat(read)

    unread = sub.add_parser("unread", help="пометить чат непрочитанным")
    _add_account(unread)
    _add_chat(unread)

    draft = sub.add_parser("draft", help="черновик")
    _add_account(draft)
    _add_chat(draft)
    draft.add_argument("--text", default="")
    draft.add_argument("--clear", action="store_true")

    file_cmd = sub.add_parser("file", help="отправить файл или фото")
    _add_account(file_cmd)
    _add_chat(file_cmd)
    file_cmd.add_argument("--path", required=True)
    file_cmd.add_argument("--caption", default="")
    file_cmd.add_argument("--silent", action="store_true")

    forward = sub.add_parser("forward", help="переслать сообщение")
    _add_account(forward)
    _add_chat(forward)
    forward.add_argument("--msg", type=int, required=True)
    forward.add_argument("--to", default="me")

    polls = sub.add_parser("polls", help="найти опросы в чате")
    _add_account(polls)
    _add_chat(polls)
    polls.add_argument("--limit", type=int, default=200)

    vote = sub.add_parser("vote", help="выбрать вариант в опросе")
    _add_account(vote)
    _add_chat(vote)
    vote.add_argument("--option", required=True, help="подстрока варианта, например нет")
    vote.add_argument("--msg", type=int, help="id сообщения-опроса")
    vote.add_argument("--scan", type=int, default=400, help="сколько сообщений смотреть")

    poll = sub.add_parser("poll", help="создать опрос")
    _add_account(poll)
    _add_chat(poll)
    poll.add_argument("--question", required=True)
    poll.add_argument("--answers", nargs="+", required=True)

    return parser


def main() -> None:
    args = build_parser().parse_args()
    account = args.account
    if args.command == "login":
        asyncio.run(cmd_login(account, getattr(args, "code", None)))
    elif args.command == "whoami":
        asyncio.run(cmd_whoami(account))
    elif args.command == "dialogs":
        asyncio.run(cmd_dialogs(account, args.limit))
    elif args.command == "send-saved":
        asyncio.run(cmd_send_saved(account, args.text))
    elif args.command == "send":
        asyncio.run(
            cmd_send(
                account, args.chat, args.text, args.reply, args.silent
            )
        )
    elif args.command == "history":
        asyncio.run(cmd_history(account, args.chat, args.limit))
    elif args.command == "search":
        asyncio.run(cmd_search(account, args.chat, args.query, args.limit))
    elif args.command == "edit":
        asyncio.run(cmd_edit(account, args.chat, args.msg, args.text))
    elif args.command == "delete":
        asyncio.run(cmd_delete(account, args.chat, args.msg))
    elif args.command == "react":
        asyncio.run(cmd_react(account, args.chat, args.msg, args.emoji, args.clear))
    elif args.command == "pin":
        asyncio.run(cmd_pin(account, args.chat, args.msg, False))
    elif args.command == "unpin":
        asyncio.run(cmd_pin(account, args.chat, args.msg, True))
    elif args.command == "read":
        asyncio.run(cmd_read(account, args.chat, False))
    elif args.command == "unread":
        asyncio.run(cmd_read(account, args.chat, True))
    elif args.command == "draft":
        asyncio.run(cmd_draft(account, args.chat, args.text, args.clear))
    elif args.command == "file":
        asyncio.run(cmd_file(account, args.chat, args.path, args.caption, args.silent))
    elif args.command == "forward":
        asyncio.run(cmd_forward(account, args.chat, args.msg, args.to))
    elif args.command == "polls":
        asyncio.run(cmd_polls(account, args.chat, args.limit))
    elif args.command == "vote":
        asyncio.run(cmd_vote(account, args.chat, args.option, args.msg, args.scan))
    elif args.command == "poll":
        asyncio.run(cmd_poll(account, args.chat, args.question, args.answers))
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
