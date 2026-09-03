"""Общие вызовы МойСклад: мета, доп.поля, проекты, организация."""

from db import get_setting, set_setting
from net import MS_BASE, env, ms_headers, req

PRODUCT_ATTRS = [
    ("Литраж, л", "double", "ATTR_LITERS_ID"),
    ("Клиент фулфилмента", "counterparty", "ATTR_CLIENT_ID"),
    ("Маркетплейс", "string", "ATTR_MP_ID"),
    ("GTIN", "string", "ATTR_GTIN_ID"),
]

AGENT_ATTRS = [
    ("WB токен", "text", "ATTR_AGENT_WB"),
    ("Ozon Client-Id", "string", "ATTR_AGENT_OZON_CID"),
    ("Ozon Api-Key", "text", "ATTR_AGENT_OZON_KEY"),
    ("Тариф хранения, руб/л/сутки", "double", "ATTR_AGENT_TARIFF_STORE"),
    ("Тариф приёмки, руб/шт", "double", "ATTR_AGENT_TARIFF_IN"),
    ("Тариф отгрузки, руб/шт", "double", "ATTR_AGENT_TARIFF_OUT"),
    ("Синхронизация", "text", "ATTR_AGENT_SYNC"),
]

ORDER_ATTRS = [
    ("Номер отправления", "string", "ATTR_ORDER_POSTING"),
    ("Дата отгрузки по МП", "time", "ATTR_ORDER_SHIPDATE"),
    ("Кабинет", "string", "ATTR_ORDER_CABINET"),
    ("Статус на площадке", "string", "ATTR_ORDER_MP_STATUS"),
]

PROJECTS = {
    ("wb", "fbs"): "WB ФБС",
    ("wb", "fbo"): "WB ФБО",
    ("ozon", "fbs"): "Ozon ФБС",
    ("ozon", "fbo"): "Ozon ФБО",
}

TRACKING = {
    "Не маркируется": "NOT_TRACKED",
    "Одежда": "LP_CLOTHES",
    "Обувь": "SHOES",
    "БАД": "FOOD_SUPPLEMENT",
    "Парфюм": "PERFUMERY",
    "Шины": "TIRES",
    "Молочка": "MILK",
    "Вода": "WATER",
}

TRACKING_LABELS = list(TRACKING.keys())

# Предмет WB / название Ozon → тип продукции МойСклад.
_KIND_HINTS = (
    (
        (
            "босонож",
            "балетк",
            "кроссов",
            "сапог",
            "туфл",
            "кеды",
            "кед ",
            "ботин",
            "обув",
            "сандал",
            "тапоч",
            "лофер",
            "мокасин",
            "слипон",
            "угги",
            "вален",
            "лодочк",
            "полусапож",
        ),
        "Обувь",
    ),
    (
        (
            "куртк",
            "пуховик",
            "плать",
            "футболк",
            "брюк",
            "джин",
            "пальто",
            "свитер",
            "худи",
            "рубаш",
            "юбк",
            "костюм",
            "жилет",
            "толстов",
            "комбинезон",
            "одежд",
            "бель",
            "пиджак",
            "блуз",
            "кардиган",
            "ветров",
        ),
        "Одежда",
    ),
    (("парфюм", "духи", "туалетная вода"), "Парфюм"),
    (("бад", "витамин", "биологически"), "БАД"),
    (("шин",), "Шины"),
    (("молок", "кефир", "творог", "йогурт"), "Молочка"),
    (("вода пить", "питьевая вода"), "Вода"),
)


def kind_from_subject(subject, need_kiz=None):
    text = (subject or "").lower()
    for keys, kind in _KIND_HINTS:
        if any(key in text for key in keys):
            return kind
    if need_kiz is False:
        return "Не маркируется"
    if need_kiz is True:
        return ""
    return "Не маркируется"


def ms_meta(typ, oid):
    return {
        "meta": {
            "href": "%s/entity/%s/%s" % (MS_BASE, typ, oid),
            "type": typ,
            "mediaType": "application/json",
        }
    }


def org_id():
    return env("MS_ORG_ID")


def store_id():
    return env("MS_STORE_ID")


def attr_value(entity, name, meta_key, value):
    aid = get_setting(meta_key)
    if not aid or value is None or value == "":
        return None
    return {
        "meta": {
            "href": "%s/entity/%s/metadata/attributes/%s" % (MS_BASE, entity, aid),
            "type": "attributemetadata",
            "mediaType": "application/json",
        },
        "value": value,
    }


def product_attrs(liters, agent_id, marketplace, gtin):
    out = []
    if liters is not None and liters != "":
        try:
            val = float(str(liters).replace(",", "."))
        except ValueError:
            val = 0.0
        item = attr_value("product", "Литраж, л", "ATTR_LITERS_ID", val)
        if item:
            out.append(item)
    if agent_id:
        item = attr_value("product", "Клиент фулфилмента", "ATTR_CLIENT_ID", ms_meta("counterparty", agent_id))
        if item:
            out.append(item)
    if marketplace:
        item = attr_value("product", "Маркетплейс", "ATTR_MP_ID", marketplace)
        if item:
            out.append(item)
    if gtin:
        item = attr_value("product", "GTIN", "ATTR_GTIN_ID", gtin)
        if item:
            out.append(item)
    return out


def order_attrs(posting, ship_date, cabinet, status):
    out = []
    mapping = (
        ("ATTR_ORDER_POSTING", posting),
        ("ATTR_ORDER_SHIPDATE", ship_date),
        ("ATTR_ORDER_CABINET", cabinet),
        ("ATTR_ORDER_MP_STATUS", status),
    )
    for key, val in mapping:
        item = attr_value("customerorder", key, key, val)
        if item:
            out.append(item)
    return out


def attrs_by_name(entity):
    out = {}
    for row in entity.get("attributes") or []:
        out[row.get("name") or ""] = row.get("value")
    return out


def ensure_attrs(entity, wanted):
    r = req("GET", MS_BASE + "/entity/%s/metadata/attributes" % entity, headers=ms_headers())
    r.raise_for_status()
    existing = {row.get("name"): row for row in (r.json().get("rows") or [])}
    for name, typ, key in wanted:
        row = existing.get(name)
        if not row:
            created = req(
                "POST",
                MS_BASE + "/entity/%s/metadata/attributes" % entity,
                headers=ms_headers(),
                json={"name": name, "type": typ, "required": False},
            )
            if created.status_code not in (200, 201) and typ == "text":
                created = req(
                    "POST",
                    MS_BASE + "/entity/%s/metadata/attributes" % entity,
                    headers=ms_headers(),
                    json={"name": name, "type": "string", "required": False},
                )
            if created.status_code not in (200, 201):
                raise SystemExit(
                    "не создал поле %s/%s: %s %s" % (entity, name, created.status_code, created.text[:200])
                )
            row = created.json()
            print("создано %s/%s id=%s" % (entity, name, row.get("id")))
        else:
            print("есть %s/%s id=%s" % (entity, name, row.get("id")))
        set_setting(key, row.get("id"))


def ensure_all_attrs():
    ensure_attrs("product", PRODUCT_ATTRS)
    ensure_attrs("counterparty", AGENT_ATTRS)
    ensure_attrs("customerorder", ORDER_ATTRS)


def ensure_projects():
    r = req("GET", MS_BASE + "/entity/project?limit=100", headers=ms_headers())
    r.raise_for_status()
    have = {row.get("name"): row.get("id") for row in (r.json().get("rows") or [])}
    for name in PROJECTS.values():
        if name not in have:
            created = req(
                "POST",
                MS_BASE + "/entity/project",
                headers=ms_headers(),
                json={"name": name},
            )
            if created.status_code not in (200, 201):
                raise SystemExit("не создал проект %s: %s %s" % (name, created.status_code, created.text[:200]))
            have[name] = created.json().get("id")
            print("проект создан %s id=%s" % (name, have[name]))
        set_setting("PROJECT_%s" % name.replace(" ", "_"), have[name])
    return have


SERVICES = {
    "storage": "Хранение на складе",
    "intake": "Приёмка товара",
    "ship": "Отгрузка заказов",
}


def ensure_services():
    r = req("GET", MS_BASE + "/entity/service?limit=100", headers=ms_headers())
    r.raise_for_status()
    have = {row.get("name"): row.get("id") for row in (r.json().get("rows") or [])}
    out = {}
    for key, name in SERVICES.items():
        if name not in have:
            created = req(
                "POST",
                MS_BASE + "/entity/service",
                headers=ms_headers(),
                json={"name": name, "vat": 0, "vatEnabled": False},
            )
            if created.status_code not in (200, 201):
                raise RuntimeError(
                    "не создал услугу %s: %s %s" % (name, created.status_code, created.text[:200])
                )
            have[name] = created.json().get("id")
            print("услуга создана %s id=%s" % (name, have[name]))
        set_setting("SERVICE_%s" % key, have[name])
        out[key] = have[name]
    return out


def service_id(key):
    cached = get_setting("SERVICE_%s" % key)
    if cached:
        return cached
    return ensure_services().get(key)


def project_id(marketplace, kind):
    name = PROJECTS.get((marketplace, kind))
    if not name:
        return None
    cached = get_setting("PROJECT_%s" % name.replace(" ", "_"))
    if cached:
        return cached
    return ensure_projects().get(name)


def find_entity(entity, field, value):
    r = req(
        "GET",
        MS_BASE + "/entity/%s" % entity,
        headers=ms_headers(),
        params={"filter": "%s=%s" % (field, value), "limit": 2},
    )
    if r.status_code != 200:
        return None
    rows = r.json().get("rows") or []
    return rows[0] if rows else None


def tracking_code(label):
    text = (label or "").strip() or "Не маркируется"
    return TRACKING.get(text, "NOT_TRACKED")
