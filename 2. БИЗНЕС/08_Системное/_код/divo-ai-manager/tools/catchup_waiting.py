"""Ответить чатам, которые ждут после сбоя OpenRouter.

  PYTHONPATH=. LLM_PROXY=socks5://127.0.0.1:1080 python tools/catchup_waiting.py
"""
from __future__ import annotations

import asyncio
import logging

from bot.autoru import Autoru
from bot.autoru_loop import AutoruChannel
from bot.avito import Avito
from bot.avito_loop import AvitoChannel
from bot.config import settings
from bot.main import CHANNELS, catchup_waiting
from bot.tg import Telegram

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


async def main() -> None:
    tg = Telegram(settings.telegram_token)
    avito = Avito()
    autoru = Autoru() if settings.autoru_enabled else None
    CHANNELS["tg"] = tg
    CHANNELS["avito"] = AvitoChannel(avito, tg)
    if autoru is not None:
        CHANNELS["autoru"] = AutoruChannel(autoru, tg)
    try:
        sent = await catchup_waiting()
        print("ответил %d" % sent)
    finally:
        await tg.close()
        await avito.close()
        if autoru is not None:
            await autoru.close()


def review_fingerprint(history: list[dict]) -> str:
    import hashlib, json
    stable = [{k: row.get(k) for k in ("id", "role", "content", "at")} for row in history]
    return hashlib.sha256(json.dumps(stable, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


async def send_reviewed(channel, chat_id, text, expected, read_history, journal_path):
    """One reviewed send, fail closed on new messages or an uncertain POST.

    Caller exclusively pauses the selected chat in the main process first.
    A persisted intent is never automatically sent again after restart.
    """
    import hashlib, json, time
    from bot import store, human
    key = hashlib.sha256((chat_id + expected + text).encode()).hexdigest()
    journal = json.loads(journal_path.read_text()) if journal_path.exists() else {}
    if key in journal:
        return journal[key]
    latest = await read_history()
    row = {"chat_id": chat_id, "expected": expected, "text": text, "at": time.time()}
    journal[key] = row
    if review_fingerprint(latest) != expected:
        row["status"] = "changed_skip"
        store.atomic_json(journal_path, journal)
        return row
    row["status"] = "sending"
    store.atomic_json(journal_path, journal)
    try:
        await channel.send(chat_id, text)
        latest = await read_history()
        own = set(map(str, (store.load_doc(chat_id).get("crm") or {}).get("out_ids") or []))
        found = [m for m in latest if str(m.get("id")) in own and m.get("role") == "assistant"
                 and m.get("content", "").strip() == human.for_chat(text).strip()
                 and m.get("at", 0) >= row["at"] - 2]
        row["status"] = "confirmed" if len(found) == 1 else "uncertain"
        if len(found) == 1:
            row["message_id"] = found[0]["id"]
    except Exception as exc:
        row["status"] = "uncertain"
        row["error_type"] = type(exc).__name__
    store.atomic_json(journal_path, journal)
    return row


if __name__ == "__main__":
    asyncio.run(main())
