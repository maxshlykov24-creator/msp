"""Отправка результата в Telegram. api.telegram.org из РФ может требовать прокси (exit-node)."""
from config import settings
from net import proxied_client

MAX = 4000  # лимит Telegram ~4096; режем с запасом и шлём частями


def send(text: str, chat_id: str | None = None) -> bool:
    token = settings.telegram_bot_token
    chat = chat_id or settings.telegram_chat_id
    if not token or not chat:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    ok = True
    with proxied_client(timeout=30) as cli:
        for chunk in _split(text, MAX):
            body = {"chat_id": chat, "text": chunk,
                    "parse_mode": "HTML", "disable_web_page_preview": True}
            r = cli.post(url, json=body)
            ok = ok and r.status_code == 200
    return ok


def _split(text: str, size: int) -> list[str]:
    if len(text) <= size:
        return [text]
    parts, buf = [], ""
    for line in text.split("\n"):
        if len(buf) + len(line) + 1 > size:
            if buf:
                parts.append(buf)
            buf = line
        else:
            buf = f"{buf}\n{line}" if buf else line
    if buf:
        parts.append(buf)
    return parts
