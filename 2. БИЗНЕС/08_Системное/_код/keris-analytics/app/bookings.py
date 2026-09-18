"""Read-only выборка визитов из Postgres keris-server."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from urllib.parse import urlparse, urlunparse

from app.config import settings

log = logging.getLogger("bookings")

CLOSED = {"cancelled", "no_show"}


def _dsn(url: str) -> str:
    """postgresql+psycopg://… → postgresql://… для psycopg.connect."""
    raw = (url or "").strip()
    if raw.startswith("postgresql+"):
        parsed = urlparse(raw)
        return urlunparse(parsed._replace(scheme="postgresql"))
    return raw


def is_visit(status: str, starts_at: datetime, now: datetime) -> bool:
    return status not in CLOSED and starts_at <= now


def fetch_bookings() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Записи, мастера, смены. Нет DATABASE_URL — пустые списки, питомник всё равно собирается."""
    if not settings.database_url:
        log.warning("DATABASE_URL пуст — груминг в срезе будет пустым")
        return [], [], []
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError:
        log.error("psycopg не установлен")
        return [], [], []

    dsn = _dsn(settings.database_url)
    with psycopg.connect(dsn) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT id, owner_phone, master_id, starts_at, ends_at, price, status, created_at
                FROM bookings
                """
            )
            bookings = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT id, name, active FROM masters")
            masters = [dict(r) for r in cur.fetchall()]
            try:
                cur.execute(
                    "SELECT master_id, date_iso, starts_at, ends_at FROM master_shifts"
                )
                shifts = [dict(r) for r in cur.fetchall()]
            except Exception:
                log.warning("master_shifts недоступна — загрузку не считаем")
                conn.rollback()
                shifts = []
            closed_days: set[str] = set()
            try:
                cur.execute("SELECT date_iso FROM salon_closures")
                closed_days = {str(r["date_iso"]) for r in cur.fetchall()}
            except Exception:
                conn.rollback()
    if closed_days:
        shifts = [s for s in shifts if str(s.get("date_iso") or "") not in closed_days]
    return bookings, masters, shifts
