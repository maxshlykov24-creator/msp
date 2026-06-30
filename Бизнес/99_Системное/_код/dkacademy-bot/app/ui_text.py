"""UX-тексты бота DK Академии. Все строки в одном месте — проще менять."""
from __future__ import annotations

from urllib.parse import quote


CB_MY_DELIVERIES = "my_deliveries"

# Текст, который попадает в черновик при открытии чата с менеджером (?text=)
MANAGER_CHAT_PREFILL = "Здравствуйте! Вопрос по заказу в DK Academy."


def manager_link(username: str) -> str:
    u = (username or "").lstrip("@").strip()
    if not u:
        return "https://t.me"
    q = quote(MANAGER_CHAT_PREFILL, safe="")
    return f"https://t.me/{u}?text={q}"


def manager_max_link(url: str) -> str:
    """Прямая ссылка на менеджера в MAX (max.ru/u/…)."""
    u = (url or "").strip()
    return u if u else "https://max.ru"


def manager_handle(username: str) -> str:
    u = (username or "").lstrip("@").strip()
    return f"@{u}" if u else "@dk_academy"


# ВРЕМЕННЫЙ текст согласия (заглушка). Финальную редакцию пришлёт клиент —
# заменить значение CONSENT_TEXT, перезапуск не требует правок в других местах.
CONSENT_TEXT = (
    "Нажимая «Поделиться номером», вы даёте согласие на обработку персональных "
    "данных (номер телефона) для информирования о статусе ваших заказов. "
    "(Тестовый текст — финальная редакция появится позже.)"
)


def hello_text() -> str:
    return (
        "Здравствуйте! Добро пожаловать в DK Академию 🌿\n\n"
        "Через этот бот вы будете получать актуальные статусы доставки своих заказов.\n\n"
        "Поделитесь номером телефона кнопкой ниже — это займёт несколько секунд.\n\n"
        f"{CONSENT_TEXT}"
    )


def max_hello_text() -> str:
    """Онбординг в MAX: кнопка контакта в MAX часто не отдаёт номер,
    поэтому основной способ — просто написать номер сообщением."""
    return (
        "Здравствуйте! Это DK Академия 🌿\n\n"
        "Здесь вы будете получать уведомления о статусе своих заказов.\n\n"
        "Напишите ваш номер телефона — и мы вас подключим.\n\n"
        f"{CONSENT_TEXT}"
    )


def phone_retry_text() -> str:
    return (
        "Не получилось распознать номер 🙈\n\n"
        "Пришлите его, пожалуйста, ещё раз одним сообщением — "
        "например, +7 900 123-45-67."
    )


def phone_give_up_text(manager_username: str) -> str:
    return (
        "Никак не получается распознать номер 😔\n\n"
        "Напишите, пожалуйста, менеджеру — он подключит вас вручную: "
        f"{manager_handle(manager_username)}"
    )


def phone_give_up_max_text(manager_max_url: str) -> str:
    """Версия для MAX: вместо @username даём кликабельную ссылку в MAX."""
    url = (manager_max_url or "").strip() or "https://max.ru"
    return (
        "Никак не получается распознать номер 😔\n\n"
        "Напишите, пожалуйста, менеджеру — он подключит вас вручную:\n"
        f"{url}"
    )


def contact_accepted_text(manager_username: str) -> str:
    return (
        "Отлично, номер подтверждён ✅\n\n"
        "Теперь все обновления по доставке будут приходить сюда. "
        "Как только заказ отправят — вы узнаете об этом первыми 📦\n\n"
        f"Остались вопросы? Мы рядом: {manager_handle(manager_username)}"
    )


def contact_no_match_text(manager_username: str) -> str:
    return (
        "Номер принят ✅\n\n"
        "По этому номеру активных заказов пока не найдено. "
        "Как только менеджер оформит отправку — уведомления начнут "
        "приходить сюда автоматически.\n\n"
        f"Есть вопросы? Напишите нам: {manager_handle(manager_username)}"
    )


def contact_wrong_text() -> str:
    return (
        "Пожалуйста, поделитесь своим контактом с помощью кнопки ниже — "
        "именно своим, а не пересланным."
    )


def contact_bad_phone_text() -> str:
    return "Не удалось распознать номер. Пожалуйста, попробуйте ещё раз кнопкой ниже."


def contact_error_text(manager_username: str) -> str:
    return (
        "Что-то пошло не так при привязке номера. "
        "Попробуйте позже или напишите менеджеру: "
        f"{manager_handle(manager_username)}"
    )


def status_no_binding_text() -> str:
    return (
        "Чтобы видеть статусы доставок, сначала нажмите /start "
        "и поделитесь номером телефона 📱"
    )


def status_empty_text(manager_username: str) -> str:
    return (
        "Активных доставок пока нет 📭\n\n"
        "Как только ваш заказ будет отправлен, мы сразу уведомим вас.\n\n"
        f"Есть вопросы? {manager_handle(manager_username)}"
    )


def status_list_text(items: list[tuple[str, str]], manager_username: str) -> str:
    """items: список (track, status_text). track пустой → «заказ»."""
    all_pending = all(not (s or "").strip() for _, s in items)
    lines = ["📦 Ваши доставки:\n"]
    for track, status in items:
        t = (track or "").strip() or "заказ"
        s = (status or "").strip()
        if not s:
            s = f"статус неизвестен — уточните у менеджера {manager_handle(manager_username)}"
        lines.append(f"• Трек {t}\n  {s}")
    if all_pending:
        lines.append("\nКак только менеджер внесёт данные доставки — статус обновится автоматически.")
    return "\n".join(lines)


def fallback_text(manager_username: str) -> str:
    return (
        "Этот бот создан для отслеживания доставок DK Академии 📦\n\n"
        "Нажмите «📦 Мои доставки», чтобы узнать актуальный статус заказа.\n\n"
        f"По всем остальным вопросам — менеджер всегда на связи: {manager_handle(manager_username)}"
    )


def status_error_text(manager_username: str) -> str:
    return (
        "Не удалось получить статусы прямо сейчас — попробуйте чуть позже.\n\n"
        f"Если вопрос срочный, напишите менеджеру: {manager_handle(manager_username)}"
    )


def manager_contact_text(_manager_username: str) -> str:
    """Текст при «Менеджер»: ник не показываем — ниже кнопка с переходом в чат."""
    return "Напишите вашему менеджеру — мы всегда рядом 💬"
