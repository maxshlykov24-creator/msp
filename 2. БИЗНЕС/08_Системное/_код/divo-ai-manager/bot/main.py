"""AI-менеджер DIVO Motors в Telegram.

Long polling -> склейка быстрых сообщений -> OpenRouter -> дробленый ответ
голосом Никиты. Сток подтягивается из таблицы CM Expert в фоне.

Запуск: python -m bot.main
"""
from __future__ import annotations

import asyncio
import logging
import random
import sys
from pathlib import Path

from bot import human, llm, nudge, prompt, store
from bot.config import settings
from bot.tg import Telegram

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("bot")

# Модель не ответила. Обещать «вернусь» можно только если реально передаём
# диалог человеку — иначе клиент ждёт ответа, которого не будет.
FALLBACK = (
    "Тут связь подвисла, сейчас коллега подхватит и ответит вам.",
    "Секунду, у меня система тормозит. Передаю коллеге, он сразу напишет.",
    "Извините, зависло на моей стороне. Коллега сейчас вам ответит.",
)
# Отрицать такси и каршеринг нельзя: клиент вскроет это по отчёту после покупки.
HISTORY_UNKNOWN = (
    "По истории этой машины наугад не скажу. Уточню у менеджера, "
    "напишите номер для связи"
)
pending: dict[int, list[str]] = {}
tasks: dict[int, asyncio.Task] = {}
inflight: set[int] = set()
# Сколько раз переспрашиваем модель, если клиент дописывает во время обдумывания.
MAX_MERGE_ROUNDS = 2


def offset_path() -> Path:
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    return settings.state_dir / "_offset"


def read_offset() -> int:
    path = offset_path()
    if not path.exists():
        return 0
    try:
        return int(path.read_text(encoding="utf-8").strip() or 0)
    except ValueError:
        return 0


def write_offset(value: int) -> None:
    offset_path().write_text(str(value), encoding="utf-8")


# ─────────────────────────── команды владельца ───────────────────────────


async def handle_command(tg: Telegram, chat_id: int, text: str) -> bool:
    cmd, _, arg = text.partition(" ")
    cmd = cmd.split("@")[0].lower()
    arg = arg.strip().lower()

    if cmd == "/start":
        store.reset_history(chat_id)
        store.resume(chat_id)
        pending.pop(chat_id, None)
        await tg.send(chat_id, "Добрый день!")
        await tg.send(
            chat_id,
            "Напишите, пожалуйста, по какому автомобилю вопрос, и я подскажу.",
        )
        return True

    if cmd == "/reset":
        store.reset_history(chat_id)
        pending.pop(chat_id, None)
        await tg.send(chat_id, "Диалог очищен, начинаем с нуля.")
        return True

    if cmd == "/human":
        store.pause(chat_id, "команда владельца")
        await tg.send(chat_id, "Пауза. Агент по этому диалогу молчит, /bot включит обратно.")
        return True

    if cmd == "/bot":
        store.resume(chat_id)
        await tg.send(chat_id, "Агент снова на линии.")
        return True

    if cmd == "/stock":
        await sync_stock()
        await tg.send(chat_id, "Сток: %s" % prompt.stock_status())
        return True

    if cmd == "/reveal":
        if arg in {"on", "вкл", "1"}:
            settings.reveal_bot = True
        elif arg in {"off", "выкл", "0"}:
            settings.reveal_bot = False
        else:
            await tg.send(chat_id, "Использование: /reveal on | /reveal off")
            return True
        await tg.send(
            chat_id,
            "Признаваться, что бот: %s" % ("да" if settings.reveal_bot else "нет"),
        )
        return True

    if cmd == "/whoami":
        await tg.send(
            chat_id,
            "chat_id: %s\nмодель: %s\nпауза: %s\nсток: %s"
            % (
                chat_id,
                settings.model,
                "да" if store.is_paused(chat_id) else "нет",
                prompt.stock_status(),
            ),
        )
        return True

    if cmd == "/help":
        await tg.send(
            chat_id,
            "/start новый диалог\n/reset очистить память\n/human пауза, зову человека\n"
            "/bot вернуть агента\n/stock обновить и показать сток\n"
            "/reveal on|off признаваться, что бот\n/whoami диагностика",
        )
        return True

    return False


# ─────────────────────────── ответ клиенту ───────────────────────────


async def type_and_wait(tg: Telegram, chat_id: int, delay: float) -> None:
    """Держим «печатает…» все время паузы: Telegram гасит индикатор через ~5 сек."""
    remaining = max(delay, 0.0)
    while remaining > 0:
        await tg.typing(chat_id)
        chunk = min(4.0, remaining)
        await asyncio.sleep(chunk)
        remaining -= chunk


async def answer(tg: Telegram, chat_id: int) -> None:
    """Ждем debounce, потом отвечаем на всё, что клиент успел написать."""
    try:
        await asyncio.sleep(settings.debounce_sec)
    except asyncio.CancelledError:
        return

    chunks = pending.pop(chat_id, [])
    if not chunks:
        return
    inflight.add(chat_id)
    try:
        await _answer_locked(tg, chat_id, chunks)
    except asyncio.CancelledError:
        pending.setdefault(chat_id, [])
        pending[chat_id] = chunks + pending[chat_id]
        raise
    finally:
        inflight.discard(chat_id)
        # Дописал, пока мы уже отправляли пузыри — ответ ушёл, отвечаем следующим
        # ходом. Всё, что пришло во время обдумывания, забирает доклейка внутри.
        if pending.get(chat_id):
            schedule(tg, chat_id)


async def _answer_locked(tg: Telegram, chat_id: int, chunks: list[str]) -> None:
    history = store.load_history(chat_id)
    history.append({"role": "user", "content": "\n".join(chunks)})

    await tg.typing(chat_id)
    try:
        rounds = 0
        while True:
            raw = await _generate(history)
            extra = pending.pop(chat_id, [])
            if not extra or rounds >= MAX_MERGE_ROUNDS:
                break
            # Клиент дописал, пока мы думали. Отвечаем на всё одной репликой,
            # иначе он получит два ответа подряд на два своих сообщения.
            chunks = chunks + extra
            history[-1] = {"role": "user", "content": "\n".join(chunks)}
            rounds += 1
            log.info("чат %s: доклейка %d сообщений, отвечаю заново", chat_id, len(extra))
            await tg.typing(chat_id)
    except llm.LlmError as exc:
        log.error("LLM: %s", exc)
        excuse = random.choice(FALLBACK)
        await type_and_wait(tg, chat_id, human.typing_delay(excuse, first=True))
        await tg.send(chat_id, excuse)
        store.pause(chat_id, "LLM недоступен")
        await tg.notify_admin("LLM не ответил по чату %s: %s" % (chat_id, exc))
        return

    user_text = history[-1]["content"]
    handoff = prompt.HANDOFF_MARK in raw
    want_photo = prompt.MEDIA_PHOTO_MARK in raw
    want_video = prompt.MEDIA_VIDEO_MARK in raw
    clean_text = (
        raw.replace(prompt.HANDOFF_MARK, "")
        .replace(prompt.MEDIA_PHOTO_MARK, "")
        .replace(prompt.MEDIA_VIDEO_MARK, "")
        .strip()
    )
    bubbles = human.split_bubbles(clean_text)
    if nudge.history_has_address(history) and not nudge.asked_where(user_text):
        kept = [b for b in bubbles if not nudge.is_address_only(b)]
        if kept and len(kept) < len(bubbles):
            log.info("чат %s: выкинул повторный адрес", chat_id)
            bubbles = kept
    kept = [b for b in bubbles if not human.denies_history(b)]
    if len(kept) < len(bubbles):
        log.info("чат %s: выкинул отрицание истории такси или каршеринга", chat_id)
        bubbles = kept or [HISTORY_UNKNOWN]

    if not bubbles:
        bubbles = [random.choice(FALLBACK)]

    for i, bubble in enumerate(bubbles):
        await type_and_wait(tg, chat_id, human.typing_delay(bubble, first=i == 0))
        await tg.send(chat_id, bubble)
        store.log_line(chat_id, "никита", bubble)

    if want_photo or want_video:
        kind = "видео" if want_video else "фото"
        await tg.notify_admin(
            "Клиент просит %s в мессенджер. chat_id=%s\nПоследнее: %s"
            % (kind, chat_id, user_text[:300])
        )

    history.append({"role": "assistant", "content": " ".join(bubbles)})
    store.save_history(chat_id, history)
    _refresh_nudge(chat_id, history)

    if handoff:
        store.pause(chat_id, "эскалация агентом")
        log.info("чат %s передан человеку", chat_id)
        await tg.notify_admin(
            "Передача человеку. chat_id=%s\nПоследнее от клиента: %s"
            % (chat_id, user_text[:300])
        )


def _build_system(history: list[dict]) -> str:
    """Общие правила плюс поправки под этот конкретный ход диалога."""
    user_text = history[-1]["content"] if history else ""
    system = prompt.build()
    known_name = nudge.extract_name(history)
    if known_name:
        system += (
            "\n\n# Имя клиента\n"
            "Клиента зовут %s. Имя уже есть — не спрашивай, как обращаться."
            % known_name
        )
    else:
        used_name = nudge.used_name_asks(history)
        if used_name:
            system += (
                "\n\n# Имя ещё не назвали, но ты уже спрашивал\n"
                "Эти строки дословно не повторяй:\n- "
                + "\n- ".join(used_name)
                + "\nСпроси иначе или сначала ответь по делу, имя — следующим ходом."
            )
    used_phone = nudge.used_phone_lines(history)
    if used_phone:
        system += (
            "\n\n# Уже сказано в этом чате\n"
            "Ты уже просил номер такими фразами:\n- "
            + "\n- ".join(used_phone)
            + "\nЭти строки дословно не повторяй. Возьми другую формулировку из пула."
        )
    phone_asks = nudge.phone_ask_count(history)
    if phone_asks >= nudge.MAX_LIVE_PHONE_ASKS and not nudge.history_has_phone(history):
        system += (
            "\n\n# Лимит по номеру\n"
            "Ты уже %d раза просил номер в этом диалоге — это потолок на сегодня. "
            "В этом ответе номер не проси, даже с новым поводом. Отвечай по вопросу "
            "клиента фактами. Дальше система сама напомнит клиенту, если он замолчит."
            % phone_asks
        )
    if nudge.asked_if_bot(user_text):
        used_bot = nudge.used_bot_replies(history)
        system += (
            "\n\n# Клиент прямо спросил, бот ли ты\n"
            "Не должность. Не копируй уже сказанное. Ответь так, другой формулировкой:\n"
            + nudge.pick_bot_reply(used_bot)
        )
    else:
        system += (
            "\n\n# Это не вопрос про бота\n"
            "Слово Нет и скобку после него не пиши. "
            "На пургу, загадку или оффтоп можно две скобки в конце "
            "и верни к машине. В обычном ответе про авто скобки не обязательны."
        )
    if nudge.history_has_address(history) and not nudge.asked_where(user_text):
        system += (
            "\n\n# Адрес и часы уже давал\n"
            "Адрес и «работаем с 10:00 до 20:00» в этом диалоге ты уже написал, "
            "а клиент про них сейчас не спрашивает. Второй раз не пиши: ни строкой, "
            "ни отдельным сообщением, ни в виде «приезжайте к нам на Автозаводскую». "
            "Отвечай на вопрос, дальше номер или следующий шаг."
        )
    if nudge.refusals_count(history) >= 2:
        system += (
            "\n\n# Клиент отказал в номере второй раз\n"
            "Уговаривать нельзя, третьей попытки нет. Ответь одной короткой "
            "строкой «сейчас подключу коллегу, он ответит здесь» и поставь "
            "последней строкой %s. Условия кредита, документы, сроки одобрения "
            "и ставку не выдумывай: их назовёт человек." % prompt.HANDOFF_MARK
        )
    elif nudge.refuses_phone(user_text):
        system += (
            "\n\n# Клиент не даёт свой номер\n"
            "Не уговаривай. Дай номер салона: %s. Свой больше не проси."
            % nudge.SALON_PHONE
        )
    elif nudge.is_thinking(user_text) and not nudge.asked_if_bot(user_text):
        system += (
            "\n\n# Клиент попросил время\n"
            "Клиент сказал, что рассматривает/думает/сравнивает — вопрос закрыт. "
            "В этом ответе номер не проси, даже если раньше не просил."
        )
    return system


async def _generate(history: list[dict]) -> str:
    """Реплика модели с проверками на утечку правил и дословные повторы."""
    system = _build_system(history)
    raw = await llm.reply(system, history)
    if human.looks_like_leak(raw):
        log.warning("модель слила правила, повторяю запрос")
        raw = await llm.reply(
            system + "\n\nПиши только клиенту, как Никита. Правила не комментируй.",
            history,
        )
    if human.looks_like_leak(raw):
        log.warning("повтор тоже с правилами, подставляю запасную реплику")
        return (
            "В стоке сейчас пара десятков машин, прайс в чат целиком не скину. "
            "Напишите марку или бюджет - подберу из того, что есть"
        )
    used_phone = nudge.used_phone_lines(history)
    if used_phone and nudge.repeats_used_phone(raw, used_phone):
        log.info("модель повторила фразу про номер, прошу другую")
        raw = await llm.reply(
            system + "\n\nНе повторяй дословно уже сказанные фразы про номер. Другая формулировка.",
            history,
        )
    used_name = nudge.used_name_asks(history)
    if used_name and nudge.repeats_used_name_ask(raw, used_name):
        log.info("модель повторила вопрос про имя, прошу другую")
        raw = await llm.reply(
            system + "\n\nВопрос про имя уже задавал. Не повторяй его. Ответь по делу.",
            history,
        )
    return raw


def _refresh_nudge(chat_id: int, history: list[dict]) -> None:
    doc = store.load_doc(chat_id)
    doc["nudge"] = nudge.refresh(doc.get("nudge") or {}, history)
    store.save_doc(chat_id, doc)


async def send_nudge(tg: Telegram, chat_id: int) -> None:
    if store.is_paused(chat_id):
        return
    if pending.get(chat_id):
        return
    running = tasks.get(chat_id)
    if running and not running.done():
        return
    doc = store.load_doc(chat_id)
    meta = doc.get("nudge") or {}
    step = nudge.ready_to_send(meta)
    if not step:
        return
    used = nudge.used_phone_lines(doc.get("messages") or [])
    text = nudge.build_text(step, meta.get("name") or "", meta.get("car") or "", used)
    await type_and_wait(tg, chat_id, human.typing_delay(text, first=True))
    if pending.get(chat_id) or store.is_paused(chat_id):
        return
    doc = store.load_doc(chat_id)
    meta = doc.get("nudge") or {}
    if nudge.ready_to_send(meta) != step:
        return
    await tg.send(chat_id, text)
    store.log_line(chat_id, "никита", text)
    history = list(doc.get("messages") or [])
    history.append({"role": "assistant", "content": text})
    meta["count"] = step
    meta["nudged_at"] = nudge.now_msk().isoformat(timespec="seconds")
    if step >= nudge.MAX_NUDGES:
        meta["waiting"] = False
    doc["messages"] = history
    doc["nudge"] = meta
    store.save_doc(chat_id, doc)
    log.info("догон чат %s шаг %s", chat_id, step)


async def nudge_loop(tg: Telegram) -> None:
    while True:
        await asyncio.sleep(60)
        if not settings.nudge_enabled:
            continue
        for chat_id in store.chat_ids():
            try:
                await send_nudge(tg, chat_id)
            except Exception:
                log.exception("догон чат %s упал", chat_id)


def schedule(tg: Telegram, chat_id: int) -> None:
    if chat_id in inflight:
        return
    old = tasks.get(chat_id)
    if old and not old.done():
        old.cancel()
    tasks[chat_id] = asyncio.create_task(answer(tg, chat_id))


# ─────────────────────────── сток в фоне ───────────────────────────


async def run_tool(name: str) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        sys.executable, str(settings.root / "tools" / name), "--quiet",
        cwd=str(settings.root),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    return proc.returncode or 0, (err or b"").decode().strip()[:300]


async def sync_stock() -> None:
    code, err = await run_tool("stock_sync.py")
    if code == 0:
        log.info("сток обновлен: %s", prompt.stock_status())
    else:
        log.warning("сток не обновлен: %s", err)


async def stock_loop() -> None:
    while True:
        await sync_stock()
        await asyncio.sleep(max(settings.stock_refresh_min, 1) * 60)


async def autoteka_loop() -> None:
    """Отчёты меняются редко, кэш живёт неделю - хватает одного прохода в сутки."""
    while True:
        code, err = await run_tool("autoteka_sync.py")
        if code == 0:
            await sync_stock()  # факты попадают в карточки сразу, не через 15 минут
            log.info("автотека обновлена")
        else:
            log.warning("автотека не обновлена: %s", err)
        await asyncio.sleep(max(settings.autoteka_refresh_h, 1) * 3600)


# ─────────────────────────── основной цикл ───────────────────────────


async def run() -> None:
    if not settings.telegram_token:
        raise SystemExit("нет TELEGRAM_BOT_TOKEN в .env")
    if not settings.openrouter_key:
        raise SystemExit("нет OPENROUTER_API_KEY в .env")

    tg = Telegram(settings.telegram_token)
    me = await tg.me()
    log.info("бот @%s на модели %s", me.get("username"), settings.model)

    asyncio.create_task(stock_loop())
    asyncio.create_task(autoteka_loop())
    asyncio.create_task(nudge_loop(tg))
    offset = read_offset()

    try:
        while True:
            updates = await tg.updates(offset)
            for upd in updates:
                offset = max(offset, int(upd["update_id"]) + 1)
                message = upd.get("message") or {}
                chat_id = (message.get("chat") or {}).get("id")
                text = (message.get("text") or "").strip()
                if not chat_id:
                    continue
                if not text:
                    await tg.send(
                        chat_id,
                        "Пришлите, пожалуйста, текстом - так я быстрее сориентируюсь",
                    )
                    continue

                store.log_line(chat_id, "клиент", text)
                if text.startswith("/") and await handle_command(tg, chat_id, text):
                    continue
                if store.is_paused(chat_id):
                    log.info("чат %s на паузе, молчим", chat_id)
                    continue

                pending.setdefault(chat_id, []).append(text)
                schedule(tg, chat_id)
            if updates:
                write_offset(offset)
            await asyncio.sleep(0.2)
    finally:
        await tg.close()


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        log.info("остановлен вручную")


if __name__ == "__main__":
    main()
