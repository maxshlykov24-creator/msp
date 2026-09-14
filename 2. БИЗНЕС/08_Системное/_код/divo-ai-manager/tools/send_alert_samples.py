"""Разовая рассылка актуальных формулировок алертов в группу менеджеров."""
from __future__ import annotations

import asyncio
import sys

from bot import crm
from bot.alerts import AlertBot, format_alert

LEAD = "https://divomotors.amocrm.ru/leads/detail/00000000"
PAUSE = 1.2

THREAD_CALL = [
    ["Клиент", "BMW X6 ещё продаёте? Можно сегодня посмотреть?"],
    ["Никита", "Да, в наличии на Крылатской. Могу принять после 16."],
    ["Клиент", "Кузов ровный? ДТП не было?"],
    ["Никита", "ДТП не было, кузов не чинили."],
    ["Клиент", "Ок, 8 900 111-22-33, перезвоните пожалуйста"],
]
THREAD_CHAT = [
    ["Клиент", "Coolray ещё есть? Сколько микрон бьётся?"],
    ["Никита", "Да, в наличии. По микронам сниму видео толщиномером и пришлю в WhatsApp."],
    ["Клиент", "Давайте живого человека, я не с ботом хочу"],
]
THREAD_ECHO = THREAD_CHAT + [["Клиент", "Алло, вы на связи? Жду ответ"]]


def samples() -> list[str]:
    call = {
        "wait": "call",
        "name": "Иван",
        "phone": "79001112233",
        "car": "BMW X6 2024, 48 186 км",
        "channel": "Авито",
        "reason": "phone",
        "thread": THREAD_CALL,
        "lead_url": LEAD,
    }
    chat = {
        "wait": "chat",
        "name": "Бирбек",
        "car": "Geely Coolray 2023",
        "channel": "Telegram",
        "reason": "handoff",
        "thread": THREAD_CHAT,
        "lead_url": LEAD,
    }
    echo_snap = dict(chat)
    echo_snap["thread"] = THREAD_ECHO
    echo = format_alert(echo_snap, 0)
    rest = "\n".join(echo.splitlines()[1:])
    return [
        "🧪 <b>Новые формулировки</b> | DIVO\n"
        "Тест, не живые клиенты. Старые шаблоны из чата убрал.",
        format_alert(call, 0),
        format_alert(call, 15),
        format_alert(chat, 0),
        format_alert(chat, 10),
        "💬 <b>Клиент пишет, ответа нет</b> | DIVO" + rest,
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
    for i, text in enumerate(rows, skip + 1):
        ok = await bot.send(text)
        print(i, "ok" if ok else "FAIL")
        if not ok:
            await bot.close()
            return 1
        await asyncio.sleep(PAUSE)
    await bot.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
