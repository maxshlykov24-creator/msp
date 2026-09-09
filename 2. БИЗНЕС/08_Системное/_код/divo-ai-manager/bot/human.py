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
    re.compile(r"\s*у\s+этого\s+экземпляра", re.IGNORECASE),
)
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
            part = re.sub(r"\s*[-–—,;:]\s*$", "", part)
        out.append(part)
    text = " ".join(out)
    return _tidy(text, original) if text != original else original


def for_chat(text: str) -> str:
    """Как пишет человек в телефоне: дефис вместо длинного тире, без точки в конце."""
    text = (text or "").replace("—", "-").replace("–", "-")
    text = MARKET_TALK.sub("ниже аналогов", text)
    text = drop_manager(text)
    text = drop_qual(text)
    text = trim_permit(text)
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
    base = len(text) / cps + extra
    jitter = random.uniform(0.9, 1.2)
    return max(settings.delay_min_sec, min(settings.delay_max_sec, base * jitter))
