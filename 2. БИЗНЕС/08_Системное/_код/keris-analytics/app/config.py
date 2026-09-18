from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    amocrm_base: str = "https://kerisclub.amocrm.ru"
    amocrm_token: str = ""
    amocrm_account_id: int = 33117150
    amocrm_max_rps: float = 4.0

    pipeline_sales: int = 11036674
    pipeline_puppies: int = 11036834
    pipeline_installment: int = 11036838

    status_new: int = 86717266
    status_in_work: int = 86717270
    status_wait_litter: int = 86717274
    status_puppy_picked: int = 86717278
    status_booked: int = 86718354
    status_docs: int = 86718358
    status_sold: int = 142
    status_lost: int = 143

    field_source: int = 1820835
    field_next_pay: int = 1820847

    database_url: str = ""

    auth_login: str = "karina"
    auth_password_hash: str = ""
    session_secret: str = "change-me"
    auth_cookie_secure: bool = True
    auth_cookie_path: str = "/"

    tz: str = "Europe/Moscow"
    collect_interval_min: int = 15
    collect_on_start: bool = True
    stale_after_hours: int = 2

    data_dir: str = "./data"
    run_scheduler: bool = True
    sleeping_days: int = 60


settings = Settings()

TEST_NAME_MARKERS = ("[ТЕСТ", "ТЕСТ бота")
