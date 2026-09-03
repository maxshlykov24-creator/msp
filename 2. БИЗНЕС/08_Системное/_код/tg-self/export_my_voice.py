#!/usr/bin/env python3
"""Выгрузка переписок обоих Telegram-аккаунтов в JSONL."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from tg import ACCOUNTS_FILE, ROOT, open_authorized, require_session

DUMP = ROOT / "_выгрузки"
DEFAULT_OUT = DUMP / "сообщения.jsonl"
STATE_PATH = DUMP / "state.json"
CHATS_PATH = DUMP / "чаты.md"
DEDUP_PATH = DUMP / "дедуп.md"
SKIP_PATH = DUMP / "skip_chats.json"

MSP_TITLE = "MSP"
CHAT_PAUSE_SEC = 0.4
FLOOD_PAD_SEC = 2


def load_accounts() -> dict:
    if not ACCOUNTS_FILE.exists():
        sys.exit("Нет accounts.json. Сначала: python3 tg.py whoami --account self")
    return json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))


def my_ids(accounts: dict) -> set[int]:
    return {int(v["id"]) for v in accounts.values() if v.get("id")}


def filter_title(item) -> str:
    title = getattr(item, "title", "")
    if hasattr(title, "text"):
        return (title.text or "").strip()
    return str(title or "").strip()


def peer_numeric_id(peer) -> int | None:
    from telethon.utils import get_peer_id

    try:
        return int(get_peer_id(peer))
    except Exception:
        return None


async def sleep_flood(seconds: int) -> None:
    wait = int(seconds) + FLOOD_PAD_SEC
    print(f"  FloodWait {wait}с, жду", flush=True)
    await asyncio.sleep(wait)


async def msp_peer_ids(client) -> tuple[set[int], list[str]]:
    from telethon.errors import FloodWaitError
    from telethon.tl.functions.messages import GetDialogFiltersRequest

    while True:
        try:
            result = await client(GetDialogFiltersRequest())
            break
        except FloodWaitError as exc:
            await sleep_flood(exc.seconds)

    filters = getattr(result, "filters", result)
    titles = []
    found = None
    for item in filters:
        title = filter_title(item)
        titles.append(title)
        if title.upper() == MSP_TITLE:
            found = item
    if found is None:
        return set(), titles

    ids: set[int] = set()
    for peer in list(getattr(found, "include_peers", []) or []) + list(
        getattr(found, "pinned_peers", []) or []
    ):
        pid = peer_numeric_id(peer)
        if pid is not None:
            ids.add(pid)
    return ids, titles


def load_skip_ids() -> set[int]:
    if not SKIP_PATH.exists():
        return set()
    data = json.loads(SKIP_PATH.read_text(encoding="utf-8"))
    return {int(x) for x in data.get("chat_ids", [])}


def should_skip_dialog(dialog, me_id: int) -> str | None:
    from telethon.tl.types import User

    if dialog.id == me_id:
        return "saved"
    entity = dialog.entity
    if isinstance(entity, User) and getattr(entity, "bot", False):
        return "bot"
    if dialog.is_channel and not dialog.is_group:
        return "channel"
    return None


def media_duration(message, media) -> int | None:
    for obj in (media, getattr(message, "file", None)):
        if obj is None:
            continue
        value = getattr(obj, "duration", None)
        if value is not None:
            return int(value)
    return None


def message_kind(message) -> tuple[str, int | None, str]:
    voice = getattr(message, "voice", None)
    if voice is not None:
        return "voice", media_duration(message, voice), ""
    video_note = getattr(message, "video_note", None)
    if video_note is not None:
        return "video_note", media_duration(message, video_note), ""
    text = message.message or ""
    if getattr(message, "sticker", None) is not None and not text.strip():
        emoji = getattr(message.sticker, "alt", None) or getattr(
            message.file, "emoji", None
        )
        text = emoji or ""
    return "text", None, text


def iso_date(dt) -> str:
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def sender_name(message) -> str:
    sender = getattr(message, "sender", None)
    if sender is None:
        return ""
    first = getattr(sender, "first_name", None)
    last = getattr(sender, "last_name", None)
    title = getattr(sender, "title", None)
    if first or last:
        return " ".join(part for part in (first, last) if part)
    return title or ""


def reply_to_id(message) -> int | None:
    reply = getattr(message, "reply_to", None)
    if reply is not None and getattr(reply, "reply_to_msg_id", None):
        return int(reply.reply_to_msg_id)
    raw = getattr(message, "reply_to_msg_id", None)
    return int(raw) if raw else None


def text_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def secondary_key(row: dict) -> str | None:
    text = (row.get("text") or "").strip()
    if not text:
        return None
    date = (row.get("date") or "")[:19]
    return f"{date}|{row.get('sender_id')}|{text_hash(text)}"


def better_row(old: dict, new: dict) -> dict:
    if old.get("direction") == "out" and new.get("direction") != "out":
        return old
    if new.get("direction") == "out" and old.get("direction") != "out":
        return new
    if old.get("account") == "self":
        return old
    if new.get("account") == "self":
        return new
    return old


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"chats": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"chats": {}}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def parse_since(raw: str) -> datetime:
    dt = datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return dt


async def iter_messages_safe(client, entity, since: datetime, min_id: int):
    from telethon.errors import FloodWaitError

    last_id = min_id
    while True:
        try:
            async for message in client.iter_messages(
                entity,
                reverse=True,
                offset_date=since,
                min_id=last_id,
                wait_time=0.25,
            ):
                last_id = message.id
                yield message
            return
        except FloodWaitError as exc:
            await sleep_flood(exc.seconds)


def is_service(message) -> bool:
    from telethon.tl.types import MessageService

    return isinstance(message, MessageService)


def is_forward_without_own(message, kind: str, text: str) -> bool:
    if not getattr(message, "fwd_from", None):
        return False
    if kind in ("voice", "video_note"):
        return False
    return not text.strip()


async def export_account(
    account: str,
    ids: set[int],
    work_peers: set[int],
    since: datetime,
    work_since: datetime,
    limit_dialogs: int | None,
    out_handle,
    state: dict,
    use_state: bool,
    state_path: Path,
    chat_rows: list[dict],
) -> tuple[int, int]:
    client = await open_authorized(account)
    me = await client.get_me()
    print(f"== {account}: {me.id}", flush=True)

    local_work = work_peers
    titles: list[str] = []
    if account == "self":
        local_work, titles = await msp_peer_ids(client)
        if not local_work:
            await client.disconnect()
            listed = ", ".join(t or "(без имени)" for t in titles) or "пусто"
            sys.exit(
                "Папка MSP в личном аккаунте не найдена. "
                f"Есть папки: {listed}. Подтверди название."
            )
        print(f"  MSP: {len(local_work)} чатов", flush=True)

    skip_ids = load_skip_ids()
    dialogs = []
    skipped_partners = 0
    async for dialog in client.iter_dialogs():
        reason = should_skip_dialog(dialog, me.id)
        if reason:
            continue
        if dialog.id in skip_ids:
            skipped_partners += 1
            continue
        dialogs.append(dialog)
        if limit_dialogs and len(dialogs) >= limit_dialogs:
            break

    if skipped_partners:
        print(f"  пропуск партнёрских групп: {skipped_partners}", flush=True)

    kept = 0
    skipped = 0
    for index, dialog in enumerate(dialogs, start=1):
        if account == "work" or dialog.id in local_work:
            scope = "work"
            window = work_since
        else:
            scope = "personal"
            window = since

        state_key = f"{account}:{dialog.id}"
        min_id = 0
        if use_state:
            min_id = int(state.get("chats", {}).get(state_key, {}).get("last_message_id", 0))

        last_active = dialog.date
        if last_active is not None and last_active.tzinfo is None:
            last_active = last_active.replace(tzinfo=timezone.utc)
        if scope == "personal" and last_active is not None and last_active < window:
            chat_rows.append(
                {
                    "account": account,
                    "chat_id": dialog.id,
                    "chat_title": dialog.name or "",
                    "scope": scope,
                    "is_group": bool(dialog.is_group),
                    "messages": 0,
                }
            )
            if index % 50 == 0:
                print(f"  [{index}/{len(dialogs)}] skip stale personal", flush=True)
            continue

        print(
            f"  [{index}/{len(dialogs)}] {dialog.name!r} scope={scope} "
            f"since={window.date()} min_id={min_id}",
            flush=True,
        )

        prev_uid = None
        prev_date = None
        last_id = min_id
        chat_kept = 0
        async for message in iter_messages_safe(client, dialog, window, min_id):
            if is_service(message):
                skipped += 1
                continue
            kind, duration, text = message_kind(message)
            if is_forward_without_own(message, kind, text):
                skipped += 1
                continue

            sender_id = message.sender_id
            direction = "out" if sender_id in ids else "in"
            date = message.date
            if date is not None and date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            seconds_since_prev = None
            if prev_date is not None and date is not None:
                seconds_since_prev = int((date - prev_date).total_seconds())

            row = {
                "msg_uid": f"{dialog.id}:{message.id}",
                "account": account,
                "date": iso_date(date),
                "chat_id": dialog.id,
                "chat_title": dialog.name or "",
                "is_group": bool(dialog.is_group),
                "scope": scope,
                "direction": direction,
                "sender_id": sender_id,
                "sender_name": sender_name(message),
                "kind": kind,
                "text": text,
                "text_source": "typed" if kind == "text" else "",
                "duration_sec": duration,
                "reply_to_msg_id": reply_to_id(message),
                "prev_msg_uid": prev_uid,
                "seconds_since_prev": seconds_since_prev,
                "edited": bool(getattr(message, "edit_date", None)),
            }
            out_handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            kept += 1
            chat_kept += 1
            last_id = message.id
            prev_uid = row["msg_uid"]
            prev_date = date

        out_handle.flush()
        if use_state:
            state.setdefault("chats", {})[state_key] = {
                "last_message_id": last_id,
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "chat_title": dialog.name or "",
                "scope": scope,
            }
            save_state(state_path, state)
        chat_rows.append(
            {
                "account": account,
                "chat_id": dialog.id,
                "chat_title": dialog.name or "",
                "scope": scope,
                "is_group": bool(dialog.is_group),
                "messages": chat_kept,
            }
        )
        print(f"    +{chat_kept}", flush=True)
        await asyncio.sleep(CHAT_PAUSE_SEC)

    await client.disconnect()
    return kept, skipped


def compact_jsonl(path: Path) -> dict:
    by_uid: dict[str, dict] = {}
    uid_dropped = 0
    secondary_seen: dict[str, str] = {}
    secondary_hits: list[dict] = []
    order: list[str] = []

    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            uid = row["msg_uid"]
            if uid in by_uid:
                uid_dropped += 1
                by_uid[uid] = better_row(by_uid[uid], row)
                continue
            by_uid[uid] = row
            order.append(uid)

            key = secondary_key(row)
            if key:
                if key in secondary_seen and secondary_seen[key] != uid:
                    secondary_hits.append(
                        {
                            "key": key,
                            "keep": secondary_seen[key],
                            "drop": uid,
                            "text": (row.get("text") or "")[:80],
                        }
                    )
                    # не удаляем по второму ключу автоматически для коротких «ок»
                    if len((row.get("text") or "").strip()) >= 12:
                        uid_dropped += 1
                        order.pop()
                        del by_uid[uid]
                        continue
                else:
                    secondary_seen[key] = uid

    tmp = path.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for uid in order:
            if uid in by_uid:
                handle.write(json.dumps(by_uid[uid], ensure_ascii=False) + "\n")
    tmp.replace(path)

    chats = {row["chat_id"] for row in by_uid.values()}
    return {
        "kept": len(by_uid),
        "uid_dropped": uid_dropped,
        "secondary_hits": secondary_hits,
        "chats": len(chats),
        "by_uid": by_uid,
    }


def write_chats_report(rows: list[dict], path: Path) -> None:
    lines = [
        "# Чаты выгрузки",
        "",
        f"Дата: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "| account | scope | тип | сообщений | чат | id |",
        "|---|---|---|---:|---|---|",
    ]
    for row in sorted(rows, key=lambda r: (r["scope"], r["account"], r["chat_title"])):
        kind = "группа" if row["is_group"] else "личный"
        title = (row["chat_title"] or "").replace("|", "/")
        lines.append(
            f"| {row['account']} | {row['scope']} | {kind} | {row['messages']} | {title} | `{row['chat_id']}` |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_dedup_report(stats: dict, path: Path) -> None:
    hits = stats["secondary_hits"]
    lines = [
        "# Дедуп",
        "",
        f"- записей после дедупа: {stats['kept']}",
        f"- чатов: {stats['chats']}",
        f"- схлопнуто по msg_uid: {stats['uid_dropped']}",
        f"- совпадений по второму ключу (дата+sender+текст): {len(hits)}",
        "",
    ]
    if hits:
        lines.append("## Второй ключ")
        lines.append("")
        for item in hits[:50]:
            text = (item["text"] or "").replace("\n", " ")
            lines.append(f"- keep `{item['keep']}` drop `{item['drop']}` · {text}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def async_main(args: argparse.Namespace) -> None:
    accounts = load_accounts()
    ids = my_ids(accounts)
    if not ids:
        sys.exit("В accounts.json нет id")

    wanted = ["self", "work"] if args.account == "all" else [args.account]
    for name in wanted:
        if name not in accounts and name == "work":
            sys.exit("Рабочий аккаунт ещё не залогинен: python3 tg.py login --account work")
        require_session(name)

    DUMP.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    since = parse_since(args.since)
    work_since = parse_since(args.work_since)
    state = load_state(Path(args.state)) if args.state else {"chats": {}}
    use_state = not args.no_state

    started = time.time()
    chat_rows: list[dict] = []
    total_kept = 0
    total_skipped = 0

    # work_peers заполняется внутри self; для work-аккаунта не нужен
    work_peers: set[int] = set()

    with out_path.open("a", encoding="utf-8") as handle:
        for name in wanted:
            kept, skipped = await export_account(
                name,
                ids,
                work_peers,
                since,
                work_since,
                args.limit_dialogs,
                handle,
                state,
                use_state,
                Path(args.state),
                chat_rows,
            )
            total_kept += kept
            total_skipped += skipped
            if use_state:
                save_state(Path(args.state), state)

    compact = compact_jsonl(out_path)
    write_chats_report(chat_rows, CHATS_PATH if not args.limit_dialogs else DUMP / "чаты_проба.md")
    write_dedup_report(compact, DEDUP_PATH if not args.limit_dialogs else DUMP / "дедуп_проба.md")

    print("", flush=True)
    print(f"файл: {out_path}", flush=True)
    print(f"записано сырьём: {total_kept}, отброшено: {total_skipped}", flush=True)
    print(
        f"после дедупа: {compact['kept']} сообщений, {compact['chats']} чатов, "
        f"схлопнуто {compact['uid_dropped']}",
        flush=True,
    )
    print(f"время: {int(time.time() - started)}с", flush=True)
    print("примеры:", flush=True)
    for row in list(compact["by_uid"].values())[:3]:
        preview = (row.get("text") or "")[:60].replace("\n", " ")
        print(
            f"  {row['date']} {row['scope']} {row['direction']} {row['kind']} "
            f"{row['chat_title']!r} {preview!r}",
            flush=True,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Выгрузка Telegram в JSONL")
    parser.add_argument("--account", choices=("self", "work", "all"), default="all")
    parser.add_argument("--since", default="2026-01-01", help="окно личного тона")
    parser.add_argument("--work-since", default="2000-01-01", help="окно рабочих чатов")
    parser.add_argument("--limit-dialogs", type=int, default=0)
    parser.add_argument("--output", default=str(DEFAULT_OUT))
    parser.add_argument("--state", default=str(STATE_PATH))
    parser.add_argument("--no-state", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.limit_dialogs == 0:
        args.limit_dialogs = None
    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()
