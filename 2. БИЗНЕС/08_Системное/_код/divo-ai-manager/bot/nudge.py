"""Догон по номеру, если клиент замолчал.

Три касания, потом тишина. Тексты фиксированные — без модели, без обещаний.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from bot.config import settings

MSK = ZoneInfo("Europe/Moscow")
MAX_NUDGES = 3

ASKED_PHONE = re.compile(
    r"(номер(а)? (телефона )?для связи|напишите.{0,40}номер|"
    r"ваш номер телефона|контактный телефон|скиньте.{0,30}номер|"
    # Формулировка из эталона менеджера: без слова «номер», но это тот же
    # запрос телефона, и в лимит попыток он обязан попадать.
    r"по какому (телефону|номеру)|оставьте.{0,30}(номер|телефон))",
    re.IGNORECASE,
)
HAS_PHONE = re.compile(
    r"(?:\+?7|8)[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}|\b\d{10,11}\b"
)
STOP = re.compile(
    r"(подумаю|не актуально|сам разберусь|не надо|не интересно|уже купил|отстань)",
    re.IGNORECASE,
)
REFUSES_PHONE = re.compile(
    r"("
    r"не (хочу|буду) (оставлять|давать|скидывать).{0,20}номер|"
    r"номер.{0,20}не (дам|оставлю|скажу|скину)|"
    r"не (дам|оставлю|скажу|скину).{0,20}номер|"
    r"свой номер не|"
    r"свой не (дам|оставлю)|"
    r"без (моего )?номер"
    r")",
    re.IGNORECASE,
)
SALON_PHONE = "8 495 089 29 29"
NICE_NAME = re.compile(r"очень приятно,\s*([А-ЯЁA-Z][а-яёa-zA-Z\-']+)", re.IGNORECASE)
ASKED_NAME = re.compile(
    # Модель спрашивает имя разными словами, а повтор одного и того же вопроса
    # клиент читает как «меня не слушают». Ловим все формы.
    r"(как (я )?могу к вам обращать|как к вам обращать|как вас зовут|"
    r"как могу вас (называть|звать)|подскажите (ваше )?имя)",
    re.IGNORECASE,
)
LOOKS_LIKE_NAME = re.compile(
    r"^(?:меня зовут |зовут |я |это )?"
    r"([А-ЯЁA-Z][а-яёa-zA-Z\-']{1,19})"
    r"(?:\s+[А-ЯЁA-Z][а-яёa-zA-Z\-']{1,19})?"
    r"\.?$",
)
NAME_STOP = {
    "дтп", "да", "нет", "ок", "окей", "хорошо", "привет", "гелик", "машина",
    "автотека", "кредит", "лизинг", "торг", "скидка", "фото", "видео",
    "понял", "спасибо", "здравствуйте", "добрый",
}
ADDRESS_SHORT = "мы на Автозаводской 18, ТЦ Ривьера, -2 этаж"
ADDRESS_LINE = re.compile(
    r"(автозаводск|ривьер|минус 2 этаж|-2 этаж|с 10:00 до 20:00|"
    r"работаем ежедневно)",
    re.IGNORECASE,
)
ASKED_WHERE = re.compile(
    r"(где вы|где наход|где смотр|какой адрес|адрес|как добрать|как доехать|"
    r"куда (ехать|приезжать|подъехать)|метро|во сколько (вы )?работа|"
    r"график|до скольки|режим работы|когда открыт)",
    re.IGNORECASE,
)
CAR_BIT = re.compile(
    r"("
    r"coolray|кулре[йи]|panamera|панамер[аы]|macan|макан|"
    r"g-?класс|g-?класс amg|maybach|майбах|gle|gls|glc|"
    r"x[5-7]|camry|камри|land cruiser|крузер|prado|прадо"
    r")",
    re.IGNORECASE,
)
YEAR = re.compile(r"\b(20[12]\d)\b")
COLOR = re.compile(
    r"\b(черн\w+|чёрн\w+|бел\w+|красн\w+|сер\w+|син\w+|зелён\w+|зелен\w+)\b",
    re.IGNORECASE,
)


def now_msk() -> datetime:
    return datetime.now(MSK)


def parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=MSK)
    return dt.astimezone(MSK)


def in_working_hours(moment: datetime | None = None) -> bool:
    local = (moment or now_msk()).astimezone(MSK)
    return settings.nudge_hour_from <= local.hour < settings.nudge_hour_to


def clamp_hours(dt: datetime) -> datetime:
    """До 10:00 и после 20:00 сдвигаем на 11:00 следующего рабочего окна."""
    local = dt.astimezone(MSK)
    morning = settings.nudge_morning_hour
    if local.hour >= settings.nudge_hour_to:
        local = (local + timedelta(days=1)).replace(
            hour=morning, minute=0, second=0, microsecond=0
        )
    elif local.hour < settings.nudge_hour_from:
        local = local.replace(hour=morning, minute=0, second=0, microsecond=0)
    return local


def due_at(last: datetime, step_to_send: int) -> datetime:
    last = last.astimezone(MSK)
    if step_to_send == 1:
        return clamp_hours(last + timedelta(minutes=settings.nudge_wait_1_min))
    if step_to_send == 2:
        return clamp_hours(last + timedelta(minutes=settings.nudge_wait_2_min))
    nxt = (last + timedelta(days=1)).replace(
        hour=settings.nudge_morning_hour, minute=0, second=0, microsecond=0
    )
    return nxt


def has_phone(text: str) -> bool:
    return bool(HAS_PHONE.search(text or ""))


def asked_phone(text: str) -> bool:
    return bool(ASKED_PHONE.search(text or ""))


def wants_stop(text: str) -> bool:
    return bool(STOP.search(text or ""))


def history_has_phone(messages: list[dict]) -> bool:
    return any(m.get("role") == "user" and has_phone(m.get("content") or "") for m in messages)


def history_asked_phone(messages: list[dict]) -> bool:
    return any(
        m.get("role") == "assistant" and asked_phone(m.get("content") or "") for m in messages
    )


def _norm_line(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip(" .!?")


def used_phone_lines(messages: list[dict]) -> list[str]:
    """Фразы, которыми уже просили номер — их нельзя повторять дословно."""
    found: list[str] = []
    seen: set[str] = set()
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        for part in re.split(r"(?<=[.!?])\s+", msg.get("content") or ""):
            line = part.strip()
            if not line or not asked_phone(line):
                continue
            key = _norm_line(line)
            if key and key not in seen:
                seen.add(key)
                found.append(line)
    return found


def repeats_used_phone(text: str, used: list[str]) -> bool:
    blob = _norm_line(text)
    return any(_norm_line(line) and _norm_line(line) in blob for line in used)


def phone_ask_count(messages: list[dict]) -> int:
    """Сколько раз модель сама (не система) уже просила номер в этом диалоге."""
    return sum(
        1 for m in messages if m.get("role") == "assistant" and asked_phone(m.get("content") or "")
    )


def is_thinking(text: str) -> bool:
    """Клиент попросил время/сравнивает — номер в ответ на это не проси."""
    return bool(THINKING.search(text or ""))


def asked_if_bot(text: str) -> bool:
    return bool(ASKED_BOT.search(text or ""))


def refuses_phone(text: str) -> bool:
    return bool(REFUSES_PHONE.search(text or ""))


def history_refuses_phone(messages: list[dict]) -> bool:
    return any(
        m.get("role") == "user" and refuses_phone(m.get("content") or "") for m in messages
    )


def refusals_count(messages: list[dict]) -> int:
    """Сколько раз клиент отказался дать номер. Два - дальше зовём человека."""
    return sum(
        1
        for m in messages
        if m.get("role") == "user" and refuses_phone(m.get("content") or "")
    )


def history_wants_stop(messages: list[dict]) -> bool:
    return any(m.get("role") == "user" and wants_stop(m.get("content") or "") for m in messages)


def asked_name(text: str) -> bool:
    return bool(ASKED_NAME.search(text or ""))


def has_address(text: str) -> bool:
    return bool(ADDRESS_LINE.search(text or ""))


def asked_where(text: str) -> bool:
    """Клиент сам спросил про адрес, дорогу или часы — тогда повтор уместен."""
    return bool(ASKED_WHERE.search(text or ""))


def history_has_address(messages: list[dict]) -> bool:
    return any(
        m.get("role") == "assistant" and has_address(m.get("content") or "") for m in messages
    )


INVITE_LINE = re.compile(
    r"(приезжайте|приходите|подъезжайте|подъехать|посмотреть можно|"
    r"можно посмотреть|можно приехать|приехать посмотреть|посмотреть вживую|"
    r"посмотреть у нас|ждем вас|ждём вас)",
    re.IGNORECASE,
)


def invites(text: str) -> bool:
    return bool(INVITE_LINE.search(text or ""))


def with_address(text: str) -> str:
    """Дописывает адрес к приглашению приехать.

    Модель зовёт смотреть машину и забывает сказать, куда ехать, — клиент в
    ответ спрашивает «а где вы находитесь», и это лишний ход вместо визита.
    Вопрос в конце реплики не перекрываем: адрес встаёт перед ним.
    """
    body = (text or "").strip()
    if not body or has_address(body) or not invites(body):
        return text
    parts = re.split(r"(?<=[.!?])\s+", body)
    tail = ADDRESS_SHORT[0].upper() + ADDRESS_SHORT[1:]
    if parts[-1].rstrip().endswith("?"):
        question = parts.pop()
        head = " ".join(parts).rstrip()
        if head:
            return "%s %s. %s" % (head, tail, question)
        return "%s. %s" % (tail, question)
    return "%s, %s" % (body.rstrip(" .,"), ADDRESS_SHORT)


def is_address_only(text: str) -> bool:
    """Пузырь целиком про адрес и часы: приглашение приехать без нового факта."""
    body = (text or "").strip()
    if not body or not has_address(body):
        return False
    left = ADDRESS_LINE.sub(" ", body)
    left = re.sub(
        r"(приезжайте|приходите|ждем вас|ждём вас|мы находимся|наш адрес|"
        r"находимся|шоурум|салон|тц|этаж|по адресу|к нам|в гости|"
        r"будем рады|адрес|18|,|\.|:|-)",
        " ",
        left,
        flags=re.IGNORECASE,
    )
    return len(re.sub(r"\s+", "", left)) <= 12


def name_from_text(text: str) -> str:
    m = LOOKS_LIKE_NAME.match((text or "").strip())
    if not m:
        return ""
    first = m.group(1)
    if first.lower().replace("ё", "е") in NAME_STOP:
        return ""
    return first[0].upper() + first[1:]


def extract_name(messages: list[dict]) -> str:
    asked = False
    from_user = ""
    for msg in messages:
        text = msg.get("content") or ""
        role = msg.get("role")
        if role == "assistant":
            if asked_name(text):
                asked = True
            found = NICE_NAME.search(text)
            if found:
                return found.group(1)
        elif role == "user":
            guess = name_from_text(text)
            if guess and asked:
                from_user = guess
    return from_user


def used_name_asks(messages: list[dict]) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        for part in re.split(r"(?<=[.!?])\s+", msg.get("content") or ""):
            line = part.strip()
            if not line or not asked_name(line):
                continue
            key = _norm_line(line)
            if key and key not in seen:
                seen.add(key)
                found.append(line.rstrip(" ?."))
    return found


def repeats_used_name_ask(text: str, used: list[str]) -> bool:
    blob = _norm_line(text)
    return any(_norm_line(line) and _norm_line(line) in blob for line in used)


def extract_car(messages: list[dict]) -> str:
    """Коротко: цвет + модель + год, если удаётся снять с последних реплик."""
    for msg in reversed(messages):
        text = msg.get("content") or ""
        model = CAR_BIT.search(text)
        if not model:
            continue
        year = YEAR.search(text)
        color = COLOR.search(text)
        parts = []
        if color:
            raw = color.group(1).lower().replace("ё", "е")
            if raw.startswith("черн"):
                parts.append("чёрный")
            elif raw.startswith("бел"):
                parts.append("белый")
            elif raw.startswith("красн"):
                parts.append("красный")
            elif raw.startswith("сер"):
                parts.append("серый")
            elif raw.startswith("син"):
                parts.append("синий")
        parts.append(model.group(1))
        if year:
            parts.append(year.group(1))
        return " ".join(parts)
    return ""


BOT_REPLIES = (
    "Нет)\n\nДавайте созвонимся с вами. И оперативнее на вопросы смогу ответить и удостоверитесь, что не бот",
    "Не бот))\n\nДавайте трубку возьмём, так быстрее разберёмся",
    "Давайте созвонимся, сами услышите",
    "Напишите номер, наберу. В трубке так проще, чем в переписке",
    "Ок, давайте голосом. Скиньте номер, созвонимся",
)


def used_bot_replies(messages: list[dict]) -> list[str]:
    blob = " ".join(
        _norm_line(m.get("content") or "")
        for m in messages
        if m.get("role") == "assistant"
    )
    found = []
    for reply in BOT_REPLIES:
        if _norm_line(reply.split("\n\n")[0]) in blob or _norm_line(reply) in blob:
            found.append(reply)
    return found


def pick_bot_reply(used: list[str] | None = None) -> str:
    taken = {_norm_line(x) for x in (used or [])}
    for reply in BOT_REPLIES:
        if _norm_line(reply) not in taken:
            return reply
    return BOT_REPLIES[-1]


PHONE_ASKS = (
    "Напишите, пожалуйста, ваш номер телефона для связи",
    "Напишите номер для связи",
    "Напишите, пожалуйста, контактный телефон",
    "Скиньте, пожалуйста, номер телефона",
)

MAX_LIVE_PHONE_ASKS = 3  # дальше только через таймер, не живой моделью
ASKED_BOT = re.compile(
    r"(ты бот|это бот|вы бот|нейросеть|чат.?гпт|chatgpt|ты робот|вы робот|"
    r"ты ии|вы ии|искусственн\w+ интеллект)",
    re.IGNORECASE,
)
THINKING = re.compile(
    r"(рассматрива|подумаю|думаю|посмотрю ещ[её]|пока смотрю|сравнива|"
    r"не актуально|не сейчас)",
    re.IGNORECASE,
)


def pick_phone_ask(used: list[str] | None = None) -> str:
    taken = {_norm_line(line) for line in (used or [])}
    for phrase in PHONE_ASKS:
        if _norm_line(phrase) not in taken:
            return phrase
    return PHONE_ASKS[0]


def build_text(
    step: int,
    name: str,
    car: str,
    used: list[str] | None = None,
    asked: bool = True,
    address: bool = False,
) -> str:
    """Три касания. Второе — про приезд, а не про номер: цель не контакт, а визит.

    asked=False означает, что номер в этом диалоге ещё не просили: клиент просто
    замолчал после ответа по машине. Тогда первое касание тоже мягкое.
    address=True добавляет к приглашению адрес: звать смотреть, не сказав куда, —
    значит получить в ответ «а где вы находитесь» вместо приезда.
    """
    who = (name + ", ") if name else ""
    where = (", " + ADDRESS_SHORT) if address else ""
    if step == 1:
        if not asked:
            if car:
                return "%s%s в наличии, посмотреть можно в любой день до 20:00%s" % (
                    who,
                    car,
                    where,
                )
            return "Машина в наличии, посмотреть можно в любой день до 20:00%s" % where
        ask = pick_phone_ask(used)
        if car:
            return "%s%s на площадке. %s" % (who, car, ask)
        return ask
    if step == 2:
        tail = (". %s" % ADDRESS_SHORT[0].upper() + ADDRESS_SHORT[1:]) if address else ""
        if car:
            return (
                "%s ещё в наличии. Приезжайте посмотреть вживую, по цене "
                "готовы обсудить на месте%s" % (car, tail)
            )
        return (
            "Машина в наличии. Приезжайте посмотреть, по цене обсудим на месте%s"
            % tail
        )
    if car:
        return "Добрый день. %s - без изменений. Напишите номер, если актуально" % car
    return "Добрый день. Напишите номер, если актуально"


def refresh(nudge: dict, messages: list[dict]) -> dict:
    """Пересчитать флаг ожидания после хода диалога. Счётчик касаний не сбрасываем."""
    out = dict(nudge or {})
    out.setdefault("count", 0)
    if history_wants_stop(messages) or history_has_phone(messages) or history_refuses_phone(messages):
        out["waiting"] = False
        return out
    if out.get("count", 0) >= MAX_NUDGES:
        out["waiting"] = False
        return out
    # Раньше догон включался только после просьбы номера. Клиент, который
    # спросил про пробег и замолчал, уходил молча — а это тот же тёплый лид.
    # Теперь ждём после любого ответа, а тон касания зависит от того, просили
    # номер или нет. Стоп-условия выше не изменились.
    out["waiting"] = True
    out["asked"] = history_asked_phone(messages)
    out["asked_at"] = now_msk().isoformat(timespec="seconds")
    out["name"] = extract_name(messages) or out.get("name") or ""
    out["car"] = extract_car(messages) or out.get("car") or ""
    return out


def ready_to_send(nudge: dict) -> int:
    """Какой шаг слать сейчас: 1/2/3 или 0 если ещё рано / не надо."""
    if not settings.nudge_enabled:
        return 0
    if not nudge.get("waiting"):
        return 0
    count = int(nudge.get("count") or 0)
    if count >= MAX_NUDGES:
        return 0
    if not in_working_hours():
        return 0
    last = parse_iso(nudge.get("nudged_at") or "") or parse_iso(nudge.get("asked_at") or "")
    if not last:
        return 0
    step = count + 1
    return step if now_msk() >= due_at(last, step) else 0
