"""Человеческий слой: дробление реплики, чистка форматирования, задержки набора."""
from __future__ import annotations

import random
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from bot.config import settings

MSK = ZoneInfo("Europe/Moscow")

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
    r"никакого markdown|пустой строкой:"
    r")",
    re.IGNORECASE,
)
# Служебные маркеры - штатный протокол, а не утечка правил. Раньше [[ЧЕЛОВЕК]]
# считался утечкой, и правильная эскалация подменялась заглушкой про прайс.
MARKS = re.compile(r"\[\[[^\]]{1,20}\]\]")
URL = re.compile(r"https?://\S+")
# «По низу рынка» и родня: руководитель считает такие переговоры мусорными.
# В KB запрет есть, но модель срывается, поэтому меняем формулировку на выходе.
MARKET_TALK = re.compile(r"(ниже|по\s+низу)\s+рынка", re.IGNORECASE)
# «Передам ваш контакт менеджеру» ломает роль: клиент пишет продавцу и слышит,
# что продавец отдаст вопрос кому-то другому. Первый вопрос в ответ - «а ты кто?».
# В KB запрет есть (правило 37), но модель срывается на шаблон, поэтому чиним
# на выходе. Порядок важен: сначала связки, потом одиночное слово.
MANAGER_CASES = {
    "менеджер": "коллега",
    "менеджера": "коллеги",
    "менеджеру": "коллеге",
    "менеджером": "коллегой",
    "менеджере": "коллеге",
    "менеджеры": "коллеги",
}
MANAGER_CALL = r"(наберет|наберёт|перезвонит|позвонит|свяжется)"
MANAGER_OBJ = r"(\s+с\s+вами|\s+вас|\s+вам)?"
MANAGER_PHRASES = (
    (
        re.compile(
            r"переда(м|ю|дим)\s+(ваш\w*\s+)?(контакт\w*|номер\w*|данные|вопрос)\s+"
            r"(наш\w+\s+)?(менеджер\w+|специалист\w+|в\s+отдел\w*\s*\w*)",
            re.IGNORECASE,
        ),
        "наберу вас",
    ),
    (
        re.compile(
            r"(наш\s+)?менеджер\w*\s+(с\s+вами\s+)?" + MANAGER_CALL + MANAGER_OBJ,
            re.IGNORECASE,
        ),
        "наберу вас",
    ),
    (re.compile(r"(наш\s+)?менеджер\w*\s+уточнит", re.IGNORECASE), "уточню"),
    (re.compile(r"(наш\s+)?менеджер\w*\s+подтвердит", re.IGNORECASE), "подтвержу"),
    (re.compile(r"(наш\s+)?менеджер\w*\s+ответит", re.IGNORECASE), "отвечу"),
    (re.compile(r"уточнит\s+(наш\s+)?менеджер\w*", re.IGNORECASE), "уточню"),
    (re.compile(r"подтвердит\s+(наш\s+)?менеджер\w*", re.IGNORECASE), "подтвержу"),
    (
        re.compile(
            r"(перезвонит|свяжется|наберет|наберёт)\s+(наш\s+)?менеджер\w*",
            re.IGNORECASE,
        ),
        "наберу вас",
    ),
    (re.compile(r"уточн(ю|им)\s+у\s+менеджера", re.IGNORECASE), r"уточн\1"),
)
MANAGER_WORD = re.compile(r"\bменеджер(ы|а|у|ом|е)?\b", re.IGNORECASE)
# То же самое без слова «менеджер»: «передам в отдел продаж», «вам перезвонят».
FACELESS = (
    (
        re.compile(
            r"переда(м|ю|дим)\s+(ваш\w*\s+)?(контакт\w*|номер\w*|данные|заявк\w+)"
            r"(\s+в\s+отдел\w*(\s+\w+)?|\s+коллег\w+|\s+специалист\w+)",
            re.IGNORECASE,
        ),
        "наберу вас",
    ),
    (re.compile(r"\bвам\s+(перезвонят|наберут|позвонят)\b", re.IGNORECASE), "наберу вас"),
    (
        re.compile(
            r"(наш|наши)?\s*(кредитн\w+\s+)?специалист\w*\s+"
            + MANAGER_CALL
            + MANAGER_OBJ,
            re.IGNORECASE,
        ),
        "наберу вас",
    ),
    (
        re.compile(r"\bс\s+вами\s+свяж(утся|ется)\b", re.IGNORECASE),
        "свяжусь с вами",
    ),
)
# Хвосты, которые остаются от шаблона после замены связок: глагол в третьем
# лице без подлежащего («наберу вас, после чего свяжется с вами»).
DOUBLE_CALLBACK = re.compile(
    r",?\s*(и\s+|а\s+|после\s+чего\s+)?свяж(ется|усь)\s+с\s+вами[^.!?]*",
    re.IGNORECASE,
)
MANAGER_TAILS = (
    (re.compile(r"\bсвяжется\s+с\s+вами", re.IGNORECASE), "свяжусь с вами"),
    (re.compile(r"\bперезвонит\s+вам", re.IGNORECASE), "перезвоню"),
    (re.compile(r"\bответит\s+точно", re.IGNORECASE), "отвечу точно"),
    (re.compile(r"\bуточнит\b", re.IGNORECASE), "уточню"),
    (re.compile(r"\bподтвердит\b", re.IGNORECASE), "подтвержу"),
    (re.compile(r"\bрасскажут\b", re.IGNORECASE), "расскажу"),
    (re.compile(r"\bответят\b", re.IGNORECASE), "отвечу"),
    (re.compile(r"\bуточнят\b", re.IGNORECASE), "уточню"),
    (re.compile(r"\bпосчитают\b", re.IGNORECASE), "посчитаем"),
    (re.compile(r"наберу вас[,\s]+наберу вас", re.IGNORECASE), "наберу вас"),
)
# Отрицания истории, которые всегда ложь: в базе нет записи не значит «чисто».
# Скрипт «в такси автомобиль не использовался» для каршеринговых машин сюда
# не попадает - его руководитель как раз просил.
HISTORY_DENIAL = re.compile(
    r"("
    r"по\s+(наш\w+\s+)?баз\w+\s+(они\s+|она\s+|машин\w+\s+)?(полностью\s+)?чист|"
    r"чист\w+\s+по\s+(наш\w+\s+)?баз|"
    r"без\s+истории\s+(каршеринга|такси)|"
    r"без\s+работы\s+в\s+(такси|каршеринге)|"
    r"без\s+(каршеринга|такси)\b|"
    r"так\w+\s+истории\s+(у\s+\w+\s+)?нет|"
    r"истори\w+\s+(такси|каршеринга)\s+нет|"
    r"от\s+частн\w+\s+владельц|частн\w+\s+владелец\b|"
    r"не\s+(было|найдено)\s+ни\s+(такси|каршеринга)|"
    r"(лицензи\w+\s+(на\s+)?такси\s+нет|нет\s+лицензи\w+\s+(на\s+)?такси)|"
    r"(в\s+)?(такси|каршеринге)\s+не\s+числится|"
    r"не\s+числится\s+(в\s+)?(такси|каршеринге)"
    r")",
    re.IGNORECASE,
)
# «По окрасам данных нет - значит их нет». Пустое поле означает «не знаю», и
# вслух этот вывод звучит как отговорка продавца, который сам себе разрешил
# ответить за базу. Оставляем только сам факт из карточки.
NO_DATA_LOGIC = re.compile(
    r"("
    r"(данн\w+|сведени\w+|информаци\w+)\s+нет\s*[-,]?\s*значит|"
    r"(не\s+указан\w*|пуст\w+|нет\s+записи)\s*[-,]?\s*значит|"
    r"раз\s+(данн\w+|сведени\w+)\s+нет\s*[-,]?\s*(то\s+)?знач|"
    r"значит\s+(их|его|ее|её)\s+нет"
    r")",
    re.IGNORECASE,
)
# Телефонные формулы в переписке звучат нелепо: в чате никто не «слушает».
# В KB они есть законно - это скрипт входящего звонка, а не чата.
PHONE_SCRIPT = re.compile(
    r"\s*[-,]?\s*(слушаю\s+вас|я\s+вас\s+слушаю|говорите|на\s+связи,\s+слушаю)\b(\s*[.!])?",
    re.IGNORECASE,
)
# ASR в расшифровках звонков слышит «Дива / Диво / Дивы Моторс», и модель тащит
# это в чат. Название пишется латиницей: DIVO MOTORS.
BRAND_MISHEARD = re.compile(
    r"\b(див[аоы]|дио|дима|diva|divo)\s*[- ]?\s*(моторс|моторз|motors)\b",
    re.IGNORECASE,
)
BRAND = "DIVO MOTORS"
# Квалификация клиента: «для себя или в коммерческих целях», «какой бюджет».
# Такого скрипта у нас нет ни в промпте, ни в KB - модель достает его из своей
# выучки про автосалоны. Лишний ход, а ответ «под работу» уводит диалог
# в лизинг и юрлицо. Запрет в правиле 38, здесь режем на выходе.
QUAL_QUESTION = re.compile(
    r"("
    r"в\s+коммерческ\w+\s+цел|"
    r"для\s+коммерческ\w+\s+(использован|цел)|"
    r"для\s+себя\s+(или|же\s+или)|"
    r"для\s+себя\s+(рассматрива|подбира|присматрива|бере|смотри|ище)\w*|"
    r"(для|под)\s+семьи\s+или|"
    r"(для|под)\s+работ\w+\s+или|"
    r"перв\w+\s+(ваш\w*\s+)?(автомобиль|машина)\s+или|"
    r"как\w+\s+(у\s+вас\s+)?бюджет|"
    # Способ оплаты клиент выбирает при покупке, а не в переписке. Вопрос
    # «наличными или в кредит» это та же квалификация, лишний ход.
    r"как\s+вам\s+удобнее\s*[-,]?\s*наличн|"
    r"как\s+(вы\s+)?планируете\s+(оплачивать|оплату|рассчитыва)|"
    r"(юрлицо|юр\.?\s*лицо)\s+или\s+наличн|"
    r"наличн\w+\s+или\s+(в\s+)?кредит|"
    r"(в\s+)?кредит\s+или\s+(сразу\s+)?(за\s+)?наличн|"
    r"бюджет\w*\s+(вы\s+)?(рассматрива|ориентир)\w*|"
    r"на\s+как\w+\s+бюджет"
    r")",
    re.IGNORECASE,
)
# Разрешение на работу в такси: сам факт клиенту нужен, реквизиты - нет.
# Модель пересказывает блок автотеки целиком («неактивное разрешение по
# Ростовской области от мая 2024»), и короткий ответ превращается в приговор
# машине. Запрет в правиле 33, здесь режем реквизиты на выходе.
PERMIT_SENTENCE = re.compile(
    r"(разрешени\w*\s+на\s+работу\s+в\s+такси|"
    r"такси.{0,120}разрешени|"
    r"разрешени.{0,120}такси|"
    r"разрешени\w*.{0,60}(действующ|неактивн|выдано|петербург|москв|област)|"
    r"лицензи\w*.{0,60}(действующ|неактивн|выдано|петербург|москв|област))",
    re.IGNORECASE | re.DOTALL,
)
PERMIT_MONTH = (
    r"январ|феврал|март|апрел|ма[йяе]|июн|июл|август|сентябр|октябр|ноябр|декабр"
)
PERMIT_DETAIL = (
    re.compile(
        r"\s*(по|в)\s+\w+(ой|ей)\s+(области|обл\.?|республике)",
        re.IGNORECASE,
    ),
    re.compile(r"\s*(по|в)\s+\w+ском\s+краю", re.IGNORECASE),
    re.compile(r"\s*(по|в|-)?\s*санкт-?петербург\w*", re.IGNORECASE),
    re.compile(r"\s*(по|в|-)?\s*москв\w*", re.IGNORECASE),
    re.compile(r"\s*(по|в|-)?\s*ростовск\w*", re.IGNORECASE),
    re.compile(
        r"\s*оформлен\w*\s+(в\s+)?(" + PERMIT_MONTH + r")\w*\s*\d{4}",
        re.IGNORECASE,
    ),
    re.compile(r"\s*,?\s*оформленн?\w*", re.IGNORECASE),
    re.compile(r"\s*,?\s*статус\b.*", re.IGNORECASE),
    re.compile(
        r"\s*\([^)]*(петербург|москв|област|кра[йя]|"
        + PERMIT_MONTH
        + r"|действующ|неактивн|активн|\d{4})[^)]*\)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\s*(от|с|выдано\s+в|выданное\s+в)\s+"
        r"(" + PERMIT_MONTH + r")"
        r"\w*\s*\d{4}\s*(года|год|г\.?)?",
        re.IGNORECASE,
    ),
    re.compile(
        r"\s*выдано\s+в\s+[\w-]+(?:е|и)?\s+в\s+("
        + PERMIT_MONTH
        + r")\w*\s*\d{4}",
        re.IGNORECASE,
    ),
    re.compile(r"\s*(от|с)\s+\d{2}[.\-/]\d{2}[.\-/]\d{4}", re.IGNORECASE),
    re.compile(r"\s*(неактивн|активн|аннулированн|действующ|прекращённ|прекращенн)\w*\s+(?=разрешени)", re.IGNORECASE),
    re.compile(r"\s*,?\s*(оно\s+|которое\s+)?(сейчас\s+)?(не\s+)?действу\w+", re.IGNORECASE),
    re.compile(
        r"\s+(сейчас\s+)?(не\s+)?(активн|неактивн|аннулированн|действующ|прекращённ|прекращенн)\w*",
        re.IGNORECASE,
    ),
    re.compile(r"\s*,?\s*(оно\s+)?зарегистрирован\w*", re.IGNORECASE),
    re.compile(r"\s*у\s+этого\s+экземпляра", re.IGNORECASE),
)
# Отказ по карте в лоб. Факт верный, но руководитель просил формулировать
# через цену: «стоимость указана за наличный расчет». Правило 29 модель
# нарушает на прямом вопросе «а картой можно?», поэтому чиним на выходе.
CARD_REFUSAL = (
    re.compile(
        r"карт(ой|у|ами)?\s+(мы\s+)?(вообще\s+)?не\s+"
        r"(принимаем|работаем|берем|берём|получится|оплатить)\w*",
        re.IGNORECASE,
    ),
    re.compile(
        r"(оплата\s+)?карт(ой|ы)\s+(тоже\s+)?"
        r"(невозможна|не\s+предусмотрена|не\s+получится|нельзя)",
        re.IGNORECASE,
    ),
    re.compile(r"с\s+карты\s+на\s+карту\s*-?\s*нет", re.IGNORECASE),
    re.compile(r"эквайринга?\s+(у\s+нас\s+)?нет", re.IGNORECASE),
    re.compile(r"картой\s+не\s+получится", re.IGNORECASE),
)
CARD_SOFT = "цена указана за наличный расчет"
# Хвост отказа: «поэтому и комиссии нет» держится за вырезанную фразу.
CARD_TAIL = re.compile(
    r"\s*,?\s*поэтому\s+и?\s*(комиссии|процента)\s+нет", re.IGNORECASE
)
# Предложение без точки: «Пожалуйста Все три машины в наличии». Разрезаем
# только там, где следующее слово точно начинает новую фразу, - имена
# и марки в этот список не попадают.
SENTENCE_START = (
    "Все|Машина|Машины|Можно|Напишите|Наберу|Приезжайте|Скиньте|Подскажите|"
    "Как|Что|Если|Цена|Стоимость|Есть|Обсудим|Автомобиль|Уточню|Хотите|"
    "Готовы|Работаем|Адрес|Кредит|Оплата|Торг|Смогу|Могу|Давайте|Приятно|"
    "Записал|Спасибо|Хорошо|Понимаю|Отчет|Отчёт|Пришлем|Пришлём"
)
MISSING_DOT = re.compile(r"(?<=[а-яё]{2})\s+(?=(?:%s)\b)" % SENTENCE_START)
QUAL_LEAD_IN = re.compile(
    r"(подскажите|скажите|уточните|а|и|кстати|ещё|еще)([\s,]+(подскажите|скажите))?",
    re.IGNORECASE,
)
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


def _tidy(text: str, original: str) -> str:
    """Замены строчные и оставляют двойные пробелы - вернуть реплике вид."""
    text = re.sub(r"\s{2,}", " ", text).replace(" ,", ",").strip()
    if original[:1].isupper() and text[:1].islower():
        text = text[0].upper() + text[1:]
    return text


def _sub_keep_case(pattern: re.Pattern, repl: str, text: str) -> str:
    """Замена написана строчными: в начале предложения ставим заглавную."""
    if "\\" in repl:
        return pattern.sub(repl, text)

    def fn(m: re.Match) -> str:
        head = m.string[: m.start()].rstrip()
        if not head or head[-1] in ".!?":
            return repl[0].upper() + repl[1:]
        return repl

    return pattern.sub(fn, text)


def drop_manager(text: str) -> str:
    """Убрать «менеджера» как третье лицо: продавец в чате - сам Никита."""
    original = text or ""
    text = original
    for pattern, repl in FACELESS:
        text = _sub_keep_case(pattern, repl, text)
    touched = text != original
    if MANAGER_WORD.search(text):
        touched = True
        for pattern, repl in MANAGER_PHRASES:
            text = _sub_keep_case(pattern, repl, text)
        text = MANAGER_WORD.sub(
            lambda m: MANAGER_CASES.get(m.group(0).lower(), "коллега"), text
        )
    if touched:
        for pattern, repl in MANAGER_TAILS:
            text = _sub_keep_case(pattern, repl, text)
        # «Наберу вас, после чего свяжусь с вами» - обещание звонка дважды.
        if "наберу вас" in text.lower():
            text = DOUBLE_CALLBACK.sub("", text)
    return _tidy(text, original)


def _drop_qual_clause(part: str) -> str:
    """Квал-вопрос приклеен к факту через запятую: оставить факт, вопрос убрать."""
    chunks = [c for c in re.split(r",\s*", part) if not QUAL_QUESTION.search(c)]
    text = ", ".join(c.strip() for c in chunks if c.strip())
    text = re.sub(r"[\s,]*[?!.]*$", "", text)
    # «Подскажите» без самого вопроса - пустая вводная, не реплика.
    if not text or QUAL_LEAD_IN.fullmatch(text):
        return ""
    return text


def drop_qual(text: str) -> str:
    """Выкинуть придуманную квалификацию: цель покупки, бюджет, «для себя ли»."""
    original = text or ""
    if not QUAL_QUESTION.search(original):
        return original
    kept = []
    for part in re.split(r"(?<=[.!?\n])\s+", original):
        if not QUAL_QUESTION.search(part):
            kept.append(part)
            continue
        clause = _drop_qual_clause(part)
        if clause:
            kept.append(clause)
    text = " ".join(kept).strip()
    # Вся реплика была таким вопросом - пузырь пустой, его выкинет main.
    return _tidy(text, original) if text else ""


def soften_card(text: str) -> str:
    """Отказ по карте заменить на формулировку через цену за наличный расчет.

    Меняем предложение целиком, а не кусок: «картой не работаем, поэтому и
    комиссии нет» после точечной замены превращается в кашу. Если мягкая
    формулировка в реплике уже есть, предложение просто выкидываем.
    """
    original = text or ""
    if not any(p.search(original) for p in CARD_REFUSAL):
        return original
    parts = re.split(r"(?<=[.!?])\s+", original)
    kept: list[str] = []
    for part in parts:
        if not any(p.search(part) for p in CARD_REFUSAL):
            kept.append(part)
            continue
        tail = "." if part.rstrip().endswith(".") else ""
        soft = CARD_SOFT[0].upper() + CARD_SOFT[1:] + tail
        already = any("наличный расчет" in k.lower() for k in kept)
        if not already:
            kept.append(soft)
    text = " ".join(k for k in kept if k.strip())
    if not text.strip():
        text = CARD_SOFT[0].upper() + CARD_SOFT[1:]
    return _tidy(text, original)


def add_missing_dots(text: str) -> str:
    """Модель роняет точку между фразами. Возвращаем её по началу предложения."""
    original = text or ""
    text = MISSING_DOT.sub(". ", original)
    return text if text != original else original


def trim_permit(text: str) -> str:
    """Оставить факт разрешения на такси, убрать регион, дату и статус."""
    original = text or ""
    parts = re.split(r"(?<=[.!?\n])\s+", original)
    out = []
    for part in parts:
        if PERMIT_SENTENCE.search(part):
            for pattern in PERMIT_DETAIL:
                part = pattern.sub(" ", part)
            part = re.sub(r"\s*,?\s*(оно|которое)\s*$", "", part, flags=re.IGNORECASE)
            part = re.sub(
                r"(разрешени\w*(?:\s+на\s+(?:работу\s+в\s+)?такси)?(?:\s+есть)?)"
                r"\s*[,;].*$",
                r"\1",
                part,
                flags=re.IGNORECASE,
            )
            part = re.sub(
                r"(разрешени\w*(?:\s+на\s+(?:работу\s+в\s+)?такси)?)"
                r"\s+(было|выдано|оформлен\w*)\b.*$",
                r"\1",
                part,
                flags=re.IGNORECASE,
            )
            # После вырезанных реквизитов остаётся «разрешение - .» или
            # висящий дефис перед точкой: подчищаем хвост предложения.
            part = re.sub(r"\s*[-–—,;:]\s*(?=[.!?]|$)", "", part)
            part = re.sub(r"\s+([.!?])", r"\1", part)
            part = re.sub(r"\s*[-–—,;:]\s*$", "", part)
            # От «разрешение - зарегистрировано в Петербурге, действующее»
            # остаётся огрызок «разрешение». Возвращаем факт целиком.
            if re.fullmatch(
                r"(да[,\s]+)?(есть\s*[-–—,]?\s*)?разрешени\w*(\s*[-–—,]?\s*есть)?[.!?]?",
                part.strip(),
                re.IGNORECASE,
            ):
                part = "Да, разрешение на работу в такси есть."
        out.append(part)
    text = " ".join(out)
    return _tidy(text, original) if text != original else original


def drop_no_data_logic(text: str) -> str:
    """Выкидывает вывод «данных нет - значит нет», оставляя сам факт.

    «По окрасам данных нет - значит их нет, машина без окрасов» превращается в
    «Машина без окрасов»: вывод убираем, факт из карточки оставляем.
    """
    original = text or ""
    if not NO_DATA_LOGIC.search(original):
        return original
    out: list[str] = []
    for part in re.split(r"(?<=[.!?])\s+", original):
        if not NO_DATA_LOGIC.search(part):
            out.append(part)
            continue
        # Внутри предложения вывод обычно стоит перед фактом: берём то, что
        # после него, и это ровно ответ клиенту.
        pieces = [p.strip(" ,-") for p in re.split(r",", part) if p.strip(" ,-")]
        tail = [p for p in pieces if not NO_DATA_LOGIC.search(p)]
        if tail:
            keep = ", ".join(tail)
            out.append(keep[0].upper() + keep[1:])
    text = " ".join(p for p in out if p.strip())
    return _tidy(text, original) if text.strip() else original


def fix_brand(text: str) -> str:
    return BRAND_MISHEARD.sub(BRAND, text or "")


def drop_phone_script(text: str) -> str:
    """«Слушаю вас» и «говорите» - формулы телефонного звонка, не переписки."""
    original = text or ""
    if not PHONE_SCRIPT.search(original):
        return original
    # Точку, на которой кончалось предложение, оставляем: без неё следующая
    # фраза слипается с приветствием.
    text = PHONE_SCRIPT.sub(lambda m: (m.group(2) or "").strip(), original)
    if not re.search(r"\w", text):
        return ""  # вся реплика была телефонной формулой, отправлять нечего
    return _tidy(text, original)


PUSH_INVITE = re.compile(
    r"("
    r"приезжайте|приходите|подъезжайте|"
    r"посмотреть можно|можно посмотреть|можно приехать|"
    r"приехать посмотреть|посмотреть вживую|посмотреть у нас|"
    r"на какой день|во сколько (подъедете|приедете|заедете)|"
    r"когда удобнее|ждем вас|ждём вас"
    r")",
    re.IGNORECASE,
)
PUSH_CALL = re.compile(
    r"("
    r"набер(?:у|ём|ем|ёт|ет|ут)|"
    r"позвон(?:ю|им)|"
    r"перезвон(?:ю|им)|"
    r"свяж(?:усь|емся|ёмся)|"
    r"в ближайшее время"
    r")",
    re.IGNORECASE,
)


def drop_push_after_contact(text: str, *, allow_invite: bool = False) -> str:
    """После передачи контакта не дожимаем: факт по вопросу, без визита и звонка."""
    original = text or ""
    if not original.strip():
        return original
    kept: list[str] = []
    for part in re.split(r"(?<=[.!?\n])\s+", original):
        low = part.lower()
        if "автотек" in low or "autoteka.ru" in low:
            kept.append(part)
            continue
        if not allow_invite and PUSH_INVITE.search(part):
            continue
        if PUSH_CALL.search(part):
            continue
        kept.append(part)
    text = " ".join(kept).strip()
    return _tidy(text, original) if text else ""


CLAUSE_DASH = re.compile(r"\s+[–—-]\s+")


def drop_clause_dashes(text: str) -> str:
    """Связку фраз через « - » режем на точку. Дефис внутри слова не трогаем."""
    original = text or ""
    parts = [p.strip() for p in CLAUSE_DASH.split(original) if p.strip()]
    if len(parts) <= 1:
        return original
    out = parts[0]
    for part in parts[1:]:
        if part[:1].islower():
            part = part[0].upper() + part[1:]
        if out[-1] in ".!?":
            out = out + " " + part
        else:
            out = out + ". " + part
    return out


def drop_owner_legal(text: str) -> str:
    """Владельцы в чате — только число. Юрлицо и «износ выше» не говорим.

    Это история владения из автотеки, не продажа на организацию. Цену на
    юрлицо / НДС не трогаем.
    """
    original = text or ""
    text = re.sub(
        r",?\s*по\s+уч[её]ту\s+(на\s+)?(юрлиц\w*|юридическ\w*(?:\s+лиц\w*)?)",
        "",
        original,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"[;,]?\s*автомобилем\s+владело\s+(юридическое|физическое)\s+лицо\.?",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"[;,]?\s*износ\s+у\s+таких\s+машин[^.\n]*\.?",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"(?<=владелец)\s+(юрлиц\w*|юридическ\w*(?:\s+лиц\w*)?)",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"(?<=владельца)\s+(юрлиц\w*|юридическ\w*(?:\s+лиц\w*)?)",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"(?<=владельцев)\s+(юрлиц\w*|юридическ\w*(?:\s+лиц\w*)?)",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\s+,", ",", text)
    text = re.sub(r",\s*,+", ",", text)
    text = re.sub(r"\s+\.", ".", text)
    return _tidy(text, original)


BODY_PART = re.compile(
    r"\b("
    r"крыл\w*|двер\w*|капот\w*|бампер\w*|порог\w*|"
    r"крышк\w*|крыш[аеиу]\w*|зеркал\w*|лонжерон\w*|стойк\w*|арк\w*"
    r")\b",
    re.I,
)
PAINT_TALK = re.compile(r"окрас|крашен|покрашен|покраск|лкп", re.I)
PAINT_TALLY = re.compile(r"(\d{1,2})\s+(элемент\w*|детале\w*|окрас\w*)", re.I)
SMOOTH_PAINTS = "Есть косметические окрасы, по кузову всё видно на осмотре"


def _many_paints(text: str) -> bool:
    blob = text or ""
    if not PAINT_TALK.search(blob):
        return False
    if len(BODY_PART.findall(blob)) >= 3:
        return True
    found = PAINT_TALLY.search(blob)
    return bool(found and int(found.group(1)) >= 3)


def soften_many_paints(text: str) -> str:
    """Три и больше элементов в окрасе в чат списком не отдаём."""
    original = text or ""
    if not _many_paints(original):
        return original
    out: list[str] = []
    for part in re.split(r"(?<=[.!?])\s+", original):
        if _many_paints(part):
            out.append(SMOOTH_PAINTS)
        else:
            out.append(part)
    return _tidy(" ".join(out), original)


def drop_kstati(text: str) -> str:
    """«Кстати» в чате звучит как бот. Вырезаем, смысл оставляем."""
    original = text or ""
    text = re.sub(r",\s*кстати\s*,", ",", original, flags=re.IGNORECASE)
    text = re.sub(r",\s*кстати\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bкстати\s*,\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bкстати\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+([?!.])", r"\1", text)
    return _tidy(text, original)


GREET_LEAD = re.compile(
    r"^\s*(?:"
    r"(?:добр(?:ый|ое|ой)\s+(?:день|утро|вечер|ночи)|здравствуйте|здрасте|"
    r"привет(?:ствую)?)"
    r"[!,.]?\s*"
    r")+",
    re.IGNORECASE,
)
INTRO_LEAD = re.compile(
    r"^\s*(?:(?:меня\s+зовут|я)\s+)?"
    r"(?:никита|(?:divo|диво)\s*motors)[!,.]?\s*",
    re.IGNORECASE,
)


def drop_regreeting(text: str) -> str:
    """Убрать повторное «добрый день» и представление, если диалог уже шёл."""
    original = (text or "").strip()
    if not original:
        return ""
    text = GREET_LEAD.sub("", original, count=1).strip()
    for _ in range(4):
        nxt = INTRO_LEAD.sub("", text, count=1).strip(" ,.-")
        if nxt == text:
            break
        text = nxt
    if not text:
        return ""
    if original[:1].isupper() and text[:1].islower():
        text = text[0].upper() + text[1:]
    return text


LEASING_BIT = re.compile(r"лизинг", re.IGNORECASE)
TORG_BIT = re.compile(
    r"("
    r"комплиментарн|комплементарн|"
    r"торг\w*.{0,28}(обсуд|готов|можн)|"
    r"(обсуд|готов)\w*.{0,28}торг|"
    r"по цене\s+(готов|обсуд)|"
    r"по цене.{0,24}(осмотр|на месте)|"
    r"цен[уеы]\s+(готов[аы]?\s+)?обсуд|"
    r"разумных пределах|"
    r"торг и условия"
    r")",
    re.IGNORECASE,
)
HEATER_BIT = re.compile(
    r"отопител|вебасто|webasto|нагреватель",
    re.IGNORECASE,
)
# На «скиньте фото» модель тащит карточку: пробег, ДТП, автотека. Это не спрашивали.
CONDITION_DUMP = re.compile(
    r"("
    r"по состоянию|"
    r"пробег\s+\d|"
    r"состояни[ея]\s+хорош|"
    r"машина не новая|"
    r"не била|"
    r"не битая|"
    r"автотек\w*.{0,48}нет|"
    r"данных по дтп|"
    r"окрасам из отч|"
    r"по кузову всё видно|"
    r"видно при осмотре"
    r")",
    re.IGNORECASE,
)
SOFT_TORG = (
    "В разумных пределах торг и условия можем обсудить после осмотра"
)
HARD_TORG_NO = re.compile(r"не сможем", re.IGNORECASE)
FIXED_PRICE = re.compile(r"зафиксирован", re.IGNORECASE)
PRICE_IN_SENT = re.compile(r"(\d[\d\s]{2,}\d(?:\s*руб(?:лей)?)?)", re.IGNORECASE)


def soften_hard_torg(text: str, *, allow_torg: bool = False) -> str:
    """Борщат по цене: не рубим «не сможем», оставляем торг после осмотра."""
    original = text or ""
    if not allow_torg or not original.strip():
        return original
    if not HARD_TORG_NO.search(original) and not FIXED_PRICE.search(original):
        return original
    kept: list[str] = []
    for part in re.split(r"(?<=[.!?\n])\s+", original):
        if HARD_TORG_NO.search(part):
            continue
        if FIXED_PRICE.search(part):
            found = PRICE_IN_SENT.search(part)
            if found:
                kept.append("Цена в объявлении %s" % " ".join(found.group(1).split()))
            continue
        kept.append(part)
    body = " ".join(p.strip() for p in kept if p.strip()).strip()
    if not body:
        return SOFT_TORG
    if not re.search(r"осмотр|торг|услови", body, re.I):
        body = "%s. %s" % (body.rstrip("."), SOFT_TORG)
    return _tidy(body, original)


def drop_unsolicited(
    text: str,
    *,
    allow_leasing: bool = False,
    allow_torg: bool = False,
    allow_heater: bool = False,
    allow_condition: bool = True,
) -> str:
    """Лизинг, торг, отопитель и карточку состояния выкидываем без вопроса."""
    original = text or ""
    if not original.strip():
        return original
    kept: list[str] = []
    for part in re.split(r"(?<=[.!?\n])\s+", original):
        if not allow_leasing and LEASING_BIT.search(part):
            continue
        if not allow_torg and TORG_BIT.search(part):
            continue
        if not allow_heater and HEATER_BIT.search(part):
            continue
        if not allow_condition and CONDITION_DUMP.search(part):
            continue
        kept.append(part)
    text = " ".join(kept).strip()
    return _tidy(text, original) if text else ""


WHERE_CHOICE = re.compile(
    r"(где|куда)\s+(вам\s+)?удобнее|"
    r"вам\s+удобнее\s+(приехать|подъехать|заехать|к\s+нам)",
    re.IGNORECASE,
)
WHERE_LEAD = re.compile(
    r"^\s*(где|куда)\s+(вам\s+)?удобнее\s*[,.]?\s*",
    re.IGNORECASE,
)
# «Сейчас наберу» звучит как срок, который менеджер не обязан выдержать.
CALL_VERB = (
    r"(?:набер(?:у|ём|ем|ёт|ет|ут)|позвон(?:ю|им)|перезвон(?:ю|им)|"
    r"свяж(?:усь|емся|ёмся)|созвон(?:юсь|имся))"
)
NOW_WORD = r"(?:прямо\s+|вот\s+)?сейчас(?:\s+же)?"
NOW_BEFORE_CALL = re.compile(
    r"\b"
    + NOW_WORD
    + r"(?=\s+(?:я\s+|мы\s+)?(?:вам\s+|тебе\s+|вас\s+|тебя\s+)?"
    + CALL_VERB
    + r"\b)",
    re.IGNORECASE,
)
CALL_THEN_NOW = re.compile(
    r"(\b" + CALL_VERB + r"(?:\s+(?:вас|вам|тебя|тебе))?)\s+" + NOW_WORD + r"\b",
    re.IGNORECASE,
)


def drop_where_choice(text: str) -> str:
    """Салон один: «где вам удобнее» не вопрос, а мусор."""
    original = text or ""
    if not WHERE_CHOICE.search(original):
        return original
    kept: list[str] = []
    for part in re.split(r"(?<=[.!?\n])\s+", original):
        if not WHERE_CHOICE.search(part):
            kept.append(part)
            continue
        rest = WHERE_LEAD.sub("", part).strip(" ,")
        if rest and not WHERE_CHOICE.search(rest):
            if rest[:1].islower():
                rest = rest[0].upper() + rest[1:]
            kept.append(rest)
    text = " ".join(kept).strip()
    return _tidy(text, original) if text else ""


def soften_now_call(text: str) -> str:
    """«Сейчас наберу» → «в ближайшее время наберу». Наличие «сейчас нет» не трогаем."""
    original = text or ""
    if not NOW_BEFORE_CALL.search(original) and not CALL_THEN_NOW.search(original):
        return original
    text = _sub_keep_case(NOW_BEFORE_CALL, "в ближайшее время", original)
    text = CALL_THEN_NOW.sub(r"\1 в ближайшее время", text)
    return _tidy(text, original)


LOGISTICS_LECTURE = re.compile(
    r"тема логистик|"
    r"логистик\w*.{0,48}пошлин|"
    r"пошлин\w*.{0,24}не моя|"
    r"наугад не скажу.{0,80}(логистик|пошлин|утил)",
    re.IGNORECASE,
)
DEAD_CLOSER = re.compile(
    r"\bобращайтесь\b|"
    r"если что.?(-| )?(то )?понадоб|"
    r"если интересно по машинам",
    re.IGNORECASE,
)


def _drop_parts(text: str, pattern: re.Pattern) -> str:
    original = text or ""
    if not pattern.search(original):
        return original
    kept = [part for part in re.split(r"(?<=[.!?\n])\s+", original) if not pattern.search(part)]
    text = " ".join(kept).strip()
    return _tidy(text, original) if text else ""


def drop_logistics_lecture(text: str) -> str:
    """Пошлины и логистику не читаем, если клиент про них не спрашивал."""
    return _drop_parts(text, LOGISTICS_LECTURE)


def drop_dead_closer(text: str) -> str:
    """«Обращайтесь» и «если что, пишите» — ответ ради ответа."""
    return _drop_parts(text, DEAD_CLOSER)


STOCK_LEAD = re.compile(
    r"(?:"
    r"да,\s*в наличии[.!]?\s*|"
    r"именно эта машина в наличии(?: и готова к продаже)?,?\s*|"
    r"данн(?:ый|ого)\s+автомобил[ьяе]\s+в наличии,?\s*|"
    r"машина (?:у нас )?в наличии,?\s*|"
    r"ещё в наличии[.!]?\s*"
    r")",
    re.IGNORECASE,
)
STOCK_WORD = re.compile(r"\s*в наличии(?: и готова к продаже)?", re.IGNORECASE)
INVITE_FALLBACK = "Посмотреть можно в любой день с 10:00 до 20:00"
INVITE_FIRST = (
    "Посмотреть можно в любой день с 10:00 до 20:00, "
    "мы на Автозаводской 18, ТЦ Ривьера, -2 этаж"
)
UNTIL_CLOSE = re.compile(r"до\s*20[:.]00", re.IGNORECASE)


def fix_hours(text: str) -> str:
    """Пишем и со скольких, и до скольких: с 10:00 до 20:00."""
    original = text or ""

    def repl(match: re.Match) -> str:
        prefix = original[max(0, match.start() - 14) : match.start()].lower()
        if re.search(r"с\s*10(?::00)?\s*$", prefix):
            return match.group(0)
        return "с 10:00 до 20:00"

    return UNTIL_CLOSE.sub(repl, original)


def has_in_stock(text: str) -> bool:
    return "в наличии" in (text or "").lower()


def strip_in_stock(text: str) -> str:
    """Оставляет приглашение, убирает повтор «в наличии»."""
    original = text or ""
    if not has_in_stock(original):
        return original
    text = STOCK_LEAD.sub("", original)
    text = STOCK_WORD.sub("", text)
    text = re.sub(r"\s+,", ",", text)
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"^[,\s.]+", "", text)
    text = _tidy(text, original)
    if text.strip():
        return text
    if re.search(r"посмотр|приехать|приезжа", original, re.I):
        return INVITE_FALLBACK
    return ""


def dedupe_in_stock(bubbles: list[str], *, already: bool = False) -> list[str]:
    """Наличие — один раз за диалог. Второй пузырь зовёт смотреть без этой фразы."""
    seen = already
    out: list[str] = []
    for bubble in bubbles:
        if not has_in_stock(bubble):
            if bubble.strip():
                out.append(bubble)
            continue
        if seen:
            cut = strip_in_stock(bubble)
            if cut.strip():
                out.append(cut)
            continue
        # В одном пузыре тоже не дважды.
        first, *rest = re.split(r"(?<=[.!?])\s+", bubble)
        kept = [first]
        saw = has_in_stock(first)
        for part in rest:
            if saw and has_in_stock(part):
                cut = strip_in_stock(part)
                if cut.strip():
                    kept.append(cut)
            else:
                kept.append(part)
                saw = saw or has_in_stock(part)
        text = " ".join(kept).strip()
        if text:
            out.append(text)
        seen = True
    return out


ASKS_VIN = re.compile(r"\bvin\b", re.I)
TRADEIN_MENU = re.compile(
    r"(?:,|\.)?\s*(?:а\s+)?если\s+vin\s+нет[^.!?\n]*"
    r"(?:[.!?]\s*(?:ссылк|марку)[^.!?\n]*)?",
    re.IGNORECASE,
)
TRADEIN_FALLBACK = re.compile(
    r"(?:,|\.)?\s*(?:ссылк\w*\s+на\s+объявлен[^.!?\n]*?)?(?:или\s+)?"
    r"марку,?\s*модель\s+и\s+год",
    re.IGNORECASE,
)
TRADEIN_FALLBACK_ONLY = re.compile(
    r"ссылк\w*\s+на\s+объявлен|марку,\s*модель\s+и\s+год|марку\s+модель\s+и\s+год",
    re.IGNORECASE,
)
LISTING_MARK = "Клиент прислал объявление"
REMOTE_EVAL = re.compile(r"дистанцион", re.I)
VIN_PHONE_TAIL = "Напишите, пожалуйста, контактный телефон для обратной связи"


def asks_vin(text: str) -> bool:
    return bool(ASKS_VIN.search(text or ""))


def wants_remote_eval(text: str) -> bool:
    return bool(REMOTE_EVAL.search(text or ""))


def client_listing(messages: list[dict] | None) -> str:
    """Строка карточки своей машины, если клиент уже прислал объявление."""
    for msg in messages or []:
        if msg.get("role") != "user":
            continue
        text = msg.get("content") or ""
        if LISTING_MARK not in text:
            continue
        for line in text.splitlines():
            if LISTING_MARK in line:
                return line.strip()
        return LISTING_MARK
    return ""


def drop_tradein_menu(text: str) -> str:
    """VIN-реплика без меню «или ссылка, или марка, модель и год»."""
    original = text or ""
    if not asks_vin(original):
        return original
    text = TRADEIN_MENU.sub("", original)
    text = TRADEIN_FALLBACK.sub("", text)
    kept: list[str] = []
    for part in re.split(r"(?<=[.!?\n])\s+", text):
        if not part.strip():
            continue
        if TRADEIN_FALLBACK_ONLY.search(part) and not asks_vin(part):
            continue
        kept.append(part)
    text = " ".join(kept).strip()
    return _tidy(text, original) if text else original


def drop_reask_listing(text: str) -> str:
    """Объявление уже в чате — не просим ссылку и марку повторно."""
    original = text or ""
    if not TRADEIN_FALLBACK_ONLY.search(original):
        return original
    kept: list[str] = []
    for part in re.split(r"(?<=[.!?\n])\s+", original):
        if TRADEIN_FALLBACK_ONLY.search(part) and not asks_vin(part):
            continue
        kept.append(part)
    text = TRADEIN_MENU.sub("", " ".join(kept)).strip()
    if not text:
        return ""
    return _tidy(text, original)


def with_vin_phone(text: str) -> str:
    """К запросу VIN дописать телефон, если его в реплике ещё нет."""
    original = (text or "").strip()
    if not original or not asks_vin(original):
        return original
    if re.search(r"телефон|номер(?:а)?(?:\s+телефона)?", original, re.I):
        return original
    body = original.rstrip(" .!?")
    if re.search(r"для загрузки истории", body, re.I):
        return body + " и контактный телефон для обратной связи"
    return body + ". " + VIN_PHONE_TAIL


PAPER_LEAK = re.compile(
    r"("
    r"строк[аиеуы]\s+(с\s+ценой|в\s+(базе|карточ)|нет)|"
    r"нет\s+строк|"
    r"в\s+базе\s+(пока\s+)?нет|"
    r"в\s+базе\s+пока|"
    r"юрлицо\s*/\s*ндс|"
    r"цены?\s+на\s+юрлицо\s*/|"
    r"такой\s+машины\s+строк|"
    r"в\s+карточке\s+(нет|пока|строк)|"
    r"сходу\s+не\s+подним|"
    r"свер[яи]ю\s+по\s+сток|"
    r"точной\s+карточки|"
    r"карточки\s+с\s+vin|"
    r"vin\s+у\s+меня\s+нет|"
    r"по\s+объявлению\s+с\s+(авито|авто)|"
    r"данных\s+отч[её]та|"
    r"отч[её]та\s+у\s+меня|"
    r"под\s+рукой\s+нет|"
    r"цифрами\s+наугад|"
    r"не\s+дезинформировать\s+вас|"
    r"чтобы\s+вас\s+не\s+дезинформировать"
    r")",
    re.IGNORECASE,
)
VAT_NO_PRICE = "Цену с НДС по этой машине сразу не назову, уточню"
GREET_BANG = {
    "добрый день": "Добрый день!",
    "доброе утро": "Доброе утро!",
    "добрый вечер": "Добрый вечер!",
    "доброй ночи": "Доброй ночи!",
    "здравствуйте": "Здравствуйте!",
}
GREET_HEAD = re.compile(
    r"^(добрый\s+день|доброе\s+утро|добрый\s+вечер|доброй\s+ночи|здравствуйте)"
    r"(?:\s*[.,:]|\s*!)?\s*",
    re.IGNORECASE,
)


def greeting_now(moment: datetime | None = None) -> str:
    """Приветствие по Москве: утро с 4, день с 12, вечер с 18, ночь с 23."""
    hour = (moment or datetime.now(MSK)).astimezone(MSK).hour
    if 4 <= hour < 12:
        return "Доброе утро!"
    if 12 <= hour < 18:
        return "Добрый день!"
    if 18 <= hour < 23:
        return "Добрый вечер!"
    return "Доброй ночи!"


def drop_paper_talk(text: str) -> str:
    """Клиенту не про «строку в базе»: продавец машину знает, а не сверку бумаг."""
    original = text or ""
    if not PAPER_LEAK.search(original):
        return original
    kept: list[str] = []
    for part in re.split(r"(?<=[.!?\n])\s+", original):
        if PAPER_LEAK.search(part):
            continue
        kept.append(part)
    text = " ".join(kept).strip()
    if text:
        return _tidy(text, original)
    stub = VAT_NO_PRICE if re.search(r"ндс|юрлиц", original, re.IGNORECASE) else "Уточню по этой машине"
    greet = GREET_HEAD.match(original)
    if greet:
        return _tidy(greeting_now() + " " + stub, original)
    return _tidy(stub, original)


PHONE_FOR_CALL = re.compile(
    r"("
    r"наберу|"
    r"позвон|"
    r"для обратной связи|"
    r"напишите.{0,40}номер|"
    r"скиньте.{0,30}номер|"
    r"контактный телефон|"
    r"номер телефона|"
    r"если звонок неудобен"
    r")",
    re.IGNORECASE,
)
MESSENGER_ASK = "Напишите, пожалуйста, Телеграм или Ватсап, туда пришлю"
MAX_APP = re.compile(
    r"(?:на|в)\s+(?:макс(?:е|а|у)?|max)\b"
    r"|(?<![А-Яа-яA-Za-z])макс(?:е|а|у)?(?![а-яёa-z])"
    r"|(?<![A-Za-z])max(?![A-Za-z])",
    re.IGNORECASE,
)


def drop_max_app(text: str) -> str:
    """Макс как мессенджер не предлагаем. Имя Максим не трогаем."""
    original = text or ""
    if not MAX_APP.search(original):
        return original
    out = re.sub(
        r"(?:на|в)\s+(?:макс(?:е|а|у)?|max)\b",
        "в Телеграм или Ватсап",
        original,
        flags=re.IGNORECASE,
    )
    out = re.sub(r",\s*(?:макс(?:е|а|у)?|max)\b", "", out, flags=re.IGNORECASE)
    out = re.sub(r"\s+или\s+(?:макс(?:е|а|у)?|max)\b", "", out, flags=re.IGNORECASE)
    out = re.sub(
        r"(?<![А-Яа-яA-Za-z])(?:макс(?:е|а|у)?|max)(?![а-яёA-Za-z])",
        "Телеграм или Ватсап",
        out,
        flags=re.IGNORECASE,
    )
    return _tidy(out, original)


def speak_messengers(text: str) -> str:
    """Клиенту русские имена: Телеграм, Ватсап."""
    original = text or ""
    out = original.replace("Telegram или WhatsApp", "Телеграм или Ватсап")
    out = out.replace("WhatsApp или Telegram", "Ватсап или Телеграм")
    out = re.sub(r"\bWhats[Aa]pp\b", "Ватсап", out)
    out = re.sub(r"\bTelegram\b", "Телеграм", out)
    return out


def phone_to_messenger(text: str) -> str:
    """Звонок неудобен — не просим номер под звонок, просим мессенджер."""
    original = text or ""
    if not PHONE_FOR_CALL.search(original):
        return original
    kept: list[str] = []
    for part in re.split(r"(?<=[.!?\n])\s+", original):
        if PHONE_FOR_CALL.search(part):
            continue
        kept.append(part)
    body = " ".join(kept).strip()
    if body:
        glue = "" if body[-1:] in ".!?" else "."
        return _tidy(body + glue + " " + MESSENGER_ASK, original)
    return MESSENGER_ASK


def bang_greeting(text: str, moment: datetime | None = None) -> str:
    """Первое приветствие с восклицанием и по текущему времени Москвы."""
    original = text or ""
    match = GREET_HEAD.match(original)
    if not match:
        return original
    rest = original[match.end():].lstrip()
    canon = greeting_now(moment)
    if not rest:
        return canon
    if rest[:1].islower():
        rest = rest[0].upper() + rest[1:]
    return canon + " " + rest


def greeting_only(text: str) -> bool:
    """Пузырь целиком приветствие, без ответа по делу."""
    raw = (text or "").strip()
    if not raw:
        return False
    return not drop_regreeting(raw)


def glue_lonely_greeting(bubbles: list[str]) -> list[str]:
    """«Доброе утро!» отдельным сообщением не отправляем: склеиваем с ответом.

    Иначе клиент видит одно приветствие, а второе сообщение может не уйти:
    рестарт сервиса, сбой канала, пауза набора.
    """
    if len(bubbles) < 2:
        return bubbles
    if not greeting_only(bubbles[0]):
        return bubbles
    greet = (bubbles[0] or "").strip()
    rest = (bubbles[1] or "").lstrip()
    if rest[:1].islower():
        rest = rest[0].upper() + rest[1:]
    return [("%s %s" % (greet, rest)).strip()] + list(bubbles[2:])


def ensure_greeting(
    bubbles: list[str], *, first: bool, moment: datetime | None = None
) -> list[str]:
    """Первый ход диалога всегда с приветствием по времени Москвы."""
    if not first or not bubbles:
        return bubbles
    out = list(bubbles)
    if GREET_LEAD.match(out[0] or ""):
        out[0] = bang_greeting(out[0], moment)
        return out
    rest = (out[0] or "").strip()
    if rest[:1].islower():
        rest = rest[0].upper() + rest[1:]
    out[0] = (greeting_now(moment) + " " + rest).strip()
    return out


def for_chat(text: str) -> str:
    """Как пишет человек в телефоне: без тире-связок, без точки в конце."""
    text = drop_clause_dashes(text or "")
    text = drop_kstati(text)
    text = drop_owner_legal(text)
    text = soften_many_paints(text)
    text = MARKET_TALK.sub("ниже аналогов", text)
    text = drop_manager(text)
    text = drop_qual(text)
    text = trim_permit(text)
    text = soften_card(text)
    text = drop_no_data_logic(text)
    text = drop_phone_script(text)
    text = drop_where_choice(text)
    text = soften_now_call(text)
    text = drop_paper_talk(text)
    text = drop_logistics_lecture(text)
    text = drop_dead_closer(text)
    text = drop_tradein_menu(text)
    text = fix_hours(text)
    text = fix_brand(text)
    text = add_missing_dots(text)
    return bang_greeting(drop_end_period(text))


def clean(text: str) -> str:
    """Убрать markdown, который модель тащит из KB. В чате его быть не должно."""
    out = []
    for line in text.splitlines():
        line = MARKDOWN_NOISE.sub("", line)
        line = BULLET.sub("", line)
        line = NUMBERED.sub("", line)
        out.append(line.rstrip())
    return "\n".join(out).strip()


def glue_lines(block: str) -> str:
    """Перенос строки внутри пузыря - граница предложения, а не пробел.

    Модель пишет «машина в наличии\\nНа какой день заехать»: без точки склейка
    даёт «в наличии На какой день» - так человек не печатает.
    """
    lines = [line.strip() for line in block.splitlines() if line.strip()]
    out = ""
    for line in lines:
        if not out:
            out = line
            continue
        # Перечень вариантов машины остаётся столбиком: «белый, 16 217 км -
        # 1 750 000» в строку с соседним вариантом читать невозможно.
        if is_car_facts(line) and (out[-1].isdigit() or out[-1] == ":"):
            out += "\n" + line
        elif out[-1] in ".!?,:;-" or line[0].islower():
            out += " " + line
        else:
            out += ". " + line
    return out


def split_bubbles(text: str) -> list[str]:
    """Пустая строка = граница сообщения. Больше трёх пузырей не отправляем."""
    blocks = [b.strip() for b in re.split(r"\n\s*\n", clean(text)) if b.strip()]
    if not blocks:
        return []
    if len(blocks) > MAX_BUBBLES:
        head = blocks[: MAX_BUBBLES - 1]
        head.append("\n".join(blocks[MAX_BUBBLES - 1:]))
        blocks = head
    return [x for x in (for_chat(glue_lines(b)) for b in blocks) if x]


def denies_history(text: str) -> bool:
    """Пузырь отрицает такси или каршеринг «по базе». Такое клиенту не уходит."""
    return bool(HISTORY_DENIAL.search(text or ""))


def looks_like_leak(text: str) -> bool:
    """Чеклист правил или английский разбор инструкции — клиенту такое не слать."""
    if not text:
        return False
    text = MARKS.sub(" ", text)
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
    # Латиница и цифры набираются медленнее русского текста: раскладка, цифровой
    # ряд, а VIN, ссылку и цену перед отправкой ещё и перечитывают. Ссылку не
    # набирают вручную, поэтому её из счёта убираем.
    body = URL.sub(" ", text)
    hard = len(re.findall(r"[A-Za-z0-9]", body))
    if len(body) > 70 and hard >= 10:
        cps = max(cps * 0.75, 6.0)
        extra += random.uniform(0.6, 1.6)
    base = len(text) / cps + extra
    jitter = random.uniform(0.9, 1.2)
    return max(settings.delay_min_sec, min(settings.delay_max_sec, base * jitter))
