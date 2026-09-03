from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Kommo (read-only)
    kommo_base: str = "https://licensebridgeusa.kommo.com/api/v4"
    kommo_token: str = ""
    kommo_account_id: int = 34679511
    kommo_max_rps: float = 4.0

    # Воронки
    pipeline_main: int = 11274779
    pipeline_sborka: int = 12079591
    status_won: int = 142
    status_lost: int = 143

    # Auth
    auth_login: str = "licensebridge"
    auth_password_hash: str = ""
    session_secret: str = "change-me"
    auth_cookie_secure: bool = True

    # Расписание сбора
    tz: str = "Europe/Moscow"
    # Даты сделок (создание, закрытие, переход в «Сборку») считаем в UTC —
    # так был посчитан согласованный срез 22.07.2026: сверка показала 0
    # расхождений на 1237 закрытиях. Смена пояса сдвинет часть исторических
    # дат на сутки и разойдётся с цифрами, которые клиент уже видел.
    business_tz: str = "UTC"
    # Полная перевыгрузка Kommo каждые N минут (сейчас ~25–35 сек, нагрузка мала).
    collect_interval_min: int = 30
    collect_on_start: bool = True
    # Старше этого возраста данные считаются несвежими: дашборд показывает
    # красный баннер вместо тихой выдачи устаревших цифр.
    # При интервале 30 мин порог 2 ч ≈ 4 пропущенных прогона подряд.
    stale_after_hours: int = 2

    data_dir: str = "/data"
    # Сбор живёт внутри процесса API (см. scheduler.start_background).
    run_scheduler: bool = True


settings = Settings()

# Нормы SLA по этапам основной воронки, дней. Гипотеза аудита 2026-07-22,
# щедрая намеренно — клиент может ужесточить (ПАСПОРТ_МЕТРИК.md §3).
SLA_DAYS: dict[str, int] = {
    "Новая заявка": 1,
    "Первичный контакт": 2,
    "Квалификация": 5,
    "В работе": 14,
    "Выставлен счет/согласовываю оплату": 10,
    "Прогреваю/Отложенный спрос": 30,
}

# origin чата в Kommo → человекочитаемый канал. Неизвестный origin остаётся
# как есть: лучше показать техническую строку, чем молча потерять источник.
TALK_ORIGINS: dict[str, str] = {
    "tech.widget.e-chat": "e-chat",
    "com.ringcentral.sms": "SMS/RingCentral",
    "instagram_business": "Instagram",
    "facebook": "Facebook",
    "com.wazzup.whatsapp": "WhatsApp",
    "com.wazzup24-1": "Wazzup",
    "com.wazzup.instagram": "Instagram",
    "tiktok_kommo": "Tik-Tok Ads",
    "com.apple.bm": "Apple Business Chat",
}

# Для этих origin пометка «(talk)» не нужна: канал тот же, что у рекламных лидов.
TALK_ORIGINS_NO_SUFFIX = {"facebook"}

# Значения Channel/utm, которые не являются источником (мусор в поле).
CHANNEL_JUNK = {"да", "нет", "yes", "no", "да_/_yes", "нет_/_no", "-", "—", "0", "1"}

# Разные написания Facebook в Channel/utm_source.
FB_ALIASES = {"fb", "facebook", "fb organic", "facebook ads"}

# Сотрудники, удалённые из Kommo: API их больше не отдаёт (404), а в истории
# событий они есть — без этой карты 82.7% исторических отказов остались бы
# безымянными. Имена зафиксированы аудитом 2026-07-22 (ПАСПОРТ_МЕТРИК.md §4).
FORMER_USERS: dict[int, str] = {
    14043563: "Лилия Солдатченкова",
    15289892: "Лэйла Баркова",
    15289896: "Светлана Вахрушева",
    15380172: "Алина Улизко",
}
UNKNOWN_USER_LABEL = "Бывший сотрудник"
