"""AI-менеджер DIVO Motors в Telegram.

Long polling -> склейка быстрых сообщений -> OpenRouter -> дробленый ответ
голосом Никиты. Сток подтягивается из таблицы CM Expert в фоне.

Запуск: python -m bot.main
"""
from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from bot import amojo_http, autoru_loop, avito_loop, avito_match, crm, human, llm, nudge, prompt, store
from bot.alerts import AlertBot
from bot.autoru import Autoru
from bot.avito import Avito
from bot.config import settings
from bot.tg import Telegram

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("bot")

# Сбой модели клиенту не объясняем. «Связь подвисла, повторите» уже уходило
# в чат без задержки: ключ OpenRouter моргнул, модель вернула пусто.
# Клиент видит отговорку, которой у живого продавца нет. Молчим, пишем себе.
# Отрицать такси и каршеринг нельзя: клиент вскроет это по отчёту после покупки.
NAME_ASK_REPLACED = "Посмотреть можно в любой день с 10:00 до 20:00"
HISTORY_UNKNOWN = (
    "По истории этой машины наугад не скажу. Уточню по документам, "
    "напишите номер для связи"
)
PHONE_TAKEN = "Номер принял, в ближайшее время наберу"
pending: dict[str, list[str]] = {}
tasks: dict[str, asyncio.Task] = {}
# Сбои LLM подряд по одному чату. Разовый сбой (лимит ключа, таймаут) лечится
# следующим сообщением клиента, а пауза убивает лид навсегда: снимать её надо
# руками, и о ней никто не помнит. Гасим чат только когда не отвечаем подряд.
llm_fails: dict[str, int] = {}
MAX_LLM_FAILS = 2
# Пауза от сбоя модели - не решение владельца, а авария. Кредиты кончились
# вечером, к утру ключ пополнен, а чат всё равно молчит, пока кто-то не вспомнит
# про /bot. Такую паузу снимаем сами: лид не должен зависеть от нашей памяти.
TECH_PAUSE_REASON = "LLM недоступен"
TECH_PAUSE_MIN = 10
# Порог предупреждения об остатке на ключе OpenRouter, в долларах.
BUDGET_WARN_USD = 3.0
inflight: set[str] = set()
# Каналы догона: Telegram, Авито, Авто.ру. Иначе av:… уходит в Telegram API.
CHANNELS: dict[str, object] = {}
# Сколько раз переспрашиваем модель, если клиент дописывает во время обдумывания.
MAX_MERGE_ROUNDS = 2
# Как часто проверяем очередь тех, кому бот так и не ответил.
CATCHUP_EVERY_SEC = 300
# Клиент заблокировал бота или удалил аккаунт: в такой чат не пройдёт ни одно
# сообщение. Догон обязан замолчать навсегда, иначе он ломится туда каждую
# минуту, а счётчик касаний не растёт — отправка падает до его записи.
CHAT_GONE = (
    "bot was blocked by the user",
    "user is deactivated",
    "chat not found",
    "bot was kicked",
    "peer_id_invalid",
)


def chat_gone(error: str) -> bool:
    low = (error or "").lower()
    return any(mark in low for mark in CHAT_GONE)


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
        await tg.send(chat_id, human.greeting_now())
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
            "/reveal on|off признаваться, что бот\n/whoami диагностика\n"
            "/avito status|next|on ID|off ID  (новые чаты берёт сам)\n"
            "/autoru status|next|on ID|off ID  (то же для Авто.ру)",
        )
        return True

    if cmd == "/avito":
        if str(chat_id) != str(settings.admin_chat_id):
            return True
        verb, _, rest = arg.partition(" ")
        rest = rest.strip()
        state = avito_loop.load_state()
        if verb in {"", "status", "статус"}:
            await tg.send(chat_id, avito_loop.status_text())
            return True
        if verb in {"next", "дальше"}:
            state["arm_next"] = True
            avito_loop.save_state(state)
            await tg.send(
                chat_id,
                "Следующий новый чат на Авито возьмёт бот. Напиши клиентом на объявление.",
            )
            return True
        if verb in {"on", "вкл"} and rest:
            allow = list(state.get("allow") or [])
            if rest not in allow:
                allow.append(rest)
            state["allow"] = allow
            state["legacy"] = [x for x in (state.get("legacy") or []) if x != rest]
            avito_loop.save_state(state)
            await tg.send(chat_id, "Авито: бот отвечает в чате %s" % rest)
            return True
        if verb in {"off", "выкл"} and rest:
            state["allow"] = [x for x in (state.get("allow") or []) if x != rest]
            avito_loop.save_state(state)
            store.pause(avito_loop.store_id(rest), "авито выкл")
            await tg.send(chat_id, "Авито: бот замолчал в чате %s" % rest)
            return True
        await tg.send(chat_id, "Использование: /avito status | next | on ID | off ID")
        return True

    if cmd == "/autoru":
        if str(chat_id) != str(settings.admin_chat_id):
            return True
        verb, _, rest = arg.partition(" ")
        rest = rest.strip()
        state = autoru_loop.load_state()
        if verb in {"", "status", "статус"}:
            await tg.send(chat_id, autoru_loop.status_text())
            return True
        if verb in {"next", "дальше"}:
            state["arm_next"] = True
            autoru_loop.save_state(state)
            await tg.send(
                chat_id,
                "Следующий новый чат на Авто.ру возьмёт бот. Напиши клиентом на объявление.",
            )
            return True
        if verb in {"on", "вкл"} and rest:
            allow = list(state.get("allow") or [])
            if rest not in allow:
                allow.append(rest)
            state["allow"] = allow
            state["legacy"] = [x for x in (state.get("legacy") or []) if x != rest]
            autoru_loop.save_state(state)
            await tg.send(chat_id, "Авто.ру: бот отвечает в чате %s" % rest)
            return True
        if verb in {"off", "выкл"} and rest:
            state["allow"] = [x for x in (state.get("allow") or []) if x != rest]
            autoru_loop.save_state(state)
            store.pause(autoru_loop.store_id(rest), "авто.ру выкл")
            await tg.send(chat_id, "Авто.ру: бот замолчал в чате %s" % rest)
            return True
        await tg.send(chat_id, "Использование: /autoru status | next | on ID | off ID")
        return True

    return False


def silent_reason(
    user_text: str,
    history: list[dict],
    *,
    handoff: bool = False,
    llm_dead: bool = False,
) -> str:
    """Повод замолчать и отдать человеку."""
    if llm_dead:
        return "llm"
    if nudge.is_existing_buyer(user_text):
        return "aftersale"
    if not nudge.history_has_phone(history):
        return ""
    if nudge.is_complaint(user_text):
        return "complaint"
    if nudge.wants_person(user_text):
        return "handoff"
    if (
        nudge.wants_call(user_text)
        or nudge.asks_about_call(user_text)
        or nudge.is_caller_id_paste(user_text, history)
    ):
        return "call"
    if handoff:
        if nudge.clarify_count(history) >= 3:
            return "stuck"
        return "handoff"
    return ""


async def _handoff(channel, chat_id, history: list[dict], reason: str) -> None:
    store.save_history(chat_id, history)
    last = history[-1]["content"] if history else ""
    await crm.ack_callback(channel, chat_id, [last], reason)
    store.pause(chat_id, "эскалация: %s" % reason)
    log.info("чат %s молчит, причина %s", chat_id, reason)
    if reason == "llm":
        return
    await crm.capture(chat_id, history, reason)


async def type_and_wait(channel, chat_id, delay: float) -> None:
    """Держим «печатает…» все время паузы: Telegram гасит индикатор через ~5 сек."""
    remaining = max(delay, 0.0)
    while remaining > 0:
        await channel.typing(chat_id)
        chunk = min(4.0, remaining)
        await asyncio.sleep(chunk)
        remaining -= chunk


async def answer(channel, chat_id) -> None:
    """Ждем debounce, потом отвечаем на всё, что клиент успел написать."""
    key = str(chat_id)
    try:
        await asyncio.sleep(settings.debounce_sec)
    except asyncio.CancelledError:
        return

    chunks = pending.pop(key, [])
    if not chunks:
        return
    inflight.add(key)
    try:
        await _answer_locked(channel, key, chunks)
    except asyncio.CancelledError:
        pending.setdefault(key, [])
        pending[key] = chunks + pending[key]
        raise
    finally:
        inflight.discard(key)
        if pending.get(key):
            schedule(channel, key)


def merge_user_chunks(history: list[dict], chunks: list[str]) -> list[dict]:
    """Не дублируем входящее, если опрос уже записал его на диск."""
    blob = "\n".join(c for c in chunks if c)
    if not blob:
        return list(history)
    history = list(history)
    last = history[-1] if history else {}
    if last.get("role") == "user":
        prev = str(last.get("content") or "")
        if prev == blob or blob in prev:
            return history
        if prev and prev in blob:
            history[-1] = {"role": "user", "content": blob}
            return history
    history.append({"role": "user", "content": blob})
    return history


async def _answer_locked(channel, chat_id, chunks: list[str]) -> None:
    history = store.load_history(chat_id)
    had_phone = nudge.history_has_phone(history)
    history = merge_user_chunks(history, chunks)
    user_text = history[-1]["content"] if history else ""
    reason = silent_reason(user_text, history)
    if reason:
        await _handoff(channel, chat_id, history, reason)
        return
    if not had_phone and nudge.extract_phone(user_text):
        store.save_history(chat_id, history)
        await crm.capture(chat_id, history, "phone")
        had_phone = True

    if not nudge.needs_reply(user_text):
        store.save_history(chat_id, history)
        _refresh_nudge(chat_id, history)
        if nudge.is_unheard_media(user_text):
            await channel.notify_owner(
                "Голосовое или видео в чате %s. Расшифровки нет, клиенту не писал, догон выключил."
                % chat_id
            )
        log.info("чат %s: нечего отвечать, молчу", chat_id)
        return

    await channel.typing(chat_id)
    try:
        rounds = 0
        while True:
            raw = await _generate(history, chat_id)
            extra = pending.pop(str(chat_id), [])
            if not extra or rounds >= MAX_MERGE_ROUNDS:
                break
            chunks = chunks + extra
            history[-1] = {"role": "user", "content": "\n".join(chunks)}
            rounds += 1
            log.info("чат %s: доклейка %d сообщений, отвечаю заново", chat_id, len(extra))
            await channel.typing(chat_id)
    except llm.LlmError as exc:
        log.error("LLM: %s", exc)
        key = str(chat_id)
        fails = llm_fails.get(key, 0) + 1
        llm_fails[key] = fails
        last_try = fails >= MAX_LLM_FAILS
        store.save_history(chat_id, history)
        await channel.notify_owner(
            "LLM не ответил по чату %s (подряд %d): %s" % (chat_id, fails, exc)
        )
        if last_try:
            await _handoff(channel, chat_id, history, "llm")
            log.warning("чат %s на паузе: %d сбоя LLM подряд", chat_id, fails)
            return
        log.warning(
            "чат %s: LLM сбой %d, клиенту не пишу про связь", chat_id, fails
        )
        return

    llm_fails.pop(str(chat_id), None)

    # Пока модель думала, клиент мог дописать. Срочное (номер, звонок) пишет
    # на диск опрос каналов, минуя pending, поэтому историю перечитываем: иначе
    # мы затрём его сообщение и попросим то, что уже получили.
    history = merge_user_chunks(store.load_history(chat_id), chunks)
    if store.hard_paused(chat_id):
        store.save_history(chat_id, history)
        log.info("чат %s: пока отвечал, диалог ушёл человеку, молчу", chat_id)
        return

    user_text = history[-1]["content"]
    handoff = prompt.HANDOFF_MARK in raw
    silence = prompt.SILENCE_MARK in raw
    want_photo = prompt.MEDIA_PHOTO_MARK in raw
    want_video = prompt.MEDIA_VIDEO_MARK in raw
    reason = silent_reason(user_text, history, handoff=handoff)
    if reason:
        await _handoff(channel, chat_id, history, reason)
        return
    clean_text = (
        raw.replace(prompt.HANDOFF_MARK, "")
        .replace(prompt.SILENCE_MARK, "")
        .replace(prompt.MEDIA_PHOTO_MARK, "")
        .replace(prompt.MEDIA_VIDEO_MARK, "")
        .strip()
    )
    if silence and not clean_text:
        store.save_history(chat_id, history)
        _refresh_nudge(chat_id, history)
        log.info("чат %s: модель решила молчать", chat_id)
        return
    bubbles = human.split_bubbles(clean_text)
    prior = history[:-1]
    if any((m.get("role") == "assistant") for m in prior):
        stripped = [human.drop_regreeting(b) for b in bubbles]
        stripped = [b for b in stripped if b.strip()]
        if stripped:
            bubbles = stripped
    if nudge.history_has_address(history) and not nudge.asked_where(user_text):
        kept = [b for b in bubbles if not nudge.is_address_only(b)]
        if kept and len(kept) < len(bubbles):
            log.info("чат %s: выкинул повторный адрес", chat_id)
            bubbles = kept
    kept = [b for b in bubbles if b.strip()]
    if len(kept) < len(bubbles):
        log.info("чат %s: выкинул вопрос о цели покупки или бюджете", chat_id)
        bubbles = kept
    kept = [b for b in bubbles if not human.denies_history(b)]
    if len(kept) < len(bubbles):
        log.info("чат %s: выкинул отрицание истории такси или каршеринга", chat_id)
        bubbles = kept or [HISTORY_UNKNOWN]
    if not nudge.extract_name(history) and nudge.used_name_asks(history[:-1]):
        trimmed = [nudge.drop_name_ask(b) for b in bubbles]
        if trimmed != bubbles:
            log.info("чат %s: выкинул повторный вопрос про имя", chat_id)
            bubbles = [b for b in trimmed if b.strip()] or [NAME_ASK_REPLACED]
    if not nudge.history_has_address(history):
        for i, bubble in enumerate(bubbles):
            with_where = nudge.with_address(bubble)
            if with_where != bubble:
                bubbles[i] = with_where
                log.info("чат %s: дописал адрес к приглашению", chat_id)
                break
    cleaned = [
        human.drop_unsolicited(
            b,
            allow_leasing=nudge.asked_leasing(history),
            allow_torg=nudge.asked_torg(history),
            allow_heater=nudge.asked_heater(history),
            allow_condition=nudge.asked_condition(history)
            or not nudge.asked_media(history),
        )
        for b in bubbles
    ]
    cleaned = [b for b in cleaned if b.strip()]
    if cleaned != [b for b in bubbles if b.strip()]:
        log.info("чат %s: выкинул лизинг, торг, отопитель или состояние без вопроса", chat_id)
        bubbles = cleaned
    if nudge.asked_leasing(history) and not nudge.cited_lease_report(user_text):
        shortened = [
            human.soften_lease_status(b, cited_report=False) for b in bubbles
        ]
        if shortened != bubbles:
            log.info("чат %s: убрал лекцию про лизинг в отчёте", chat_id)
            bubbles = [b for b in shortened if b.strip()] or [human.SHORT_LEASE]
    if nudge.asked_torg(history):
        softened = [human.soften_hard_torg(b, allow_torg=True) for b in bubbles]
        if softened != bubbles:
            log.info("чат %s: убрал жёсткий отказ по торгу", chat_id)
            bubbles = [b for b in softened if b.strip()]
    bubbles = [human.drop_tradein_menu(b) for b in bubbles]
    bubbles = [human.drop_max_app(b) for b in bubbles]
    bubbles = [human.speak_messengers(b) for b in bubbles]
    bubbles = [b for b in bubbles if b.strip()]
    if human.client_listing(history):
        trimmed = [human.drop_reask_listing(b) for b in bubbles]
        trimmed = [b for b in trimmed if b.strip()]
        if trimmed != bubbles:
            log.info("чат %s: выкинул повторный запрос объявления", chat_id)
            bubbles = trimmed
    if (
        bubbles
        and not nudge.history_has_phone(history)
        and not nudge.history_refuses_phone(history)
        and nudge.phone_ask_count(history) < nudge.MAX_LIVE_PHONE_ASKS
        and not nudge.is_thinking(user_text)
        and not nudge.asked_noncar_trade(user_text)
        and not nudge.still_noncar_trade(history)
    ):
        with_phone = []
        added = False
        for bubble in bubbles:
            if human.asks_vin(bubble) and not nudge.asked_phone(bubble):
                with_phone.append(human.with_vin_phone(bubble))
                added = True
            else:
                with_phone.append(bubble)
        if added:
            log.info("чат %s: к VIN дописал номер", chat_id)
            bubbles = with_phone
    if nudge.asked_noncar_trade(user_text) or nudge.still_noncar_trade(history):
        trimmed = nudge.keep_noncar_boundary(bubbles)
        if trimmed != bubbles:
            log.info("чат %s: обмен не на авто, одно сообщение без номера", chat_id)
        bubbles = trimmed
    if bubbles and nudge.history_has_phone(history):
        trimmed = [nudge.drop_phone_ask(b) for b in bubbles]
        trimmed = [b for b in trimmed if b.strip()]
        if trimmed != bubbles:
            log.info("чат %s: номер уже есть, выкинул повторный запрос телефона", chat_id)
            bubbles = trimmed or [PHONE_TAKEN]
    if nudge.history_named_messenger(history):
        trimmed = [nudge.drop_messenger_choice(b) for b in bubbles]
        trimmed = [b for b in trimmed if b.strip()]
        if trimmed != bubbles:
            log.info("чат %s: мессенджер уже назван, не спрашиваю какой", chat_id)
            bubbles = trimmed or ["Принял, напишу"]
    if nudge.asked_vat(history):
        vat = avito_match.vat_from_doc(store.load_doc(chat_id))
        if vat:
            filled = [human.ensure_vat_said(b, vat) for b in bubbles]
            if not any(human.vat_digits(vat) in human.vat_digits(b) for b in filled):
                filled = [human.vat_say(vat)] + filled
            trimmed = [nudge.drop_phone_ask(b) for b in filled]
            trimmed = [b for b in trimmed if b.strip()]
            if trimmed != bubbles:
                log.info("чат %s: назвал НДС %s, без номера", chat_id, vat)
            bubbles = trimmed or filled
    prior = history[:-1] if history else []
    stocked = human.dedupe_in_stock(
        bubbles,
        already=nudge.history_said_in_stock(prior),
    )
    if stocked != bubbles:
        log.info("чат %s: убрал повтор «в наличии»", chat_id)
        bubbles = stocked
    first = not any((m.get("role") == "assistant") for m in prior)
    greeted = human.ensure_greeting(bubbles, first=first)
    if greeted != bubbles:
        log.info("чат %s: дописал приветствие в первый ход", chat_id)
        bubbles = greeted
    glued = human.glue_lonely_greeting(bubbles)
    if glued != bubbles:
        log.info("чат %s: склеил приветствие с ответом", chat_id)
        bubbles = glued
    if (
        first
        and not nudge.history_has_phone(prior)
        and len(bubbles) == 1
        and human.greeting_only(bubbles[0])
        and (nudge.asked_visit(user_text) or nudge.asked_where(user_text))
    ):
        invite = human.INVITE_FIRST
        bubbles = [("%s %s" % (bubbles[0].rstrip(), invite)).strip()]
        log.info("чат %s: к голому приветствию дописал осмотр", chat_id)
    if nudge.history_has_phone(prior):
        trimmed = [
            human.drop_push_after_contact(
                b,
                allow_invite=nudge.asked_visit(user_text)
                or nudge.asked_where(user_text),
                allow_call=nudge.asked_our_hours_day(user_text)
                or nudge.about_our_call(user_text),
            )
            for b in bubbles
        ]
        trimmed = [b for b in trimmed if b.strip()]
        if trimmed != [b for b in bubbles if b.strip()]:
            log.info("чат %s: после контакта убрал дожим", chat_id)
        bubbles = trimmed
    if nudge.asked_our_hours_day(user_text):
        rewritten = [
            human.soften_weekday_wait(
                b,
                asked_our=True,
                about_call=nudge.about_our_call(user_text),
            )
            for b in bubbles
        ]
        if rewritten != bubbles:
            log.info("чат %s: не откладываю на будний день", chat_id)
        bubbles = [b for b in rewritten if b.strip()] or [
            human.WEEKDAY_WAIT_CALL
            if nudge.about_our_call(user_text)
            else human.WEEKDAY_WAIT_VISIT
        ]
    if nudge.wants_write_here(user_text) or nudge.history_wants_write_here(history):
        rewritten = [human.phone_to_messenger(b) for b in bubbles]
        if rewritten != bubbles:
            log.info("чат %s: вместо звонка прошу Телеграм или Ватсап", chat_id)
            bubbles = rewritten
    if nudge.asked_noncar_trade(user_text) or nudge.still_noncar_trade(history):
        bubbles = human.glue_lonely_greeting(bubbles)
        final = nudge.keep_noncar_boundary(bubbles)
        if final != bubbles:
            log.info("чат %s: обмен не на авто, одно сообщение без номера", chat_id)
        bubbles = final

    if not bubbles:
        store.save_history(chat_id, history)
        _refresh_nudge(chat_id, history)
        log.info("чат %s: после фильтров пусто, клиенту не пишу", chat_id)
        return

    for i, bubble in enumerate(bubbles):
        await type_and_wait(channel, chat_id, human.typing_delay(bubble, first=i == 0))
        await channel.send(chat_id, bubble)
        store.log_line(chat_id, "никита", bubble)

    if want_photo or want_video:
        kind = "видео" if want_video else "фото"
        await crm.notify_media(chat_id, kind, history)

    history.append({"role": "assistant", "content": " ".join(bubbles)})
    store.save_history(chat_id, history)
    _refresh_nudge(chat_id, history)
    crm.dismiss_llm_alert(chat_id)
    if not had_phone and nudge.extract_phone(user_text):
        await crm.capture(chat_id, history, "phone")


def _build_system(history: list[dict], chat_id: str = "") -> str:
    """Общие правила плюс поправки под этот конкретный ход диалога."""
    user_text = history[-1]["content"] if history else ""
    # Всё, что дописано после границы, меняется каждый ход и в кэш не идёт.
    system = prompt.build() + prompt.CACHE_SPLIT
    now = nudge.now_msk()
    system += (
        "\n\n# Сейчас по Москве\n"
        "Сейчас %s, %s. Салон ежедневно с 10:00 до 20:00, включая субботу "
        "и воскресенье. Не только будни: не соглашайся ждать понедельник, "
        "если до него мы ещё работаем."
        % (nudge.weekday_ru(now), now.strftime("%H:%M"))
    )
    doc = store.load_doc(chat_id) if chat_id else {}
    focus = avito_match.focus_from_doc(doc) if doc else ""
    if focus:
        system += "\n\n" + focus
        system += (
            "\n\n# Этот чат уже про объявление выше\n"
            "Предмет разговора - эта машина, клиент на её карточке. "
            "Не перечисляй её заново («этот AMG и ещё новый за столько»). "
            "Бензин, дизель, «гелик смотрю» - ответ про неё. "
            "Другие машины - только если спросил что ещё есть или эта не подходит.\n"
            "Не выдумывай тему. «Привезти не выгодно» не разворачивай в пошлины "
            "и логистику. «Наугад не скажу» не пиши, пока не спросил факт "
            "из карточки, которого там нет."
        )
    prior = history[:-1] if history else []
    if any((m.get("role") == "assistant") for m in prior):
        system += (
            "\n\n# Диалог уже идёт\n"
            "Ты уже писал в этом чате. Не здоровайся и не представляйся: без "
            "«добрый день», «доброе утро», «добрый вечер», «доброй ночи», "
            "«здравствуйте», «привет», без имени Никита и без "
            "названия салона. Сразу по существу последнего сообщения, опираясь "
            "на переписку выше."
        )
    else:
        greet = human.greeting_now()
        system += (
            "\n\n# Это первый ответ в диалоге\n"
            "Первая реплика начинается с «%s» отдельным предложением, "
            "не через запятую. Другое время суток в приветствии не ставь. "
            "Потом факт из карточки этой машины. "
            "Опции, которых в карточке нет (камера), не выдумывай и не начинай "
            "с голого «уточню»: сначала двигатель и комплектация, потом "
            "«уточню по этому экземпляру». На FAW Bestune NAT дизельный "
            "отопитель сам не называй. Спросили про нагреватель или вебасто - "
            "«да, дизельный отопитель стоит», без «в описании нет» и без номера."
            % greet
        )
    known_name = nudge.extract_name(history)
    if known_name:
        system += (
            "\n\n# Имя клиента\n"
            "Клиента зовут %s. Имя уже есть — не спрашивай, как обращаться."
            % known_name
        )
    else:
        used_name = nudge.used_name_asks(history)
        system += (
            "\n\n# Имя клиента неизвестно\n"
            "Клиент своё имя не называл. По имени не обращайся. "
            "«Никита,» в начале реплики нельзя: это ты, не клиент."
        )
        if used_name:
            system += (
                " Вопрос про имя уже задавал. Второй раз не спрашивай вообще — "
                "ни теми же словами, ни другими, ни вскользь через «кстати». "
                "Работай без имени: обращение не главное, разговор о машине важнее."
            )
    if nudge.is_caller_id_paste(user_text, prior):
        system += (
            "\n\n# Это не новый номер клиента\n"
            "Клиент скинул номер входящего звонка и спрашивает, это мы. "
            "Не пиши «зафиксировал этот номер» и не меняй контакт. "
            "Ответь: «Да, это мы. Наберу ещё раз в ближайшее время»."
        )
    elif nudge.extract_phone(user_text) and not nudge.history_has_phone(prior):
        system += (
            "\n\n# Клиент оставил номер\n"
            "Номер принял. Не пиши «сейчас наберу», «сейчас наберём», "
            "«прямо сейчас позвоним»: это срок, который мы не держим. "
            "«Принял, в ближайшее время наберу» или «в скором времени свяжусь». "
            "Второй раз номер не проси."
        )
        if nudge.named_messenger(user_text) or nudge.history_named_messenger(prior):
            system += (
                " Мессенджер уже назвал: пиши туда, не спрашивай "
                "«Ватсап или Телеграм»."
            )
    if not nudge.history_has_phone(history):
        system += (
            "\n\n# Телефона в этом диалоге ещё нет\n"
            "Конкретный день визита не спрашивай: ни «на какой день удобнее», "
            "ни «во сколько подъедете», ни «завтра». Звать посмотреть машину "
            "вообще - можно и нужно: «посмотреть можно в любой день с 10:00 до 20:00». "
            "Слово «в наличии» один раз: когда клиент спросил «есть?» или в "
            "первом ходе. Дальше не повторяй, зови смотреть. Про день "
            "спросишь, когда номер будет.\n"
            "Имя и номер в одной реплике не проси: это два вопроса. "
            "Сначала одно, второе следующим ходом."
        )
    else:
        system += (
            "\n\n# Номер клиента уже есть\n"
            "Телефон в переписке есть, второй раз его не проси: ни «контактный "
            "телефон», ни «напишите номер», ни «по какому телефону». Клиент мог "
            "прислать VIN и номер двумя сообщениями - смотри всю переписку, а не "
            "последнюю строку.\n"
            "Контакт уже у человека. Ты не знаешь, о чём они говорили по телефону "
            "или в мессенджере. Поэтому не дожимай: не зови в салон, не спрашивай "
            "день и время, не пиши «наберу», «свяжусь», «приезжайте», «посмотреть "
            "можно». Не предлагай следующий шаг. Отвечай только на прямой вопрос "
            "фактом из карточки. Просят автотеку и в карточке есть ссылка - кинь "
            "ссылку сюда, без приглашения и без «наберу». Ссылки нет и машина "
            "новая - «отчёта ещё нет». Ссылки нет на машине с пробегом - не говори "
            "«автотеки нет». Уточни, пришли в Телеграм или Ватсап. Вопрос закрыл "
            "фактом - всё."
        )
        if nudge.asked_our_hours_day(user_text):
            system += (
                " Исключение: клиент сейчас спрашивает, когда мы наберём "
                "или работаем ли в названный будний день. Это про НАШ график, "
                "не про его слот. Не пиши «пн подходит» и не откладывай звонок "
                "на понедельник. Работаем каждый день. Звонок: в ближайшее "
                "время наберу, ждать понедельника не нужно."
            )
    if nudge.asked_our_hours_day(user_text) and not nudge.history_has_phone(history):
        system += (
            "\n\n# Клиент гадает про наш график\n"
            "Назвал будний день вопросом, не «мне удобно только тогда». "
            "Салон каждый день 10:00–20:00. Не подтверждай понедельник как "
            "ближайший слот. Звонок: в ближайшее время наберу, ждать "
            "понедельника не нужно. Осмотр: в любой день, в том числе в выходные."
        )
    if not nudge.asked_leasing(history):
        system += (
            "\n\n# Лизинг не поднимай\n"
            "Клиент про лизинг не спрашивал. Слово «лизинг» не пиши и схему "
            "сам не предлагай."
        )
    else:
        system += (
            "\n\n# Клиент спросил про лизинг\n"
            "Простая «в лизинге была?», «она в лизинге?» — ответ одной фразой: "
            "«нет, выкуплена». Без отчёта, без «в базах может отображаться», "
            "без «документы у нас», без «на сделке покажем». "
            "Сам ткнул в автотеку, «до 28 года», «в отчёте написано» — тогда "
            "можно: в базах может отображаться, по факту закрыт, на сделке покажем. "
            "Купить в лизинг: только с полным НДС, документы третьим лицам "
            "до визита не отдаём. Не говори «данных о лизинге нет» и "
            "«автотеки нет вообще»."
        )
    if not nudge.asked_credit(history):
        system += (
            "\n\n# Кредит не поднимай\n"
            "Клиент про кредит не спрашивал. Не пиши «кредит рассматриваем», "
            "банки, Альфа, ВТБ, Сбер, ОТП и кредитного специалиста."
        )
    else:
        system += (
            "\n\n# Клиент спросил про кредит\n"
            "Салон кредит не оформляет. Способа оплаты два: наличные и "
            "расчётный счёт. Ответ: стоимость в объявлении указана за "
            "наличный расчёт, дальше номер. FAW Bestune NAT: данные автомобили "
            "продаём за наличный расчёт. Не «да», не банки, не кредитный "
            "специалист, не ставка. Настаивает — номер, не спорь."
        )
    if not nudge.asked_torg(history):
        system += (
            "\n\n# Торг не предлагай\n"
            "Клиент не просил скидку и не спрашивал про торг. Не пиши "
            "«комплиментарный торг», «по цене обсудим при осмотре», "
            "«готовы обсудить по месту». «Цена реальная?» это вопрос про "
            "цифру в объявлении: ответь да или нет по факту, без скидки."
        )
    else:
        system += (
            "\n\n# Клиент торгуется\n"
            "Свою цифру не подтверждай и не руби «не сможем» и «зафиксирована». "
            "Цена в объявлении как ориентир. В разумных пределах торг и условия "
            "возможны, обсуждаем после осмотра. Дальше схема визита: приглашение "
            "посмотреть с 10:00 до 20:00, адрес если ещё не писал. ДТП, владельцев, "
            "отопитель, такси и комплектацию сам не поднимай. На «многодетная семья» и жалобный повод скидку "
            "не увеличивай. Финальную сумму в чате не называй. Номер в этом ходе "
            "не проси, пока нет другого повода."
        )
    if not nudge.asked_heater(history):
        system += (
            "\n\n# Отопитель не поднимай\n"
            "Клиент про нагреватель, вебасто и отопитель не спрашивал. "
            "Даже на FAW Bestune NAT эти слова сам не пиши."
        )
    listing = human.client_listing(history)
    if listing:
        system += (
            "\n\n# Своя машина уже в чате\n"
            "Клиент прислал объявление своей машины. %s "
            "Не проси ссылку, марку, модель и год повторно."
            % listing
        )
    if human.wants_remote_eval(user_text):
        system += (
            "\n\n# Дистанционная оценка\n"
            "В этой реплике VIN для загрузки истории и номер телефона. "
            "Не меню: не пиши «VIN, а если нет — ссылку или марку». "
            "Ссылку и марку просишь только если клиент сам сказал, что VIN нет."
        )
        if listing:
            system += " Объявление уже есть, VIN всё равно нужен."
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
            "клиента фактами. Дальше система сама напомнит клиенту, если он замолчит. "
            "Диалог при этом не обрывай: маркер передачи человеку ставится только "
            "когда клиент прямо отказался давать номер и требует ответа в чате."
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
    if nudge.wants_write_here(user_text) or nudge.history_wants_write_here(prior):
        system += (
            "\n\n# Клиент просит писать, не звонить\n"
            "Звонки не проходят или просит ответить здесь. Это не отказ. "
            "Ответь фактом в чат, если он есть в карточке. Телефон чтобы "
            "позвонить не проси. Нужен отчёт или файл без ссылки — "
            "«напишите Телеграм или Ватсап, туда пришлю». "
            "«Наберу и расскажу» и «скиньте номер, если звонок неудобен» нельзя."
        )
    if nudge.refusals_count(history) >= 2:
        system += (
            "\n\n# Клиент отказал в номере второй раз\n"
            "Уговаривать нельзя, третьей попытки нет. Клиенту в этом ходе "
            "ничего не пиши. Поставь только маркер %s последней строкой."
            % prompt.HANDOFF_MARK
        )
    elif nudge.refuses_phone(user_text) and not nudge.wants_write_here(user_text):
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
    if nudge.asked_noncar_trade(user_text):
        system += (
            "\n\n# Обмен не на машину\n"
            "Клиент предлагает станок, картину, квартиру или просит найти "
            "покупателя на своё имущество. Одно сообщение, только граница: "
            "работаем с денежным расчётом или обменом на автомобиль, "
            "остальное и посредничество не рассматриваем. Второго сообщения "
            "нет. Номер не проси. Не уговаривай купить за наличные. "
            "Дальше молчи, догон не нужен."
        )
    return system


async def _generate(history: list[dict], chat_id: str = "") -> str:
    """Реплика модели с проверками на утечку правил и дословные повторы."""
    system = _build_system(history, chat_id)
    raw = await llm.reply(system, history)
    log.info(
        "чат %s: сырой ответ %s",
        chat_id,
        " | ".join((raw or "").splitlines())[:400],
    )
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
            "Напишите марку или бюджет, подберу из того, что есть"
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


def is_llm_pause_reason(reason: str) -> bool:
    """Пауза из-за сбоя модели, не решение менеджера.

    _handoff пишет «эскалация: llm». Старый код ждал точную строку
    «LLM недоступен» и никогда не снимал паузу после пополнения ключа.
    """
    text = (reason or "").strip().lower()
    if not text:
        return False
    if text == TECH_PAUSE_REASON.lower():
        return True
    return text == "llm" or text.endswith(": llm")


def waiting_for_bot(chat_id: str) -> bool:
    """Клиент ждёт ответ: последнее слово за ним, человек чат не забрал."""
    info = store.pause_info(chat_id)
    if store.is_paused(chat_id) and not is_llm_pause_reason(info.get("reason") or ""):
        return False
    doc = store.load_doc(chat_id)
    alert = ((doc.get("crm") or {}).get("alert") or {})
    if alert.get("picked") in {"button", "chat", "call", "stage"}:
        return False
    hist = list(doc.get("messages") or [])
    if not hist or hist[-1].get("role") != "user":
        return False
    last = str(hist[-1].get("content") or "")
    if not nudge.needs_reply(last):
        return False
    if "спасибо" in last.lower() and "?" not in last:
        return False
    return True


async def catchup_waiting() -> int:
    """После простоя ключа отвечаем тем, кто уже написал и не получил ответ."""
    for _ in range(40):
        if CHANNELS.get("tg") and (CHANNELS.get("avito") or CHANNELS.get("autoru")):
            break
        await asyncio.sleep(0.5)
    queued = [cid for cid in store.all_chat_ids() if waiting_for_bot(cid)]
    log.info("догон очереди: %d чатов ждут ответа", len(queued))
    sent = 0
    for cid in queued:
        # Догон идёт по кругу, а не только на старте: чат, которому бот уже
        # отвечает в эту секунду, трогать нельзя, иначе клиент получит дубль.
        running = tasks.get(str(cid))
        if str(cid) in inflight or (running and not running.done()):
            continue
        if is_llm_pause_reason(store.pause_info(cid).get("reason") or ""):
            store.resume(cid)
        llm_fails.pop(str(cid), None)
        channel = _nudge_channel(cid)
        if channel is None:
            log.warning("догон очереди: нет канала для %s", cid)
            continue
        last = str((store.load_history(cid) or [{}])[-1].get("content") or "")
        try:
            await _answer_locked(channel, cid, [last])
            hist = store.load_history(cid)
            if hist and hist[-1].get("role") == "assistant":
                sent += 1
                log.info("догон очереди: ответил %s", cid)
            else:
                log.warning("догон очереди: %s без исходящего", cid)
        except Exception:
            log.exception("догон очереди: %s упал", cid)
        await asyncio.sleep(1.2)
    log.info("догон очереди: готово, ответил %d", sent)
    return sent


async def catchup_loop() -> None:
    """Ключ ожил — возвращаемся к клиенту сами, без рестарта и ручного запуска.

    Догон очереди раньше был только на старте процесса. Лимит ключа выбивало
    днём, чат с одним сбоем модели оставался без ответа, и лид ждал, пока
    кто-то вспомнит про tools/catchup_waiting.py.
    """
    while True:
        await asyncio.sleep(CATCHUP_EVERY_SEC)
        try:
            if not any(waiting_for_bot(cid) for cid in store.all_chat_ids()):
                continue
            await catchup_waiting()
        except Exception:
            log.exception("догон очереди упал")


def tech_pause_lifted(chat_id: int) -> bool:
    """Снимает паузу, поставленную сбоем модели, когда пауза уже отстоялась.

    Паузу от владельца (/human) и передачу человеку не трогаем: там молчание -
    осознанное решение.
    """
    info = store.pause_info(chat_id)
    if not is_llm_pause_reason(info.get("reason") or ""):
        return False
    at = info.get("at")
    if isinstance(at, datetime):
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - at < timedelta(minutes=TECH_PAUSE_MIN):
            return False
    store.resume(chat_id)
    llm_fails.pop(str(chat_id), None)
    log.info("чат %s: снял техническую паузу, пробую отвечать", chat_id)
    return True


def _refresh_nudge(chat_id: int, history: list[dict]) -> None:
    doc = store.load_doc(chat_id)
    doc["nudge"] = nudge.refresh(doc.get("nudge") or {}, history)
    store.save_doc(chat_id, doc)


def _nudge_channel(chat_id: int | str):
    key = str(chat_id)
    if key.startswith("av:"):
        return CHANNELS.get("avito")
    if key.startswith("ar:"):
        return CHANNELS.get("autoru")
    return CHANNELS.get("tg")


async def send_nudge(chat_id: int | str) -> None:
    if not store.is_dialog_id(chat_id):
        return
    if store.is_paused(chat_id) and not tech_pause_lifted(chat_id):
        return
    if pending.get(str(chat_id)):
        return
    running = tasks.get(str(chat_id))
    if running and not running.done():
        return
    channel = _nudge_channel(chat_id)
    if channel is None:
        return
    doc = store.load_doc(chat_id)
    history = list(doc.get("messages") or [])
    if nudge.should_stop_nudge(history):
        meta = dict(doc.get("nudge") or {})
        if meta.get("waiting"):
            meta["waiting"] = False
            doc["nudge"] = meta
            store.save_doc(chat_id, doc)
        return
    meta = doc.get("nudge") or {}
    step = nudge.ready_to_send(meta)
    if not step:
        return
    used = nudge.used_phone_lines(doc.get("messages") or [])
    said_stock = nudge.history_said_in_stock(doc.get("messages") or [])
    text = nudge.build_text(
        step,
        meta.get("name") or "",
        meta.get("car") or "",
        used,
        asked=bool(meta.get("asked", True)),
        address=not nudge.history_has_address(doc.get("messages") or []),
        said_stock=said_stock,
    )
    await type_and_wait(channel, chat_id, human.typing_delay(text, first=True))
    if pending.get(str(chat_id)) or store.is_paused(chat_id):
        return
    doc = store.load_doc(chat_id)
    meta = doc.get("nudge") or {}
    if nudge.ready_to_send(meta) != step:
        return
    try:
        await channel.send(chat_id, text)
    except Exception as exc:  # noqa: BLE001
        if not chat_gone(str(exc)):
            raise
        meta["waiting"] = False
        doc["nudge"] = meta
        store.save_doc(chat_id, doc)
        log.info("догон чат %s: чат недоступен (%s), больше не пишу", chat_id, exc)
        return
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
    crm.dismiss_llm_alert(chat_id)
    log.info("догон чат %s шаг %s", chat_id, step)


async def nudge_loop(tg: Telegram) -> None:
    CHANNELS["tg"] = tg
    while True:
        await asyncio.sleep(60)
        if not settings.nudge_enabled:
            continue
        for chat_id in store.all_chat_ids():
            try:
                await send_nudge(chat_id)
            except Exception:
                log.exception("догон чат %s упал", chat_id)


def schedule(channel, chat_id) -> None:
    key = str(chat_id)
    if key in inflight:
        return
    old = tasks.get(key)
    if old and not old.done():
        old.cancel()
    tasks[key] = asyncio.create_task(answer(channel, key))


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


async def budget_loop(tg: Telegram) -> None:
    """Раз в полчаса смотрим остаток по ключу: предупреждаем на подходе к нулю
    и напоминаем каждый час, пока ключ на нуле и агент молчит."""
    warned = False
    dead_at: datetime | None = None
    while True:
        try:
            info = await llm.key_budget()
            left = info.get("remaining")
            if isinstance(left, (int, float)):
                # Ключ на нуле - агент молчит во всех чатах, и одного утреннего
                # предупреждения тут мало: напоминаем, пока лимит не поднят.
                now = datetime.now(timezone.utc)
                if left <= 0:
                    if dead_at is None or now - dead_at >= timedelta(hours=1):
                        dead_at = now
                        await tg.notify_owner(
                            "Ключ OpenRouter кончился: лимит %s долларов выбран весь. "
                            "Агент не отвечает ни в одном чате, клиенты ждут. "
                            "Подними лимит ключа." % info.get("limit")
                        )
                        log.error("ключ кончился, агент молчит: лимит %s", info.get("limit"))
                elif dead_at is not None:
                    dead_at = None
                    log.info("ключ снова с остатком %.2f, отвечаем", left)
                if 0 < left <= BUDGET_WARN_USD and not warned:
                    warned = True
                    await tg.notify_owner(
                        "Кредиты OpenRouter кончаются: осталось %.2f из %s долларов. "
                        "Пополни лимит ключа, иначе агент замолчит во всех чатах."
                        % (left, info.get("limit"))
                    )
                    log.warning("остаток по ключу %.2f доллара", left)
                elif left > BUDGET_WARN_USD * 2:
                    warned = False
        except Exception as exc:
            log.warning("остаток по ключу не проверил: %s", exc)
        await asyncio.sleep(1800)


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


async def poll_avito(tg: Telegram) -> None:
    if not settings.avito_enabled:
        log.info("авито выключен")
        return
    if not (settings.avito_client_id and settings.avito_client_secret and settings.avito_user_id):
        log.warning("авито включён, но нет AVITO_CLIENT_ID / SECRET / USER_ID")
        return
    api = Avito()
    channel = avito_loop.AvitoChannel(api, tg)
    CHANNELS["avito"] = channel
    try:
        await avito_loop.prime_cursor(api)
        await avito_loop.adopt_shot(api)
        await avito_loop.adopt_profile_chat(api)
        log.info("авито опрос каждые %s сек", settings.avito_poll_sec)
        while True:
            try:
                await avito_loop.poll_once(api, channel, schedule, pending)
            except httpx.HTTPError as exc:
                # Таймаут до Авито лечится следующим опросом. Трейсбек на него
                # только прячет настоящие поломки в логе.
                log.warning("авито опрос: сеть, %s", exc)
            except Exception:
                log.exception("авито опрос упал")
            await asyncio.sleep(max(settings.avito_poll_sec, 3.0))
    finally:
        await api.close()


async def poll_autoru(tg: Telegram) -> None:
    if not settings.autoru_enabled:
        log.info("авто.ру выключен")
        return
    if not (settings.autoru_vertis_key and settings.autoru_session_id):
        log.warning("авто.ру включён, но нет AUTORU_VERTIS_KEY / AUTORU_SESSION_ID")
        return
    if not settings.autoru_cabinet_cookie:
        log.warning("авто.ру: нет AUTORU_CABINET_COOKIE, чаты салона в кабинете не видны")
        return
    expire = (settings.autoru_session_expire or "")[:10]
    if expire:
        log.info("авто.ру сессия до %s", expire)
    api = Autoru()
    channel = autoru_loop.AutoruChannel(api, tg)
    CHANNELS["autoru"] = channel
    try:
        await autoru_loop.prime_cursor(api)
        log.info("авто.ру опрос каждые %s сек", settings.autoru_poll_sec)
        while True:
            try:
                await autoru_loop.poll_once(api, channel, schedule, pending)
            except httpx.HTTPError as exc:
                log.warning("авто.ру опрос: сеть, %s", exc)
            except Exception:
                log.exception("авто.ру опрос упал")
            await asyncio.sleep(max(settings.autoru_poll_sec, 3.0))
    finally:
        await api.close()


async def alert_loop() -> None:
    while True:
        try:
            await crm.tick()
        except Exception:
            log.exception("алерты тик")


async def run() -> None:
    if not settings.telegram_token:
        raise SystemExit("нет TELEGRAM_BOT_TOKEN в .env")
    if not settings.openrouter_key:
        raise SystemExit("нет OPENROUTER_API_KEY в .env")

    tg = Telegram(settings.telegram_token)
    alerts = AlertBot()
    crm.set_bot(alerts)
    me = await tg.me()
    log.info("бот @%s на модели %s", me.get("username"), settings.model)
    if alerts.token and not alerts.chats:
        log.warning("алерты: бот есть, чата нет. Напиши @divoalertbot в группу менеджеров")

    asyncio.create_task(stock_loop())
    asyncio.create_task(autoteka_loop())
    asyncio.create_task(nudge_loop(tg))
    asyncio.create_task(budget_loop(tg))
    asyncio.create_task(poll_avito(tg))
    asyncio.create_task(poll_autoru(tg))
    asyncio.create_task(catchup_waiting())
    asyncio.create_task(catchup_loop())
    asyncio.create_task(alert_loop())
    asyncio.create_task(amojo_http.serve())
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
                    log.info("чат %s: стикер или пустое, молчу", chat_id)
                    continue

                store.log_line(chat_id, "клиент", text)
                if text.startswith("/") and await handle_command(tg, chat_id, text):
                    continue
                if store.is_paused(chat_id) and not tech_pause_lifted(chat_id):
                    if store.hard_paused(chat_id):
                        log.info("чат %s на паузе, молчим", chat_id)
                        await crm.client_wrote_again(chat_id, text)
                        continue
                pending.setdefault(str(chat_id), []).append(text)
                urgent = await crm.capture_if_urgent(chat_id, pending[str(chat_id)])
                if urgent in crm.PAUSE_REASONS:
                    await crm.ack_callback(tg, chat_id, pending[str(chat_id)], urgent)
                    store.pause(chat_id, "эскалация: %s" % urgent)
                    if store.hard_paused(chat_id):
                        pending.pop(str(chat_id), None)
                        continue
                schedule(tg, str(chat_id))
            if updates:
                write_offset(offset)
            await asyncio.sleep(0.2)
    finally:
        await alerts.close()
        await tg.close()


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        log.info("остановлен вручную")


if __name__ == "__main__":
    main()
