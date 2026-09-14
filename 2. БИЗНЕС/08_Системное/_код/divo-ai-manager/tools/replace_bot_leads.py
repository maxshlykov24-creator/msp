"""Разовая замена дублей AI-менеджера на сделки виджета из «Техническое».

Дубль закрывается как спам. Виджет принимается из неразобранного
и ставится в Продажи. Этап: Новая заявка, если дубль ещё не брали;
Контакт установлен, если менеджер уже нажал кнопку.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import amo_client  # noqa: E402


def _contact_id(lead: dict) -> int | None:
    contacts = (lead.get("_embedded") or {}).get("contacts") or []
    if not contacts:
        return None
    cid = contacts[0].get("id")
    return int(cid) if cid else None


def _copy_notes(src_id: int, dst_id: int) -> int:
    code, data = amo_client.request(
        "/api/v4/leads/%s/notes" % int(src_id),
        {"limit": "50"},
    )
    if code >= 400 or not data:
        return 0
    n = 0
    for note in amo_client.items(data, "notes"):
        if note.get("note_type") != "common":
            continue
        text = ((note.get("params") or {}).get("text") or "").strip()
        if not text:
            continue
        amo_client.add_note(dst_id, text)
        n += 1
    return n


def main() -> None:
    # Premium Auto: дубль ещё на «Новой заявке».
    # Михаил: Никита уже взял, сохраняем «Контакт установлен».
    pairs = (
        (45678659, 45628247, "", amo_client.STATUS_NEW, amo_client.NIKITA_USER_ID),
        (45679397, 45646355, "79502292544", amo_client.STATUS_IN_WORK, amo_client.NIKITA_USER_ID),
    )
    for bot_id, widget_id, phone, status_id, responsible in pairs:
        widget = amo_client.get_lead(widget_id)
        print(
            "виджет %s pipe=%s st=%s %s"
            % (
                widget_id,
                widget.get("pipeline_id"),
                widget.get("status_id"),
                widget.get("name"),
            )
        )
        moved = amo_client.move_lead_to_sales(
            widget_id,
            status_id=status_id,
            responsible_user_id=responsible,
        )
        cid = _contact_id(moved)
        if phone and cid:
            amo_client.set_contact_phone(cid, phone)
            print("  телефон на контакт %s" % cid)
        copied = _copy_notes(bot_id, widget_id)
        amo_client.add_note(
            widget_id,
            "AI-менеджер DIVO. Сделка виджета принята из Технического, "
            "дубль %s закрыт." % bot_id,
        )
        print(
            "  → продажи pipe=%s st=%s notes+%s %s"
            % (
                moved.get("pipeline_id"),
                moved.get("status_id"),
                copied,
                amo_client.lead_url(widget_id),
            )
        )
        amo_client.close_lead_spam(
            bot_id,
            "Дубль AI-менеджера. Рабочая сделка: %s"
            % amo_client.lead_url(widget_id),
        )
        print("  дубль %s закрыт как спам" % bot_id)


if __name__ == "__main__":
    main()
