"""Разовая рассылка актуальных формулировок алертов в группу менеджеров."""
from __future__ import annotations

import asyncio
import sys

from bot import crm
from bot.alerts import AlertBot, WAIT_CALL, WAIT_CHAT, format_alert, take_keyboard

LEAD = "https://divomotors.amocrm.ru/leads/detail/00000000"
PAUSE = 1.2


def samples() -> list:
    call = {
        "wait": "call",
        "name": "Иван",
        "phone": "79001112233",
        "car": "BMW X6 2024, 48 186 км",
        "channel": "Авито",
        "reason": "phone",
        "brief": "уже сказали: в наличии на Крылатской, ДТП не было; сейчас: оставил номер",
        "lead_url": LEAD,
    }
    chat = {
        "wait": "chat",
        "name": "Бирбек",
        "car": "Geely Coolray 2023",
        "channel": "Telegram",
        "reason": "handoff",
        "brief": "уже сказали: снимем видео толщиномером в WhatsApp; сейчас: нужен живой менеджер",
        "lead_url": LEAD,
    }
    echo = format_alert(chat, 0)
    rest = "\n".join(echo.splitlines()[1:])
    return [
        (
            "🧪 <b>Короткий контекст</b> | DIVO\n"
            "Тест. Цитат клиента в алерте больше нет.",
            None,
        ),
        (format_alert(call, 0), take_keyboard("demo", WAIT_CALL)),
        (format_alert(chat, 0), take_keyboard("demo", WAIT_CHAT)),
        ("💬 <b>Клиент пишет, ответа нет</b> | DIVO" + rest, take_keyboard("demo", WAIT_CHAT)),
    ]


async def main() -> int:
    bot = AlertBot()
    crm.set_bot(bot)
    if not bot.ready:
        print("нет ALERT_CHAT_ID или токена", file=sys.stderr)
        await bot.close()
        return 1
    skip = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    rows = samples()[skip:]
    print("цель:", sorted(bot._targets()), "шаблонов:", len(rows), "с", skip + 1)
    for i, (text, markup) in enumerate(rows, skip + 1):
        ok = await bot.send(text, markup)
        print(i, "ok" if ok else "FAIL")
        if not ok:
            await bot.close()
            return 1
        await asyncio.sleep(PAUSE)
    await bot.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
