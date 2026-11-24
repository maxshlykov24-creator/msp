"""Подтверждённые пары артикул-GTIN. Это не кэш каталога.

Кэш при загрузке карточек стирается и заполняется заново из штрихкода.
У Вольготова штрихкод вида OZN, поэтому номер из кэша пропадает.
Список живёт рядом с кодом и включается только для клиента IPVOLGOTOVAR
и кабинета Ozon 3743011. Пустой номер не превращается в нули.

«М» и латинская «M» считаются одним артикулом. Если после этого на один
артикул садятся два номера или один номер на два артикула, список молчит:
иначе сверка снова примет чужой код.
"""

CLIENT_CODE = "IPVOLGOTOVAR"
CABINET_EXT = "3743011"

# Артикул как в GTIN.xls, номер строкой из 14 цифр.
_ROWS = (
    ("Комбинезон розовый L", "04681117137054"),
    ("Комбинезон розовый XL", "04681117137061"),
    ("Комбинезон черный S", "04681117137115"),
    ("Комбинезон белый L", "04681117137092"),
    ("Комбинезон белый с выточкой М", "04681117137207"),
    ("Комбинезон белый М", "04681117137085"),
    ("Комбинезон синий с выточкой L", "04681117137252"),
    ("Комбинезон синий с выточкой М", "04681117137245"),
    ("Топ спортивный болеро белый S", "04681117137306"),
    ("Комбинезон розовый М", "04681117137047"),
    ("Комбинезон черный М", "04681117137122"),
    ("Топ спортивный болеро черный М", "04681117137344"),
    ("Топ спортивный болеро черный S", "04681117137337"),
    ("Комбинезон черный L", "04681117137139"),
    ("Комбинезон белый с выточкой XL", "04681117137221"),
    ("Комбинезон белый с выточкой S", "04681117137191"),
    ("Топ спортивный болеро черный L", "04681117137351"),
    ("Комбинезон коричневый L", "04681117137177"),
    ("Комбинезон белый S", "04681117137078"),
    ("Топ спортивный болеро белый L", "04681117137320"),
    ("Топ спортивный болеро белый М", "04681117137313"),
    ("Комбинезон белый с выточкой L", "04681117137214"),
    ("Топ спортивный болеро розовый L", "04681117137290"),
    ("Комбинезон коричневый М", "04681117137160"),
    ("Комбинезон синий с выточкой S", "04681117137238"),
    ("Топ спортивный болеро розовый М", "04681117137283"),
    ("Топ спортивный болеро розовый S", "04681117137276"),
    ("Комбинезон синий с выточкой XL", "04681117137269"),
    ("Комбинезон коричневый S", "04681117137153"),
    ("Комбинезон розовый S", "04681117137030"),
    ("Комбинезон черный XL", "04681117137146"),
    ("Комбинезон белый XL", "04681117137108"),
    ("Комбинезон коричневый XL", "04681117137184"),
)


def article_key(article):
    """Один ключ для кириллической «М» и латинской «M»."""
    return str(article or "").strip().replace("\u041c", "M").replace("\u043c", "m")


def digits14(value):
    """14 цифр номера. Пустое и обрывок остаются пустыми, нулями не добиваются."""
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(digits) == 13:
        return digits.zfill(14)
    if len(digits) == 14:
        return digits
    return ""


def load_pairs(rows):
    """Пустые словари, если список сам себе противоречит."""
    by_article, by_gtin, display = {}, {}, {}
    for article, gtin in rows:
        number = str(gtin or "").strip()
        key = article_key(article)
        if len(number) != 14 or not number.isdigit() or not key:
            return {}, {}, {}
        if key in by_article and by_article[key] != number:
            return {}, {}, {}
        if number in by_gtin and by_gtin[number] != key:
            return {}, {}, {}
        by_article[key] = number
        by_gtin[number] = key
        display.setdefault(key, str(article).strip())
    return by_article, by_gtin, display


_BY_ARTICLE, _BY_GTIN, _DISPLAY = load_pairs(_ROWS)
if len(_BY_ARTICLE) != 33 or len(_BY_GTIN) != 33:
    _BY_ARTICLE, _BY_GTIN, _DISPLAY = {}, {}, {}


def applies(client_code, client_id_ext):
    return bool(_BY_ARTICLE) and str(client_code or "").strip() == CLIENT_CODE and str(client_id_ext or "").strip() == CABINET_EXT


def listed_gtin(client_code, client_id_ext, article):
    if not applies(client_code, client_id_ext):
        return ""
    return _BY_ARTICLE.get(article_key(article), "")


def bind_gtin(client_code, client_id_ext, article, item_gtin):
    """Номер для уже существующей проверки.

    None значит, что список этот артикул не ведёт и поле заказа остаётся как было.
    Сообщение значит, что в заказе уже лежит другой номер: победителя не выбираем.
    """
    number = listed_gtin(client_code, client_id_ext, article)
    if not number:
        return None, ""
    have = digits14(item_gtin)
    if have and have != number:
        return None, "В заказе номер %s, в списке номер %s. Отправка остановлена." % (have, number)
    return number, ""


def scan_stop(client_code, client_id_ext, article, code_gtin):
    """Пусто, если код можно нести дальше. В тексте нет самого кода."""
    if not applies(client_code, client_id_ext):
        return ""
    number = _BY_ARTICLE.get(article_key(article), "")
    got = str(code_gtin or "")
    if got and (len(got) != 14 or not got.isdigit()):
        got = ""
    if number:
        if not got:
            return "В коде нет номера. Отсканируй ещё раз."
        if got != number:
            return "Ждали артикул %s, номер %s. В коде номер %s." % (article, number, got)
    owner = _BY_GTIN.get(got, "") if got else ""
    if owner and owner != article_key(article):
        return "Номер %s относится к артикулу %s, а выбран %s." % (got, _DISPLAY.get(owner, owner), article)
    return ""


def ship_stop(client_code, client_id_ext, article, code_gtin):
    """Та же сверка для кода, который уже лежит на заказе."""
    text = scan_stop(client_code, client_id_ext, article, code_gtin)
    if not text:
        return ""
    if text.startswith("В коде нет номера"):
        return "Артикул %s: в коде нет номера. Сними код и отсканируй свой." % (article or "товара")
    return text + " Сними код и отсканируй свой."
