"""Поднимает локальный Postgres до импорта тестов и фиксирует DATABASE_URL."""

from __future__ import annotations

import os
from pathlib import Path

_PGDATA = Path(os.environ.get("MANSBAND_TEST_PGDATA", "/tmp/mansband-ct-pg"))
os.environ["DATABASE_URL"] = f"postgresql+psycopg2://postgres:@/bridge_test?host={_PGDATA.resolve()}"


def _ensure_postgres() -> None:
    if not _PGDATA.exists():
        _PGDATA.mkdir(parents=True)
    import pgserver

    server = pgserver.get_server(_PGDATA, cleanup_mode=None)
    server.ensure_postgres_running()
    exists = "bridge_test" in server.psql("SELECT datname FROM pg_database")
    if not exists:
        server.psql("CREATE DATABASE bridge_test")


_ensure_postgres()
