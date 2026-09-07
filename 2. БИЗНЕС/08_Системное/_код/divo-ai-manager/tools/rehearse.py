#!/usr/bin/env python3
"""Репетиции без Telegram: гоняем сценарии через тот же промпт и ту же модель.

Запуск: tools/rehearse.py [номер сценария ...]
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot import human, llm, main as bot_main, nudge, prompt  # noqa: E402

SCENARIOS: list[tuple[str, list[str]]] = [
    (
        "Машина есть в стоке",
        [
            "Здравствуйте, Джили Кулрей 2023 белый еще актуален?",
            "Максим. А пробег какой и цена окончательная?",
            "Хорошо, а когда можно приехать посмотреть?",
        ],
    ),
    (
        "Машины нет в стоке",
        [
            "Добрый день! Интересует BMW X5 2021 года, есть у вас?",
            "А что-то похожее по бюджету до 3 миллионов есть?",
        ],
    ),
    (
        "Торг и скидка",
        [
            "Мерседес GLE купе за 12.7 — это много. Скидку какую дадите?",
            "Ну хотя бы примерно скажите, сколько скинете, чтобы я ехал не зря",
        ],
    ),
    (
        "Просит человека",
        [
            "Слушайте, вы бот? Дайте живого менеджера",
        ],
    ),
    (
        "Trade-in и кредит",
        [
            "Хочу Хавал Джолион в кредит, свою Гранту в трейд-ин. Что по ставке?",
            "Гранта 2019, пробег 90 тысяч, один хозяин. Сколько дадите?",
        ],
    ),
    # Ниже — реальные провалы из диалога 5 сентября. Смотрим, что бот больше
    # не выдумывает ТТХ, историю, оценку и проценты, а берёт номер.
    (
        "Чего нет в карточке: запас хода и обкатка",
        [
            "По Нату хочу узнать",
            "Николай. Запас хода какой?",
            "Где ее обкатывали? Пробег по Китаю?",
        ],
    ),
    (
        "Лицензия такси и автотека",
        [
            "Нат смотрю. Лицензия такси есть?",
            "Почему лицензия по автотеке бьется?",
        ],
    ),
    (
        "Кредит на NAT — пометка «только наличка»",
        [
            "Нат могу в кредит купить?",
        ],
    ),
    (
        "Оценка своей машины в чате",
        [
            "Мазду на обмен за сколько возьмете? 2018 год, 2.5 л, 60 тысяч пробег",
            "Напишите здесь цену. О123вм134 номер",
            "Когда ответ ждать?",
        ],
    ),
    (
        "Оплата картой: комиссия эквайринга",
        [
            "Нат хочу картой оплатить по итогу. Можно же так сделать?",
            "Какая комиссия при оплате картой?",
        ],
    ),
    (
        "Адрес один раз за диалог",
        [
            "Где вы находитесь и до скольки работаете?",
            "Хорошо. А Хавал Джолион у вас есть?",
            "Пробег какой у него?",
            "Ладно, подумаю",
        ],
    ),
    (
        "Машина со склада, но не в продаже",
        [
            "У вас Cullinan есть в продаже?",
            "Так а объявление ваше висит. Он что продан?",
        ],
    ),
]


async def run_one(title: str, turns: list[str]) -> None:
    print("\n" + "=" * 70)
    print("СЦЕНАРИЙ: %s" % title)
    print("=" * 70)
    history: list[dict] = []
    for text in turns:
        print("\nКЛИЕНТ: %s" % text)
        history.append({"role": "user", "content": text})
        try:
            # Тот же путь, что у бота: поправки под ход диалога и те же фильтры.
            raw = await bot_main._generate(history)
        except llm.LlmError as exc:
            print("!! LLM: %s" % exc)
            return
        handoff = prompt.HANDOFF_MARK in raw
        clean = raw.replace(prompt.HANDOFF_MARK, "")
        for mark in (prompt.MEDIA_PHOTO_MARK, prompt.MEDIA_VIDEO_MARK):
            clean = clean.replace(mark, "")
        bubbles = human.split_bubbles(clean)
        if nudge.history_has_address(history) and not nudge.asked_where(text):
            kept = [b for b in bubbles if not nudge.is_address_only(b)]
            if kept and len(kept) < len(bubbles):
                print("(выкинут повторный адрес)")
                bubbles = kept
        for bubble in bubbles:
            print("НИКИТА: %s" % bubble)
        if handoff:
            print(">>> ЭСКАЛАЦИЯ: диалог уходит человеку, агент замолкает")
        history.append({"role": "assistant", "content": " ".join(bubbles)})
        if handoff:
            return


async def main() -> None:
    system = prompt.build()
    print("Системный промпт: %d символов. Сток: %s" % (len(system), prompt.stock_status()))
    picked = [int(a) for a in sys.argv[1:] if a.isdigit()]
    for i, (title, turns) in enumerate(SCENARIOS, start=1):
        if picked and i not in picked:
            continue
        await run_one("%d. %s" % (i, title), turns)


if __name__ == "__main__":
    asyncio.run(main())
