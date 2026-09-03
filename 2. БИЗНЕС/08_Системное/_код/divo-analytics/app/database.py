from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import settings

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_recycle=1800,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

Base = declarative_base()


def init_db() -> None:
    """Создать таблицы, если их ещё нет."""
    from app import models  # noqa: F401  (регистрация моделей)

    Base.metadata.create_all(bind=engine)
