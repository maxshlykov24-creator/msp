"""Номера из чатов бота → карточка контакта amo в формате +7.

Сделку не создаёт. Берёт lead_id из state и телефон из истории.
Запуск на проде:
  .venv/bin/python tools/backfill_amo_phones.py
  .venv/bin/python tools/backfill_amo_phones.py --apply
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import amo_client  # noqa: E402
from bot import nudge, store  # noqa: E402


def _contact_id(lead: dict, fallback: object) -> int | None:
    contacts = (lead.get("_embedded") or {}).get("contacts") or []
    if contacts and contacts[0].get("id"):
        return int(contacts[0]["id"])
    if fallback:
        try:
            return int(fallback)
        except (TypeError, ValueError):
            return None
    return None


def plan() -> list[dict]:
    rows: list[dict] = []
    for chat_id in store.all_chat_ids():
        doc = store.load_doc(chat_id)
        crmd = dict(doc.get("crm") or {})
        lead_id = crmd.get("lead_id")
        if not lead_id:
            continue
        phone = nudge.extract_phone_from_history(list(doc.get("messages") or []))
        if not phone:
            snap = ((crmd.get("alert") or {}).get("snap") or {})
            phone = str(snap.get("phone") or "").strip()
        formatted = amo_client.amo_phone(phone)
        if not formatted:
            continue
        rows.append(
            {
                "chat_id": str(chat_id),
                "lead_id": int(lead_id),
                "contact_id": crmd.get("contact_id"),
                "phone": formatted,
                "raw": phone,
            }
        )
    return rows


def apply_one(row: dict, *, write: bool) -> str:
    lead = amo_client.get_lead(int(row["lead_id"]))
    contact_id = _contact_id(lead, row.get("contact_id"))
    phone = row["phone"]
    if contact_id:
        contact = amo_client.get_contact(int(contact_id))
        if amo_client.contact_has_phone(contact, phone):
            return "есть"
        if write:
            amo_client.set_contact_phone(int(contact_id), phone)
        return "патч"
    if not write:
        return "нет контакта"
    contact_id = amo_client.create_contact("Клиент DIVO", phone)
    amo_client.link_contact(int(row["lead_id"]), int(contact_id))
    return "создал"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    rows = plan()
    print("чатов с номером и сделкой: %d" % len(rows))
    patched = skipped = created = errors = 0
    for row in rows:
        try:
            status = apply_one(row, write=args.apply)
        except amo_client.AmoError as exc:
            errors += 1
            print("  ошибка %s %s %s" % (row["lead_id"], row["phone"], exc))
            time.sleep(0.3)
            continue
        print(
            "  %s сделка %s %s %s"
            % (status, row["lead_id"], row["phone"], row["chat_id"][:28])
        )
        if status == "патч":
            patched += 1
        elif status == "создал":
            created += 1
        else:
            skipped += 1
        time.sleep(0.2)
    print(
        "итог: патч %d, уже были %d, создал %d, ошибок %d%s"
        % (
            patched,
            skipped,
            created,
            errors,
            "" if args.apply else " (прогон, без записи)",
        )
    )
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
