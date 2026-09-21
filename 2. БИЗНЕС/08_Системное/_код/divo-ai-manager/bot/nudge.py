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
VIN = re.compile(r"\b[A-HJ-NPR-Za-hj-npr-z0-9]{17}\b")
HAS_PHONE = re.compile(
    r"(?:\+?7|8)[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}|\b\d{10,11}\b"
)
# +994, +375 и остальные: российский шаблон их не берёт, и догон снова
# просит номер, который клиент уже прислал.
INTL_PHONE = re.compile(
    r"(?<!\d)(?:\+|00)\s*\d(?:[\s\-\(\)]*\d){9,14}(?!\d)"
)
# 910 182-22-97 без семёрки и восьмёрки: российский мобильный.
RU_PHONE_10 = re.compile(
    r"(?<!\d)\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}(?!\d)"
)
# «Подумаю» гасит только догон: живой ответ ещё можно. Закрытие интереса —
# и догон, и живую реплику.
SOFT_STOP = re.compile(r"подумаю", re.IGNORECASE)
CLOSED = re.compile(
    r"("
    r"не\s*актуал|"
    r"не интересно|"
    r"уже купил|"
    r"уже приобр[её]л|"
    r"\bотстань\b|"
    r"сам разберусь|"
    r"не надо(?![а-яё])|"
    r"не нужно(?![а-яё])|"
    r"\bудачи\b|"
    r"\bпередумал\b|"
    r"\bоткажусь\b|"
    r"не буду (брать|покупать|смотреть)"
    r")",
    re.IGNORECASE,
)
UNHEARD_MEDIA = re.compile(
    r"^клиент прислал (голосовое|видео)\.?$",
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
WRITE_HERE = re.compile(
    r"("
    r"здесь напиш|"
    r"(?<![А-Яа-яA-Za-z])пишите здесь(?!\s+цен)|"
    r"напиш\w{0,8}\s+здесь(?!\s+цен)|"
    r"сюда напиш|"
    r"напиш\w{0,8}\s+сюда|"
    r"в этом чате|"
    r"звонк\w* не (проход|доход)|не беру трубк|"
    r"на звонки не|не звоните|лучше напиш|"
    r"мне нельзя звон|звонки у меня не"
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
WEEKDAYS_RU = (
    "понедельник",
    "вторник",
    "среда",
    "четверг",
    "пятница",
    "суббота",
    "воскресенье",
)
# Будний день, не сб/вс: соглашаться «да, в пн» = клиент думает, что в выходные мы закрыты.
WEEKDAY_NAME = re.compile(
    r"(?<![а-яё])("
    r"пн|вт|ср|чт|пт|"
    r"понедельник\w*|вторник\w*|сред[аеуы]|четверг\w*|пятниц\w*"
    r")(?![а-яё])",
    re.IGNORECASE,
)
THEIR_SLOT = re.compile(
    r"("
    r"мне (удобн|можно|только|ок|свобод)|"
    r"у меня|"
    r"я (только |смогу|свобод)|"
    r"смогу только|"
    r"(приед|заед|подъед)\w*"
    r")",
    re.IGNORECASE,
)
ASKS_OUR_SLOT = re.compile(
    r"("
    r"когда (набер|позвон|перезвон|свяж|откро|работа)|"
    r"вы когда|"
    r"набер[её]те|"
    r"позвон(ите|и)|"
    r"перезвон|"
    r"работаете|"
    r"вы в\s+(пн|вт|ср|чт|пт|понедельник|вторник|сред|четверг|пятниц)"
    r")",
    re.IGNORECASE,
)
ABOUT_OUR_CALL = re.compile(
    r"(набер|позвон|перезвон|свяж)",
    re.IGNORECASE,
)
WEEKDAY_TAIL = re.compile(
    r"\?\s*(пн|вт|ср|чт|пт|понедельник|вторник|сред[ае]|четверг|пятниц)\w*\s*\??\s*$",
    re.IGNORECASE,
)
ASKED_VISIT = re.compile(
    r"("
    r"когда можно|"
    r"когда приехать|"
    r"можно приехать|"
    r"приехать посмотреть|"
    r"посмотреть автомобиль|"
    r"посмотреть машин|"
    r"посмотреть авто|"
    r"на осмотр|"
    r"во сколько (можно|приехать)"
    r")",
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


def weekday_ru(moment: datetime | None = None) -> str:
    return WEEKDAYS_RU[(moment or now_msk()).weekday()]


def asked_our_hours_day(text: str) -> bool:
    """Клиент гадает про НАШ график («наберёте? Пн?»), а не называет свой слот."""
    blob = text or ""
    if not WEEKDAY_NAME.search(blob):
        return False
    if THEIR_SLOT.search(blob):
        return False
    if ASKS_OUR_SLOT.search(blob):
        return True
    return bool(WEEKDAY_TAIL.search(blob))


def about_our_call(text: str) -> bool:
    return bool(ABOUT_OUR_CALL.search(text or ""))


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
    """До 10:00 и после 20:00 сдвигаем на 10:00 ближайшего рабочего утра."""
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
    return bool(extract_phone(text))


def asked_phone(text: str) -> bool:
    return bool(ASKED_PHONE.search(text or ""))


def is_closed(text: str) -> bool:
    """Клиент снял интерес: не актуально, уже купил, удачи."""
    return bool(CLOSED.search(text or ""))


def is_unheard_media(text: str) -> bool:
    """Голос или видео без расшифровки: это ответ, не тишина."""
    return bool(UNHEARD_MEDIA.match((text or "").strip()))


def wants_stop(text: str) -> bool:
    return is_closed(text) or bool(SOFT_STOP.search(text or ""))


# Смайлик, стикер, «ок» без вопроса: живой продавец кивает и молчит.
_EMOJI = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002700-\U000027BF"
    "\U00002600-\U000026FF"
    "\U0000FE00-\U0000FE0F"
    "\U0000200D"
    "\U0000200B"
    "\U0001F1E6-\U0001F1FF"
    "]+"
)
_ACK_ONLY = {
    "+",
    "ок",
    "оке",
    "окей",
    "ok",
    "okay",
    "хорошо",
    "ладно",
    "понял",
    "поняла",
    "понятно",
    "ясно",
    "принято",
    "принял",
    "приняла",
    "спасибо",
    "спс",
    "благодарю",
    "thanks",
    "thx",
    "угу",
    "ага",
    "договорились",
    "супер",
    "отлично",
    "круто",
    "норм",
    "нормально",
    "ок спасибо",
    "спасибо ок",
    "хорошо спасибо",
    "спасибо большое",
    "все понятно",
    "все ясно",
    "ок хорошо",
    "хорошо ок",
    "принято спасибо",
}


def needs_reply(text: str) -> bool:
    """False, если входящее не требует ответа: смайлик, «ок», «спасибо»."""
    raw = (text or "").strip()
    if not raw:
        return False
    if extract_phone(raw):
        return True
    if wants_call(raw) or asks_about_call(raw) or wants_person(raw) or is_complaint(raw):
        return True
    if wants_write_here(raw):
        return True
    if DELETED_INCOMING.match(raw):
        return False
    if is_unheard_media(raw):
        return False
    if is_closed(raw) and "?" not in raw:
        return False
    if "?" in raw:
        return True
    if raw.lower().startswith("клиент прислал"):
        return True
    body = _EMOJI.sub(" ", raw)
    body = re.sub(r"[^\w\s+-]", " ", body, flags=re.U)
    body = " ".join(body.lower().replace("ё", "е").split())
    if not body or body in _ACK_ONLY:
        return False
    return True


OWN_PHONE = re.compile(
    r"("
    r"мой номер|"
    r"это и есть мой|"
    r"вот мой|"
    r"перезвоните на|"
    r"звоните на|"
    r"наберите на|"
    r"другой номер|"
    r"новый номер"
    r")",
    re.IGNORECASE,
)


def _is_ru_mobile(digits: str) -> bool:
    return len(digits) == 11 and digits.startswith("79")


def extract_phone(text: str) -> str:
    """Номер клиента: Россия как 7XXXXXXXXXX, иностранный как цифры с кодом страны.

    Салонный номер и короткие хвосты не берём. +994 раньше не ловился, и бот
    на следующий день снова просил телефон. VIN рядом с номером не склеиваем:
    хвост SCA…77820 плюс 8910… давал ложный +7 820.
    """
    salon = re.sub(r"\D", "", SALON_PHONE)
    best = ""
    blob = VIN.sub(" ", text or "")

    def take(digits: str) -> None:
        nonlocal best
        if not digits or digits == salon or digits[-10:] == salon[-10:]:
            return
        if digits.startswith("00"):
            digits = digits[2:]
        if len(digits) == 11 and digits[0] == "8":
            digits = "7" + digits[1:]
        if len(digits) == 10:
            digits = "7" + digits
        if not (10 <= len(digits) <= 15):
            return
        if _is_ru_mobile(digits) and not _is_ru_mobile(best):
            best = digits
            return
        if _is_ru_mobile(best) and not _is_ru_mobile(digits):
            return
        # Более длинный международный важнее куска из 10 цифр внутри него.
        if len(digits) > len(best):
            best = digits
        elif len(digits) == len(best) and digits.startswith("7") and not best.startswith("7"):
            best = digits

    for match in INTL_PHONE.finditer(blob):
        take(re.sub(r"\D", "", match.group(0)))
    # Без плюса: 994993845959. Российский шаблон берёт только 10–11 цифр
    # и отрезает хвост, поэтому длинный хвост смотрим отдельно.
    for match in re.finditer(r"(?<!\d)\d{12,15}(?!\d)", blob):
        take(match.group(0))
    for match in HAS_PHONE.finditer(blob):
        take(re.sub(r"\D", "", match.group(0)))
    for match in RU_PHONE_10.finditer(blob):
        take(re.sub(r"\D", "", match.group(0)))
    return best


def _just_phone_text(text: str) -> bool:
    raw = (text or "").strip()
    if not extract_phone(raw):
        return False
    return not re.search(r"[а-яёa-z]{3,}", raw, flags=re.IGNORECASE)


def _call_question_before(messages: list[dict], idx: int) -> bool:
    lo = max(0, idx - 4)
    for msg in messages[lo:idx]:
        if msg.get("role") != "user":
            continue
        if asks_about_call(msg.get("content") or ""):
            return True
    return False


def is_caller_id_paste(text: str, prior: list[dict] | None = None) -> bool:
    """Клиент скинул АОН входящего, а не свой контакт."""
    if not extract_phone(text or ""):
        return False
    if OWN_PHONE.search(text or ""):
        return False
    if asks_about_call(text or ""):
        return True
    if not _just_phone_text(text):
        return False
    prior_list = list(prior or [])
    fake = prior_list + [{"role": "user", "content": text}]
    return _call_question_before(fake, len(prior_list))


def extract_phone_from_history(messages: list[dict]) -> str:
    """Свой номер клиента. АОН входящего после «это вы звонили» не берём."""
    msgs = list(messages or [])
    chosen = ""
    for i, msg in enumerate(msgs):
        if msg.get("role") != "user":
            continue
        text = msg.get("content") or ""
        phone = extract_phone(text)
        if not phone:
            continue
        if OWN_PHONE.search(text):
            chosen = phone
            continue
        if asks_about_call(text):
            continue
        if _just_phone_text(text) and _call_question_before(msgs, i):
            continue
        if not chosen:
            chosen = phone
    return chosen


def history_has_phone(messages: list[dict]) -> bool:
    return bool(extract_phone_from_history(messages))


def extract_vins(messages: list[dict] | None) -> list[str]:
    """VIN-ы из реплик клиента: его машины, которые он даёт на оценку."""
    out: list[str] = []
    for msg in messages or []:
        if msg.get("role") != "user":
            continue
        for found in VIN.finditer(msg.get("content") or ""):
            vin = found.group(0).upper()
            if not (re.search(r"[A-Z]", vin) and re.search(r"\d", vin)):
                continue
            if vin not in out:
                out.append(vin)
    return out


def is_existing_buyer(text: str) -> bool:
    """Уже наш клиент: покупал, залог не снят, сервис после сделки."""
    raw = (text or "").lower().replace("ё", "е")
    if re.search(r"покупал|купил[аи]?\s+(у вас|у нас|автомобил|машин)", raw):
        return True
    if re.search(r"брал[аи]?\s+у\s+(вас|нас)", raw):
        return True
    if re.search(r"залог.{0,48}(не снят|не сняли|висит|числится|сбер|втб)", raw):
        return True
    if re.search(r"(снят|снимите|выплачен|гасил)\w*.{0,24}залог", raw):
        return True
    return False


def urgent_reason(user_text: str, history: list[dict] | None = None) -> str:
    """В группу менеджеров: номер, звонок, жалоба или уже наш покупатель."""
    text = user_text or ""
    hist = list(history or [])
    if is_existing_buyer(text):
        return "aftersale"
    phone_now = extract_phone(text)
    if phone_now and is_caller_id_paste(text, hist):
        phone_now = ""
    has_phone = bool(phone_now or history_has_phone(hist))
    if not has_phone:
        return ""
    if is_complaint(text):
        return "complaint"
    if wants_person(text):
        return "handoff"
    if wants_call(text) or asks_about_call(text) or is_caller_id_paste(text, hist):
        return "call"
    if phone_now and not history_has_phone(hist):
        return "phone"
    return ""


def _user_blob(messages: list[dict] | None) -> str:
    parts = []
    for msg in messages or []:
        if msg.get("role") != "user":
            continue
        text = " ".join(str(msg.get("content") or "").split()).lower().replace("ё", "е")
        if text:
            parts.append(text)
    return " ".join(parts)


def asked_leasing(messages: list[dict] | None) -> bool:
    return "лизинг" in _user_blob(messages)


LEASE_REPORT = re.compile(
    r"автотек|отч[её]т|в базах|до\s*20\d\d|до\s+\d+\s*год|обремен|залог",
    re.IGNORECASE,
)


def cited_lease_report(text: str) -> bool:
    """Клиент ткнул в автотеку или срок лизинга, а не просто спросил «была?»."""
    return bool(LEASE_REPORT.search(text or ""))


def asked_credit(messages: list[dict] | None) -> bool:
    blob = _user_blob(messages)
    if re.search(r"\bкредитк", blob):
        return False
    return bool(re.search(r"\bкредит", blob))


def asked_vat(messages: list[dict] | None) -> bool:
    blob = _user_blob(messages)
    keys = (
        "ндс",
        "юрлиц",
        "на организац",
        "на компанию",
        "по счет",
        "по счёт",
        "расчетн",
        "расчётн",
    )
    return any(key in blob for key in keys)


def asked_torg(messages: list[dict] | None) -> bool:
    blob = _user_blob(messages)
    keys = (
        "торг",
        "скидк",
        "уступ",
        "дешевле",
        "ниже цен",
        "цену ниже",
        "последняя цена",
        "последнюю цен",
        "можно минус",
        "сделаете цен",
    )
    if any(key in blob for key in keys):
        return True
    if re.search(
        r"(продадите|продашь|отдадите|отдашь|возьм[её]те)\w*.{0,32}за\s+\d",
        blob,
    ):
        return True
    if re.search(
        r"за\s+\d+(?:[.,]\d+)?\s*(млн|миллион|тыс|тысяч|руб)",
        blob,
    ) and re.search(r"продад|отдад|готов за|забер", blob):
        return True
    # «ЗА 16 млн возьму», «возьму за 16» — это своя сумма, не вопрос про пробег.
    if re.search(r"за\s+\d", blob) and re.search(
        r"возьм|купл[юи]|\bберу\b|\bберем\b|\bберём\b",
        blob,
    ):
        return True
    if re.search(r"\d+(?:[.,]\d+)?\s*готов\s+приехать", blob):
        return True
    return False


NONCAR_ITEM = re.compile(
    r"(картин|живопис|холст|"
    r"квартир|недвижим|участк|дач[ауие]|яхт|"
    r"станок|чпу|оборудов|посреднич)",
    re.IGNORECASE,
)
TRADE_WORD = re.compile(r"обмен|trade.?in|трейд", re.IGNORECASE)
TRADE_PIVOT = re.compile(
    r"(за наличн|за деньг|без обмен|просто купл|тогда купл|"
    r"вин код|\bvin\b|моя машин|свой авто|свою машин)",
    re.IGNORECASE,
)
NONCAR_INVITE = re.compile(
    r"если интересн|напишите номер|обсудим подробн",
    re.IGNORECASE,
)
NONCAR_TRADE_REPLY = (
    "Мы работаем только с денежным расчётом или обменом на автомобиль. "
    "Обмен на другое имущество и посредничество не рассматриваем"
)


def asked_noncar_trade(text: str) -> bool:
    """Обмен на картину, станок, квартиру и прочее — не на машину."""
    blob = (text or "").lower().replace("ё", "е")
    if not NONCAR_ITEM.search(blob):
        return False
    if TRADE_WORD.search(blob):
        return True
    return bool(re.search(r"посреднич|найд\w* клиента", blob))


def keep_noncar_boundary(bubbles: list[str]) -> list[str]:
    """Одно сообщение с границей, без номера и без уговоров купить за нал."""
    cleaned: list[str] = []
    for bubble in bubbles or []:
        text = drop_phone_ask(bubble)
        parts = re.split(r"(?<=[.!?])\s+", (text or "").strip())
        kept = [p for p in parts if p.strip() and not NONCAR_INVITE.search(p)]
        line = " ".join(kept).strip().rstrip(" .,")
        if line:
            cleaned.append(line)
    return cleaned[:1] or [NONCAR_TRADE_REPLY]


def history_noncar_trade(messages: list[dict] | None) -> bool:
    return any(
        m.get("role") == "user" and asked_noncar_trade(m.get("content") or "")
        for m in messages or []
    )


def still_noncar_trade(messages: list[dict] | None) -> bool:
    """Тема всё ещё бартер не на авто, клиент не перешёл к покупке за деньги."""
    if not history_noncar_trade(messages):
        return False
    last = _last_user(messages)
    if not last:
        return True
    if extract_phone(last) or TRADE_PIVOT.search(last):
        return False
    return True


def asked_heater(messages: list[dict] | None) -> bool:
    blob = _user_blob(messages)
    return any(
        key in blob
        for key in ("отопител", "вебасто", "webasto", "нагреватель", "печк", "греет зимой")
    )


def _last_user(messages: list[dict] | None) -> str:
    for msg in reversed(messages or []):
        if msg.get("role") != "user":
            continue
        return str(msg.get("content") or "").lower().replace("ё", "е")
    return ""


def asked_media(messages: list[dict] | None) -> bool:
    blob = _last_user(messages)
    return bool(re.search(r"фото|видеообзор|(?<![а-я])видео(?![а-я])", blob))


def asked_condition(messages: list[dict] | None) -> bool:
    blob = _last_user(messages)
    keys = (
        "дтп", "окрас", "состояни", "пробег", "автотек",
        "битая", "битый", "било", "кузов", "толщиномер", "микрон",
    )
    return any(key in blob for key in keys)


WANTS_CALL = re.compile(
    r"("
    r"позвон(ите|и)|набер(ите|и)|перезвон|"
    r"жду звонка|свяжитесь (со мной )?по (телефону|номеру)|"
    r"давайте (в )?трубк|созвон"
    r")",
    re.IGNORECASE,
)
ASKS_ABOUT_CALL = re.compile(
    r"("
    r"это вы( мне)? звонил|"
    r"вы( мне)? звонил|"
    r"кто звонил|"
    r"(вам|мне) звонили|"
    r"это вы( мне)? набирал|"
    r"вы( мне)? набирал|"
    r"не дозвони|"
    r"пропущенн"
    r")",
    re.IGNORECASE,
)
CALLBACK_ACK = "Да, это мы. Наберу ещё раз в ближайшее время"
AFTERSALE_ACK = "Принял. Уточню у коллег и напишу сюда."
DELETED_INCOMING = re.compile(r"^сообщение удалено\.?$", re.IGNORECASE)
WANTS_PERSON = re.compile(
    r"("
    r"живого (человека|менеджера|продавца)|"
    r"оператор[аеу]?|подключи(те)? (человека|менеджера|коллегу)|"
    r"не бот[аом]? (нужен|хочу)|с человеком"
    r")",
    re.IGNORECASE,
)
COMPLAINT = re.compile(
    r"("
    r"жалоб|претензи|развод|кидал|обман|"
    r"в суд|роспотреб|прокуратур|верните деньги"
    r")",
    re.IGNORECASE,
)


def wants_call(text: str) -> bool:
    return bool(WANTS_CALL.search(text or ""))


def asks_about_call(text: str) -> bool:
    """Клиент спрашивает про уже состоявшийся звонок, не просит номер заново."""
    return bool(ASKS_ABOUT_CALL.search(text or ""))


def wants_person(text: str) -> bool:
    return bool(WANTS_PERSON.search(text or ""))


def is_complaint(text: str) -> bool:
    return bool(COMPLAINT.search(text or ""))


def clarify_count(messages: list[dict]) -> int:
    n = 0
    for msg in messages or []:
        if msg.get("role") != "assistant":
            continue
        if re.search(r"уточн(ю|им|у)", msg.get("content") or "", re.IGNORECASE):
            n += 1
    return n


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


def drop_phone_ask(text: str) -> str:
    """Убирает просьбу дать номер: телефон в диалоге уже есть.

    Клиент присылает VIN и номер двумя сообщениями подряд, модель отвечает на
    первое и просит телефон второй раз. Для клиента это «меня не читают».
    """
    parts = re.split(r"(?<=[.!?])\s+", (text or "").strip())
    kept = [p for p in parts if p.strip() and not asked_phone(p)]
    if len(kept) == len(parts):
        return text
    return " ".join(kept).strip().rstrip(" .,")


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


def wants_write_here(text: str) -> bool:
    """Просит писать сюда или не берёт трубку — это не отказ от контакта."""
    return bool(WRITE_HERE.search(text or ""))


NAMED_MESSENGER = re.compile(
    r"(ват[сц]ап|вацап|whats?app|телеграм|telegram|(?<![а-яёa-z])тг(?![а-яёa-z]))",
    re.IGNORECASE,
)
ASKS_MESSENGER_CHOICE = re.compile(
    r"("
    r"(напишите|укажите|какой).{0,40}(ват[сц]ап|телеграм|whats?app|telegram)|"
    r"(ват[сц]ап|вацап|whats?app).{0,16}или.{0,16}(телеграм|telegram|тг)|"
    r"(телеграм|telegram).{0,16}или.{0,16}(ват[сц]ап|вацап|whats?app)"
    r")",
    re.IGNORECASE,
)


def named_messenger(text: str) -> bool:
    return bool(NAMED_MESSENGER.search(text or ""))


def history_named_messenger(messages: list[dict]) -> bool:
    return any(
        m.get("role") == "user" and named_messenger(m.get("content") or "")
        for m in messages or []
    )


def drop_messenger_choice(text: str) -> str:
    """Уже сказали Ватсап или Телеграм — второй раз не спрашиваем какой."""
    parts = re.split(r"(?<=[.!?])\s+", (text or "").strip())
    kept = [p for p in parts if p.strip() and not ASKS_MESSENGER_CHOICE.search(p)]
    if len(kept) == len(parts):
        return text
    return " ".join(kept).strip().rstrip(" .,")


def history_wants_write_here(messages: list[dict]) -> bool:
    return any(
        m.get("role") == "user" and wants_write_here(m.get("content") or "")
        for m in messages
    )


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


def asked_visit(text: str) -> bool:
    """Клиент спрашивает, когда можно приехать смотреть машину."""
    return bool(ASKED_VISIT.search(text or ""))


def history_has_address(messages: list[dict]) -> bool:
    return any(
        m.get("role") == "assistant" and has_address(m.get("content") or "") for m in messages
    )


def history_said_in_stock(messages: list[dict] | None) -> bool:
    """Уже подтверждали наличие. Второй раз «в наличии» не говорим."""
    return any(
        m.get("role") == "assistant" and "в наличии" in (m.get("content") or "").lower()
        for m in messages or []
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
                name = found.group(1)
                if name.lower().replace("ё", "е") != "никита":
                    return name
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


def drop_name_ask(text: str) -> str:
    """Убирает вопрос про имя из реплики.

    Спросили один раз, клиент не назвал — второй раз это уже давление, и никакая
    новая формулировка («как вас зовут, кстати») дела не меняет.
    """
    parts = re.split(r"(?<=[.!?])\s+", (text or "").strip())
    kept = [p for p in parts if p.strip() and not asked_name(p)]
    if len(kept) == len(parts):
        return text
    return " ".join(kept).strip().rstrip(" .,")


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
    r"не сейчас)",
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
    said_stock: bool = False,
) -> str:
    """Три касания. Второе — про приезд, а не про номер: цель не контакт, а визит.

    asked=False означает, что номер в этом диалоге ещё не просили: клиент просто
    замолчал после ответа по машине. Тогда первое касание тоже мягкое.
    address=True добавляет к приглашению адрес: звать смотреть, не сказав куда, —
    значит получить в ответ «а где вы находитесь» вместо приезда.
    said_stock=True — наличие уже подтвердили, «в наличии» второй раз не пишем.
    """
    who = (name + ", ") if name else ""
    where = (", " + ADDRESS_SHORT) if address else ""
    if step == 1:
        if not asked:
            if said_stock:
                invite = "посмотреть можно в любой день с 10:00 до 20:00"
                if who:
                    return "%s%s%s" % (who, invite, where)
                return invite[0].upper() + invite[1:] + where
            if car:
                return "%s%s в наличии, посмотреть можно в любой день с 10:00 до 20:00%s" % (
                    who,
                    car,
                    where,
                )
            return "Машина в наличии, посмотреть можно в любой день с 10:00 до 20:00%s" % where
        ask = pick_phone_ask(used)
        if car:
            return "%s%s на площадке. %s" % (who, car, ask)
        return ask
    if step == 2:
        tail = (". %s" % ADDRESS_SHORT[0].upper() + ADDRESS_SHORT[1:]) if address else ""
        if said_stock:
            return "Приезжайте посмотреть вживую%s" % tail
        if car:
            return "%s ещё в наличии. Приезжайте посмотреть вживую%s" % (car, tail)
        return "Машина в наличии. Приезжайте посмотреть вживую%s" % tail
    if car:
        from bot.human import greeting_now

        return "%s %s без изменений. Напишите номер, если актуально" % (
            greeting_now(),
            car,
        )
    from bot.human import greeting_now

    return "%s Напишите номер, если актуально" % greeting_now()


def should_stop_nudge(messages: list[dict]) -> bool:
    """Догон только если клиент замолчал после нашего ответа.

    Голос без текста, «не актуально», номер, отказ от телефона, последнее
    слово за клиентом — это не тишина, пинг «если актуально» туда не идёт.
    """
    if history_wants_stop(messages) or history_has_phone(messages) or history_refuses_phone(messages):
        return True
    if still_noncar_trade(messages):
        return True
    if messages and (messages[-1].get("role") == "user"):
        return True
    return False


def refresh(nudge: dict, messages: list[dict]) -> dict:
    """Пересчитать флаг ожидания после хода диалога. Счётчик касаний не сбрасываем."""
    out = dict(nudge or {})
    out.setdefault("count", 0)
    if should_stop_nudge(messages):
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
    prev = parse_iso(nudge.get("nudged_at") or "")
    # Шаг уже был, а метки нет: дедлайн шага 2 считается от asked_at и
    # наутро оба касания уходят подряд. Без метки второй шаг не шлём.
    if count > 0 and not prev:
        return 0
    if prev and now_msk() - prev < timedelta(minutes=10):
        return 0
    step = count + 1
    return step if now_msk() >= due_at(last, step) else 0
