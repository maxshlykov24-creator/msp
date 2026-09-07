"""Человеческий слой: дробление реплики, чистка форматирования, задержки набора."""
from __future__ import annotations

import random
import re

from bot.config import settings

MAX_BUBBLES = 3
MARKDOWN_NOISE = re.compile(r"(\*\*|__|`|#+\s*)")
BULLET = re.compile(r"^\s*[-*•]\s+")
NUMBERED = re.compile(r"^\s*\d+[.)]\s+")
# Карточка машины: год, пробег, цена, VIN, «в наличии». Такие цифры
# человек не печатает с той же скоростью, что «добрый день».
# Модель иногда проговаривает свои правила вместо ответа клиенту.
LEAK = re.compile(
    r"("
    r"1-3 short|no markdown|no asterisks|CHANNEL_RULES|"
    r"markdown\?|техрегламент|системн(ый|ого) промпт|"
    r"\[\[ЧЕЛОВЕК\]\]|никакого markdown|пустой строкой:"
    r")",
    re.IGNORECASE,
)
URL = re.compile(r"https?://\S+")
CAR_FACTS = re.compile(
    r"(\b\d{4}\s*г|пробег|\bкм\b|₽|руб|млн|тыс\.|vin|в наличии|комплектац)",
    re.IGNORECASE,
)


def drop_end_period(text: str) -> str:
    """Сообщение в чате без точки в конце. Многоточие, ? и ! не трогаем."""
    text = (text or "").rstrip()
    if text.endswith("...") or text.endswith("…"):
        return text
    if text.endswith(".") and not text.endswith(".."):
        return text[:-1].rstrip()
    return text


def for_chat(text: str) -> str:
    """Как пишет человек в телефоне: дефис вместо длинного тире, без точки в конце."""
    text = (text or "").replace("—", "-").replace("–", "-")
    return drop_end_period(text)


def clean(text: str) -> str:
    """Убрать markdown, который модель тащит из KB. В чате его быть не должно."""
    out = []
    for line in text.splitlines():
        line = MARKDOWN_NOISE.sub("", line)
        line = BULLET.sub("", line)
        line = NUMBERED.sub("", line)
        out.append(line.rstrip())
    return "\n".join(out).strip()


def split_bubbles(text: str) -> list[str]:
    """Пустая строка = граница сообщения. Больше трёх пузырей не отправляем."""
    blocks = [b.strip() for b in re.split(r"\n\s*\n", clean(text)) if b.strip()]
    if not blocks:
        return []
    if len(blocks) > MAX_BUBBLES:
        head = blocks[: MAX_BUBBLES - 1]
        head.append(" ".join(blocks[MAX_BUBBLES - 1:]))
        blocks = head
    return [for_chat(b.replace("\n", " ").strip()) for b in blocks]


def looks_like_leak(text: str) -> bool:
    """Чеклист правил или английский разбор инструкции — клиенту такое не слать."""
    if not text:
        return False
    if LEAK.search(text):
        return True
    # Ссылка автотеки — это латиница, но не английский текст. Считаем без неё,
    # иначе короткий ответ «отчёт по ссылке» уходил в заглушку про прайс.
    body = URL.sub(" ", text)
    latin = len(re.findall(r"[A-Za-z]", body))
    cyr = len(re.findall(r"[А-Яа-яЁё]", body))
    return latin > 20 and latin > cyr * 2


def is_car_facts(text: str) -> bool:
    return bool(CAR_FACTS.search(text or ""))


def typing_delay(text: str, *, first: bool) -> float:
    """Пауза перед сообщением: думает, потом набирает. С разбросом, не метроном.

    Карточка машины дольше: сначала глянул в сток, потом набирает цифры
    медленнее обычного текста.
    """
    cps = max(settings.typing_cps, 1.0)
    extra = 0.0
    if first:
        extra += random.uniform(1.2, 2.4)
    if is_car_facts(text):
        extra += random.uniform(1.5, 3.0)
        cps = max(cps * 0.8, 8.0)
    base = len(text) / cps + extra
    jitter = random.uniform(0.9, 1.2)
    return max(settings.delay_min_sec, min(settings.delay_max_sec, base * jitter))
