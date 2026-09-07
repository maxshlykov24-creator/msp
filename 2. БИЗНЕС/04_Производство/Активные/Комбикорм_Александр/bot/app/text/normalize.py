"""Нормализация распознанной речи: регистр, число-слова, единицы, коды.

Задача — привести живую речь к предсказуемым токенам, чтобы матчинг был
устойчив к перестановкам слов, слитным/раздельным кодам и числам словами.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# --- число-слова → цифры -------------------------------------------------

_UNITS = {
    "ноль": 0, "один": 1, "одна": 1, "одну": 1, "два": 2, "две": 2, "три": 3,
    "четыре": 4, "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9,
    "десять": 10, "одиннадцать": 11, "двенадцать": 12, "тринадцать": 13,
    "четырнадцать": 14, "пятнадцать": 15, "шестнадцать": 16, "семнадцать": 17,
    "восемнадцать": 18, "девятнадцать": 19,
}
_TENS = {
    "двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50,
    "шестьдесят": 60, "семьдесят": 70, "восемьдесят": 80, "девяносто": 90,
}
_HUNDREDS = {
    "сто": 100, "двести": 200, "триста": 300, "четыреста": 400,
    "пятьсот": 500, "шестьсот": 600, "семьсот": 700, "восемьсот": 800,
    "девятьсот": 900,
}
_SCALES = {"тысяча": 1000, "тысячи": 1000, "тысяч": 1000, "тыщ": 1000, "тыща": 1000}

_ALL_NUM_WORDS = set(_UNITS) | set(_TENS) | set(_HUNDREDS) | set(_SCALES)

# единицы измерения
_BAG_WORDS = {"мешок", "мешка", "мешков", "мешочек", "шт", "штук", "штука", "штуки"}
_KG_WORDS = {"кг", "килограмм", "килограмма", "килограммов", "кило"}

# слова-разделители позиций
_SEP_WORDS = {"дальше", "далее", "следующая", "следующий", "потом", "затем", "ещё", "еще"}

# слова, помечающие итоговую сумму
_TOTAL_WORDS = {"итого", "итог", "всего", "сумма", "суммой"}

# коды-паттерны: пк, кк, к, рецепт и т.п. + число
_CODE_LETTERS = {"пк", "кк", "к", "р", "рецепт", "пкк"}


def words_to_number(tokens: list[str]) -> Optional[int]:
    """Собирает целое из подряд идущих числословов. None — если чисел нет."""
    if not tokens:
        return None
    total = 0
    current = 0
    seen = False
    for t in tokens:
        if t in _UNITS:
            current += _UNITS[t]
            seen = True
        elif t in _TENS:
            current += _TENS[t]
            seen = True
        elif t in _HUNDREDS:
            current += _HUNDREDS[t]
            seen = True
        elif t in _SCALES:
            current = (current or 1) * _SCALES[t]
            total += current
            current = 0
            seen = True
        else:
            return None
    if not seen:
        return None
    return total + current


def _replace_number_words(text: str) -> str:
    """Заменяет последовательности числослов на цифры прямо в тексте."""
    tokens = text.split()
    out: list[str] = []
    buf: list[str] = []

    def flush():
        if buf:
            num = words_to_number(buf)
            if num is not None:
                out.append(str(num))
            else:
                out.extend(buf)
            buf.clear()

    for tok in tokens:
        if tok in _ALL_NUM_WORDS:
            buf.append(tok)
        else:
            flush()
            out.append(tok)
    flush()
    return " ".join(out)


def _merge_codes(text: str) -> str:
    """«пк 1 2» → «пк-1-2», «пк 2» → «пк-2», «пика два» → уже цифры после замены."""
    text = re.sub(r"\bпика\b", "пк", text)
    text = re.sub(r"\bпэ ка\b", "пк", text)
    tokens = text.split()
    out: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in _CODE_LETTERS:
            code = tok
            j = i + 1
            # поглощаем цифры кода, но НЕ количество: цифра, за которой идёт
            # единица (мешок/кг), — это количество, останавливаемся перед ней
            while j < len(tokens) and re.fullmatch(r"\d+", tokens[j]):
                nxt = tokens[j + 1] if j + 1 < len(tokens) else ""
                if nxt in _BAG_WORDS or nxt in _KG_WORDS:
                    break
                code += "-" + tokens[j]
                j += 1
            out.append(code)
            i = j
        else:
            out.append(tok)
            i += 1
    return " ".join(out)


_PUNCT_RE = re.compile(r"[.,;:!?()\[\]{}%\"'«»\-–—/]+")
_SPACE_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Полная нормализация строки для матчинга и разбора."""
    text = text.lower().strip()
    # дефисы в кодах восстановим отдельно; сначала уберём пунктуацию как разделители
    text = _PUNCT_RE.sub(" ", text)
    text = _SPACE_RE.sub(" ", text).strip()
    text = _replace_number_words(text)
    text = _merge_codes(text)
    text = _SPACE_RE.sub(" ", text).strip()
    return text


def tokenize_name(text: str) -> set[str]:
    """Множество значимых токенов для сравнения (без чисел/единиц/служебных)."""
    tokens = set()
    for tok in normalize(text).split():
        if tok in _BAG_WORDS or tok in _KG_WORDS or tok in _SEP_WORDS or tok in _TOTAL_WORDS:
            continue
        if re.fullmatch(r"\d+", tok):
            continue
        tokens.add(tok)
    return tokens


_MONEY_KEYS = {
    "cash": {"нал", "наличные", "наличными", "наличка", "кэш"},
    "transfer": {"перевод", "переводом", "переводы", "безнал", "карта", "картой"},
    "expense": {"расход", "расходы", "расхода", "трата", "траты", "затраты"},
    "closing": {"касса", "остаток", "конец", "закрытие"},
}


def parse_money_report(text: str) -> dict:
    """«нал 15000 перевод 8000 расход 2000 касса 5000» → {cash, transfer, expense, closing}.

    Число присваивается ближайшему предшествующему ключевому слову.
    """
    norm = normalize(text)
    tokens = norm.split()
    result = {"cash": 0.0, "transfer": 0.0, "expense": 0.0, "closing": None}
    current: Optional[str] = None
    for tok in tokens:
        matched_key = None
        for key, words in _MONEY_KEYS.items():
            if tok in words:
                matched_key = key
                break
        if matched_key:
            current = matched_key
            continue
        if re.fullmatch(r"\d+(?:\.\d+)?", tok) and current:
            result[current] = float(tok)
            current = None
    return result


@dataclass
class ParsedLine:
    """Одна разобранная позиция из речи."""
    raw: str
    name_tokens: set[str] = field(default_factory=set)
    name_text: str = ""
    qty: Optional[float] = None
    unit: str = "bag"  # bag | kg
    line_sum: Optional[float] = None


_UNIT_WORDS = _BAG_WORDS | _KG_WORDS


def _split_by_qty_anchors(tokens: list[str]) -> list[list[str]]:
    """Доп. разбиение внутри куска по «якорям количества» — паре
    «число + мешок/кг/штука». В слитной речи без пауз («...500 рублей раменский
    пк-4 два мешка...») именно такая пара обычно маркирует начало новой позиции,
    даже если продавец не сказал «дальше»/«потом».
    """
    anchors = [
        i for i, tok in enumerate(tokens)
        if re.fullmatch(r"\d+", tok) and i + 1 < len(tokens) and tokens[i + 1] in _UNIT_WORDS
    ]
    if len(anchors) < 2:
        return [tokens]

    boundaries = []
    for a in anchors[1:]:
        last_digit = next(
            (i for i in range(a - 1, -1, -1) if re.fullmatch(r"\d+", tokens[i])), None
        )
        boundaries.append((last_digit + 1) if last_digit is not None else a)

    segments: list[list[str]] = []
    start = 0
    for b in boundaries:
        if b > start:
            segments.append(tokens[start:b])
            start = b
    segments.append(tokens[start:])
    return [s for s in segments if s]


def split_into_lines(norm_text: str) -> list[str]:
    """Делит нормализованный текст на позиции: сперва по явным словам-разделителям
    («дальше», «потом» и т.п.), затем внутри каждого куска — по якорям количества,
    чтобы ловить слитную речь без пауз-разделителей."""
    tokens = norm_text.split()
    chunks: list[list[str]] = [[]]
    for tok in tokens:
        if tok in _SEP_WORDS:
            if chunks[-1]:
                chunks.append([])
            continue
        chunks[-1].append(tok)
    chunks = [c for c in chunks if c]

    lines: list[str] = []
    for chunk in chunks:
        for seg in _split_by_qty_anchors(chunk):
            lines.append(" ".join(seg))
    return lines


def parse_line(norm_line: str, *, expect_sum: bool = True) -> ParsedLine:
    """Из нормализованной строки извлекает имя, количество, единицу, сумму.

    Правила: числа-кандидаты — все отдельные цифровые токены (коды вида пк-1-2
    не считаются, т.к. они склеены дефисом). Количество — число рядом с единицей
    или первое небольшое число; сумма — число рядом с 'итого' либо самое большое.
    """
    tokens = norm_line.split()
    numbers: list[tuple[int, float]] = []  # (позиция, значение)
    unit = "bag"
    qty: Optional[float] = None
    line_sum: Optional[float] = None
    total_flag = False

    for idx, tok in enumerate(tokens):
        if tok in _KG_WORDS:
            unit = "kg"
        if tok in _TOTAL_WORDS:
            total_flag = True
        if re.fullmatch(r"\d+", tok):
            numbers.append((idx, float(tok)))

    # определить количество: число, за которым следует единица (мешок/кг)
    for idx, val in numbers:
        nxt = tokens[idx + 1] if idx + 1 < len(tokens) else ""
        if nxt in _BAG_WORDS or nxt in _KG_WORDS:
            qty = val
            break

    remaining = [n for n in numbers if not (qty is not None and n[1] == qty)]

    # сумма: если есть 'итого' — число после него; иначе самое большое из оставшихся
    if expect_sum and remaining:
        if total_flag:
            # число после слова-итого
            total_idx = next((i for i, t in enumerate(tokens) if t in _TOTAL_WORDS), None)
            after = [v for (i, v) in remaining if total_idx is not None and i > total_idx]
            line_sum = after[0] if after else max(v for _, v in remaining)
        else:
            line_sum = max(v for _, v in remaining)
        remaining = [n for n in remaining if n[1] != line_sum]

    # если количество ещё не найдено — берём наименьшее оставшееся число
    if qty is None and remaining:
        qty = min(v for _, v in remaining)

    name_tokens = tokenize_name(norm_line)
    name_text = " ".join(
        t for t in tokens
        if not re.fullmatch(r"\d+", t)
        and t not in _BAG_WORDS and t not in _KG_WORDS and t not in _TOTAL_WORDS
    ).strip()

    return ParsedLine(
        raw=norm_line,
        name_tokens=name_tokens,
        name_text=name_text,
        qty=qty,
        unit=unit,
        line_sum=line_sum,
    )
