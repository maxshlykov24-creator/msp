from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # amoCRM
    amo_base_url: str = "https://dkdkacademy.amocrm.ru"
    amo_access_token: str = ""

    # DB
    database_url: str = "postgresql+psycopg2://dkanalytics:dkanalytics@db:5432/dkanalytics"

    # Auth
    auth_login: str = "maxim"
    auth_password_hash: str = ""
    session_secret: str = "change-me"

    # Schedule
    tz: str = "Europe/Moscow"
    collect_interval_min: int = 10
    collect_hours: str = "7-22"

    # Pipelines / statuses
    pipeline_sales: int = 10694702
    pipeline_repeat: int = 10697570
    status_won: int = 142
    status_lost: int = 143
    status_new: int = 84270034
    status_invoice: int = 84291534
    status_noanswer: int = 84313150
    status_waitlist: int = 84291530

    run_mode: str = "api"

    # Уволенные / служебные пользователи, которых не показываем в разрезе менеджеров.
    # Список id через запятую (env EXCLUDED_USER_IDS).
    # По умолчанию — уволенная Алина (8547745). Максим Дейкало и прочие служебные —
    # дописать их id через EXCLUDED_USER_IDS (id берётся из discover_repeat_ids.py).
    excluded_user_ids: str = "8547745"

    @property
    def excluded_ids(self) -> set[str]:
        return {x.strip() for x in self.excluded_user_ids.split(",") if x.strip()}


settings = Settings()
