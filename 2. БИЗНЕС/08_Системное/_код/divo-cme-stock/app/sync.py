"""Один прогон: CME → фильтр → маппинг → страховка → листы Данные и Склад."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.config import settings
from app.cme_client import CmeAuthError, CmeClient, CmeError
from app.filter_cars import filter_stock, filter_warehouse
from app.map_row import dedup_sort, map_row
from app import notify, state
from app.sheets import sheets_service, write_dannye, write_warehouse

log = logging.getLogger("sync")


def _should_alert(st: dict) -> bool:
    if int(st.get("fail_streak") or 0) < 3:
        return False
    last = st.get("last_alert_at")
    if not last:
        return True
    try:
        prev = datetime.fromisoformat(last)
        if prev.tzinfo is None:
            prev = prev.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - prev).total_seconds() >= 3600
    except (TypeError, ValueError):
        return True


def run_once() -> str:
    st = state.load()
    client = None
    try:
        client = CmeClient()
        raw = client.iter_cars()
        if not raw:
            raise CmeError("API вернул 0 машин (пустой ответ)")
        filt = filter_stock(
            raw,
            mode=settings.cme_publish_mode,
            publish_field=settings.cme_publish_field or st.get("publish_field") or "",
            dealer_id=settings.cme_dealer_id,
        )
        if filt.reason:
            raise CmeError(filt.reason)
        rows = dedup_sort(map_row(c) for c in filt.kept)
        reason = state.guard_write(
            len(rows),
            st.get("last_ok_count"),
            settings.allow_empty,
            settings.drop_ratio,
        )
        if reason:
            raise CmeError(reason)

        svc = sheets_service()
        write_dannye(svc, rows)
        # Склад — справочный лист для бота, под страховку не попадает: пустой
        # склад это норма, а «Данные» уже записаны выше.
        try:
            stored = dedup_sort(
                map_row(c)
                for c in filter_warehouse(
                    raw, kept=filt.kept, dealer_id=settings.cme_dealer_id
                )
            )
            write_warehouse(svc, stored)
        except (OSError, CmeError) as exc:
            log.warning("склад не записан, «Данные» в порядке: %s", exc)
            stored = []
        st["last_ok_at"] = state.now_iso()
        st["last_ok_count"] = len(rows)
        st["fail_streak"] = 0
        st["last_error"] = ""
        if filt.publish_field:
            st["publish_field"] = filt.publish_field
        state.save(st)
        msg = (
            f"ok rows={len(rows)} warehouse={len(stored)} "
            f"raw={filt.stats.get('total')} "
            f"in={filt.stats.get('in_stock')} sale={filt.stats.get('onsale')} "
            f"pub_field={filt.publish_field}"
        )
        log.info(msg)
        return msg
    except (CmeAuthError, CmeError, OSError) as exc:
        st["fail_streak"] = int(st.get("fail_streak") or 0) + 1
        st["last_error"] = str(exc)
        state.save(st)
        log.error("sync skip (Данные не тронуты): %s", exc)
        if _should_alert(st):
            notify.send(
                "DIVO CME stock: 3+ неуспешных прогона подряд. "
                f"streak={st['fail_streak']}. Данные не перезаписывались.\n{exc}"
            )
            st["last_alert_at"] = state.now_iso()
            state.save(st)
        return f"skip: {exc}"
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    print(run_once())
