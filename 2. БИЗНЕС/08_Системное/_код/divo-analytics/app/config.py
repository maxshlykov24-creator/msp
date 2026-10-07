from __future__ import annotations

from collections import defaultdict

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # amoCRM
    amo_base_url: str = "https://divomotors.amocrm.ru"
    amo_access_token: str = ""
    amo_account_id: int = 32590598

    # DB
    database_url: str = "postgresql+psycopg2://divoanalytics:divoanalytics@db:5432/divoanalytics"

    # Auth: основной логин (Николас) + необязательные доп. учётки
    auth_login: str = "divo"
    auth_password_hash: str = ""
    # Формат: login:bcrypt-hash;login2:hash2  (в .env каждый $ хеша писать как $$)
    auth_users: str = ""
    session_secret: str = "change-me"
    # На проде за HTTPS — true. Для локального http://127.0.0.1 — false
    # (иначе браузер не примет cookie с Secure и вход зациклится на /login).
    auth_cookie_secure: bool = True

    # Schedule
    tz: str = "Europe/Moscow"
    collect_interval_min: int = 10
    collect_hours: str = "0-23"

    # Воронка «Продажи» DIVO Motors
    pipeline_sales: int = 10372290
    status_won: int = 142
    status_lost: int = 143
    status_visit_planned: int = 82003654
    status_visit_done: int = 82004138
    status_purchase_agreed: int = 82004142

    # Менеджеры отдела продаж, попадающие в дашборд (whitelist, не EXCLUDED)
    allowed_manager_ids: str = "13180098,13334858,13835174,14181846"
    # Соответствие amo user_id -> ключ в объекте mgr{} на фронте
    manager_key_map: str = "13180098:eugene,13334858:nikita,13835174:elzar,14181846:nazar"
    # Подписи карточек менеджеров. Порядок задаёт порядок карточек на дашборде.
    manager_names: str = "eugene:Евгений,nikita:Никита,elzar:Эльзар,nazar:Назар"

    # Custom fields (id) — см. КОНТЕКСТ_AMOCRM.md
    field_source: int = 2026903
    field_payment_type: int = 2026901
    field_trade_in: int = 2026907
    field_loss_reason: int = 2026915
    field_agreed: int = 2027799
    # Чекбоксы для бэкфилла из истории этапов воронки (см. scripts/backfill_visits.py)
    field_visit_planned: int = 2258985
    field_visit_done: int = 2026919

    # Ожидаемые значения enum-полей (для валидации схемы перед запуском collector)
    payment_cash_value: str = "Наличные"
    payment_invoice_values: str = "Счет,Счёт"
    trade_in_yes_value: str = "Есть"

    run_mode: str = "api"

    # Звонки: независимые переключатели сбора, платного анализа и доставок.
    calls_enabled: bool = False
    calls_process_enabled: bool = False
    calls_calibration_only: bool = False  # до приёмки не оплачивать рабочие звонки
    calls_amo_enabled: bool = False
    calls_telegram_enabled: bool = False
    calls_start_at: int = 0  # 0 = момент первого включения, не история
    calls_overlap_sec: int = 86400
    calls_recording_wait_hours: int = 48
    calls_max_inflight: int = 3
    calls_max_audio_mb: int = 100
    calls_dashboard_url: str = "https://divomotors-analytics.ru"
    calls_recording_hosts: str = ""  # разрешённые точные хосты Mango, проверены на пилоте
    calls_manager_map: str = ""  # source:employee:user_id; только проверенная привязка
    calls_verified_authors: str = ""  # amo user_id, авторство проверено на пилоте
    calls_manager_names: str = "13180098:Евгений;13334858:Никита;13835174:Эльзар;14181846:Назар"
    nexara_api_key: str = ""
    nexara_base_url: str = "https://api.nexara.ru/v1"
    calls_telegram_bot_token: str = ""
    calls_telegram_chat_id: str = ""

    @property
    def call_manager_names(self) -> dict[int, str]:
        return {int(p.split(":", 1)[0]): p.split(":", 1)[1]
                for p in self.calls_manager_names.split(";") if ":" in p}

    @property
    def auth_accounts(self) -> dict[str, str]:
        out: dict[str, str] = {}
        if self.auth_login and self.auth_password_hash:
            out[self.auth_login] = self.auth_password_hash
        for pair in self.auth_users.split(";"):
            pair = pair.strip()
            if not pair or ":" not in pair:
                continue
            login, hashed = pair.split(":", 1)
            login, hashed = login.strip(), hashed.strip()
            if login and hashed:
                out[login] = hashed
        return out

    @property
    def allowed_manager_id_set(self) -> set[int]:
        return {int(x.strip()) for x in self.allowed_manager_ids.split(",") if x.strip()}

    @property
    def manager_key_map_dict(self) -> dict[int, str]:
        out: dict[int, str] = {}
        for pair in self.manager_key_map.split(","):
            pair = pair.strip()
            if not pair or ":" not in pair:
                continue
            uid, key = pair.split(":", 1)
            out[int(uid.strip())] = key.strip()
        return out

    @property
    def manager_display_list(self) -> list[dict]:
        """[{key, name, initial}] в порядке manager_names — контракт для фронта."""
        out: list[dict] = []
        known = set(self.manager_key_map_dict.values())
        for pair in self.manager_names.split(","):
            pair = pair.strip()
            if not pair or ":" not in pair:
                continue
            key, name = pair.split(":", 1)
            key, name = key.strip(), name.strip()
            if not key or key not in known:
                continue
            out.append({"key": key, "name": name, "initial": name[:1].upper()})
        # Менеджер есть в key_map, но подпись не задана — показываем ключ, а не прячем цифры.
        for key in self.manager_key_map_dict.values():
            if all(x["key"] != key for x in out):
                out.append({"key": key, "name": key, "initial": key[:1].upper()})
        # Одинаковая первая буква (Никита / Назар) — аватарки станут неразличимы,
        # поэтому у таких имён берём две буквы.
        counts: dict[str, int] = defaultdict(int)
        for item in out:
            counts[item["initial"]] += 1
        for item in out:
            if counts[item["initial"]] > 1:
                item["initial"] = item["name"][:2].capitalize()
        return out

    @property
    def payment_invoice_value_set(self) -> set[str]:
        return {x.strip() for x in self.payment_invoice_values.split(",") if x.strip()}


settings = Settings()

# Порядок и имена этапов воронки «Продажи» — синхронизировано 1:1 с фронтом
# (см. STAGE_ORDER в web/index.html) и с КОНТЕКСТ_AMOCRM.md.
STAGE_NAMES: dict[int, str] = {
    82003642: "Неразобранное",
    82003646: "Новая заявка",
    82003650: "Контакт установлен",
    82003654: "Запланирован визит",
    82004138: "Визит состоялся",
    82004142: "Покупка согласована",
    142: "Успех",
    143: "Провал",
    82249454: "Спам",
}

STAGE_ORDER: list[str] = [
    "Неразобранное", "Новая заявка", "Контакт установлен", "Запланирован визит",
    "Визит состоялся", "Покупка согласована", "Успех", "Провал", "Спам",
]
