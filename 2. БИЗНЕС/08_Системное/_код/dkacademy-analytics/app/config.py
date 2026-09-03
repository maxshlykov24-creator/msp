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

    # Канонический состав отдела. Если MANAGER_USER_IDS заполнен, в менеджерские
    # разрезы входят только эти id. EXCLUDED_USER_IDS всегда имеет приоритет.
    manager_user_ids: str = (
        "13611590,13611594,13793718,13803674,13860306,13961174"
    )
    excluded_user_ids: str = "8547745"

    @property
    def manager_ids(self) -> set[str]:
        return {x.strip() for x in self.manager_user_ids.split(",") if x.strip()}

    @property
    def excluded_ids(self) -> set[str]:
        return {x.strip() for x in self.excluded_user_ids.split(",") if x.strip()}

    def is_manager(self, user_id: int | str | None) -> bool:
        uid = str(user_id or "")
        if not uid or uid in self.excluded_ids:
            return False
        return not self.manager_ids or uid in self.manager_ids


settings = Settings()
