"""Разовая рассылка всех текущих формулировок алертов в группу менеджеров."""
from __future__ import annotations

import asyncio
import sys

from bot import crm
from bot.alerts import REASON_LINE, AlertBot, format_alert

LEAD = "https://divomotors.amocrm.ru/leads/detail/00000000"
PAUSE = 0.45


def samples() -> list[str]:
    out = [
        "🧪 <b>Тест формулировок</b> | DIVO\n"
        "Ниже все шаблоны, которые бот реально шлёт. Это не живые клиенты.",
    ]
    base_call = {
        "wait": "call",
        "name": "Иван",
        "phone": "79001112233",
        "car": "BMW X6 2024, 48 186 км",
        "channel": "Авито",
        "reason": "phone",
        "ask": "Машина в наличии? Можно на сегодня?",
        "lead_url": LEAD,
    }
    base_chat = {
        "wait": "chat",
        "name": "Бирбек",
        "car": "Geely Coolray 2023",
        "channel": "Telegram",
        "reason": "handoff",
        "ask": "Сколько микрон бьётся?",
        "lead_url": LEAD,
    }
    for ping in (0, 5, 10, 15):
        out.append(format_alert(base_call, ping))
    for ping in (0, 5, 10, 15):
        out.append(format_alert(base_chat, ping))
    for reason, label in REASON_LINE.items():
        wait = "call" if reason in {"phone", "call"} else "chat"
        snap = {
            "wait": wait,
            "name": "Тест",
            "phone": "79001112233" if wait == "call" else "",
            "car": "FAW Bestune NAT 2023",
            "channel": "Авито" if wait == "call" else "Telegram",
            "reason": reason,
            "ask": "повод: %s" % label,
            "lead_url": LEAD,
        }
        out.append(format_alert(snap, 0))
    echo = format_alert(base_chat, 0)
    rest = "\n".join(echo.splitlines()[1:])
    out.append("💬 <b>Клиент пишет, ответа нет</b> | DIVO" + rest)
    out.append(
        "Авито, новый чат.\n"
        "BMW X6 3.0 AT 2024\n"
        "Машина в наличии?\n"
        "Чтобы бот ответил, напиши:\n"
        "/avito on u2i-пример\n"
        "или /avito next и пусть клиент напишет ещё раз"
    )
    out.append("Авито: бот взял чат u2i-пример\nBMW X6 3.0 AT 2024")
    out.append(
        "Клиент просит фото в мессенджер. chat_id=212666249\n"
        "Последнее: скинь фото сзади"
    )
    out.append(
        "Клиент просит видео в мессенджер. chat_id=212666249\n"
        "Последнее: можно видео ЛКП?"
    )
    out.append("LLM не ответил по чату 212666249 (подряд 2): timeout")
    out.append(
        "Кредиты OpenRouter кончаются: осталось 2.75 из 5 долларов. "
        "Пополни лимит ключа, иначе агент замолчит во всех чатах."
    )
    out.append(
        "✅ DIVO: алерты подключены. Сюда придут номер, звонок и эскалация.\n"
        "Группа закреплена, личные сообщения боту больше не дублируют алерты."
    )
    return out


async def main() -> int:
    bot = AlertBot()
    crm.set_bot(bot)
    if not bot.ready:
        print("нет ALERT_CHAT_ID или токена", file=sys.stderr)
        await bot.close()
        return 1
    print("цель:", sorted(bot._targets()), "шаблонов:", len(samples()))
    for i, text in enumerate(samples(), 1):
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
