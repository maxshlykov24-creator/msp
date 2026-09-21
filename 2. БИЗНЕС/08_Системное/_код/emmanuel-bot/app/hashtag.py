"""Отчёт: хэштег имени или шаблон без хэштега. Кто писал — всегда Telegram-аккаунт."""

from __future__ import annotations

import re

HASHTAG_RE = re.compile(r"#[^\s#]{1,64}")
DIGEST_HASHTAG = "#рвыотчёт"
_SKIP_TAGS = frozenset({"#рвыотчёт", "#рвыотчет"})

# Узкий шаблон живого отчёта. Не «любое 1.», чтобы не зачесть объявление или обычный чат.
_ITEM_ONE = re.compile(r"(?:^|[\n\r])\s*1\s*[.)]")
_TEMPLATE = re.compile(
    r"(духовн\w*\s+рост|час(?:ы|ов)?\s+молитв|сколько\s+времени\s+потратил|"
    r"можем\s+тебе\s+помочь|обхватил|постил(?:ся|ись)?\s+ли|"
    r"сколько\s+проповед|изучал(?:а)?\s+библи)",
    re.IGNORECASE,
)


def extract_hashtag(text: str | None) -> str | None:
    """Первый хэштег имени. Служебный #рвыотчёт пропускаем."""
    if not text:
        return None
    for m in HASHTAG_RE.finditer(text):
        tag = m.group(0)
        if tag.lower() not in _SKIP_TAGS:
            return tag
    return None


def looks_like_report(text: str | None) -> bool:
    """Отчёт без хэштега: есть пункт 1. и фраза из бланка. Иначе не считаем."""
    t = (text or "").strip()
    if len(t) < 20:
        return False
    if not _ITEM_ONE.search(t) and not re.match(r"\s*1\s*[.)]", t):
        return False
    return bool(_TEMPLATE.search(t))


def tag_from_telegram(
    *,
    username: str | None,
    first_name: str | None,
    last_name: str | None,
    tg_user_id: int,
) -> str:
    """Подпись, если в тексте нет своего хэштега: @ник, иначе имя."""
    un = (username or "").strip().lstrip("@")
    if un:
        return f"#{un[:64]}"
    name = f"{(last_name or '').strip()}{(first_name or '').strip()}".replace(" ", "")
    name = re.sub(r"[^\w]+", "", name, flags=re.UNICODE)
    if name:
        return f"#{name[:64]}"
    return f"#{int(tg_user_id)}"


def override_from_tag(tag: str) -> str:
    """Хэштег из группы → значение для users.report_hashtag_override без #."""
    return re.sub(r"^#+", "", (tag or "").strip())[:120]
