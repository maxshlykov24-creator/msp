"""Разовая рассылка актуальных формулировок алертов в группу менеджеров."""
from __future__ import annotations

import asyncio
import sys

from bot import crm
from bot.alerts import AlertBot, WAIT_CALL, WAIT_CHAT, format_alert, take_keyboard

LEAD = "https://divomotors.amocrm.ru/leads/detail/00000000"
PAUSE = 1.2


def call_snap() -> dict:
    return {
        "wait": "call",
        "name": "Иван",
        "phone": "79001112233",
        "car": "BMW X6 2024, 48 186 км",
        "channel": "Авито",
        "reason": "phone",
        "brief": "Про ДТП и кузов, хочет на осмотр. ДТП не было, кузов не чинили. Показ сегодня после 16, Автозаводская 18.",
        "lead_url": LEAD,
    }


def chat_snap() -> dict:
    return {
        "wait": "chat",
        "name": "Бирбек",
        "car": "Geely Coolray 2023",
        "channel": "Telegram",
        "reason": "handoff",
        "brief": "Хочет в кредит. Эти автомобили продаём за наличный расчёт, детали по звонку.",
        "lead_url": LEAD,
    }


async def main() -> int:
    bot = AlertBot()
    crm.set_bot(bot)
    if not bot.ready:
        print("нет ALERT_CHAT_ID или токена", file=sys.stderr)
        await bot.close()
        return 1
    call = call_snap()
    chat = chat_snap()
    first = await bot.send(format_alert(call, 0), take_keyboard("demo", WAIT_CALL))
    print("1 first", "ok" if first else "FAIL")
    if not first:
        await bot.close()
        return 1
    await asyncio.sleep(PAUSE)
    ping = await bot.send(format_alert(call, 5), take_keyboard("demo", WAIT_CALL))
    print("2 ping5", "ok" if ping else "FAIL")
    if not ping:
        await bot.close()
        return 1
    if first[1] != ping[1]:
        await bot.delete(first[0], first[1])
    await asyncio.sleep(PAUSE)
    third = await bot.send(format_alert(chat, 0), take_keyboard("demo", WAIT_CHAT))
    print("3 chat", "ok" if third else "FAIL")
    await bot.close()
    return 0 if third else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
