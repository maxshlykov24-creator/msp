"""Распределение ответственных.

- Новые не-телефонные лиды → DEFAULT_SALES_OWNER_ID (с 19.08.2026 Александра).
- Ответственный ОТКРЫТОЙ сделки не меняется никогда.
- Нет открытых, есть закрытые → ответственный из свежей закрытой (по closed_at);
  если тот пользователь неактивен, удалён, из клиентского отдела или уволен
  (`DEPARTED_OWNER_IDS`) → дефолтный.
- Нет истории → дефолтный.

Про уволенных отдельно: их не деактивируют в Kommo, чтобы не потерять историю,
поэтому «активен» о работе в команде ничего не говорит. Без явного списка
ушедших вернувшийся клиент наследует карточку уволенного, и его никто не видит.

Дефолт обязан быть работающим человеком: он же и наследник, и fallback. Пока в
нём стояла уволенная Илона, отказ от наследования вёл обратно в неё, и новые
заявки копились у человека, которого в команде нет.
Логика звонков — в отдельном плане телефонии, здесь не строится.
"""
from __future__ import annotations

import logging
import time

from app.actions import Ctx, log_decision
from app.config import CLOSED_STATUS_IDS, settings

log = logging.getLogger("assignment")

_active_cache: dict[str, object] = {"ids": None, "ts": 0.0}
_ACTIVE_TTL = 600.0


def active_user_ids(client) -> set[int]:
    now = time.monotonic()
    if _active_cache["ids"] is not None and now - float(_active_cache["ts"]) < _ACTIVE_TTL:
        return _active_cache["ids"]  # type: ignore[return-value]
    ids: set[int] = set()
    try:
        for u in client.users():
            uid = u.get("id")
            if uid and u.get("rights", {}).get("is_active", True) is not False:
                ids.add(int(uid))
    except Exception as exc:
        log.warning("users fetch failed: %s", exc)
        return set()
    _active_cache["ids"] = ids
    _active_cache["ts"] = now
    return ids


def _is_open(lead: dict) -> bool:
    return lead.get("closed_at") in (None, 0) and lead.get("status_id") not in CLOSED_STATUS_IDS


def sells(client, user_id: int | None) -> bool:
    """Может ли этот пользователь вести сделку продаж.

    Признак один — добавочный в карте телефонии: продавец обязан принимать
    звонки. Карта уже правится при смене менеджеров, поэтому отдельного списка
    «не продавцов» не нужно, и он не разъедется с реальностью.

    Отсюда выпадают: Павел (владелец аккаунта, звонки не принимает, сделки
    создаёт его сценарий), Полина (клиентский отдел), уволенные и неактивные.
    Карточка на таком человеке — карточка, которой продавец не видит.
    """
    if not user_id:
        return False
    uid = int(user_id)
    if uid in settings.client_dept_owner_id_set or uid in settings.departed_owner_id_set:
        return False
    if uid not in settings.user_to_ext:
        return False
    active = active_user_ids(client)
    return not active or uid in active


def resolve_owner_for_new(ctx: Ctx, leads: list[dict]) -> int:
    """Ответственный для новой сделки контакта (кроме самой новой в списке)."""
    closed = sorted(
        [l for l in leads if not _is_open(l) and l.get("closed_at")],
        key=lambda l: l.get("closed_at") or 0,
        reverse=True,
    )
    if closed and sells(ctx.client, closed[0].get("responsible_user_id")):
        return int(closed[0]["responsible_user_id"])
    return settings.default_sales_owner_id


def holds_sales_card(client, user_id: int | None) -> bool:
    """Можно ли оставить открытую сделку продаж на этом человеке.

    Шире, чем `sells()`: Полина звонки продаж не принимает и новых лидов не
    получает, но её собственные старые сделки в воронке продаж — её работа, а не
    мусор. Автоматика такие карточки не отбирает, это решение владельца.
    Отбираем только у того, кого в команде фактически нет: уволенный, неактивный
    или технический пользователь без добавочного (Павел).
    """
    if not user_id:
        return False
    uid = int(user_id)
    if uid in settings.client_dept_owner_id_set:
        return True
    return sells(client, uid)


def sweep_non_sales_owners(ctx: Ctx, limit: int = 50) -> list[int]:
    """Открытые сделки продаж, осевшие на том, кого в команде нет.

    Нужен потому, что `assign_new_lead` ловит только событие создания. Сделку
    может завести сторонний сценарий, перенести человек руками или уронить в
    воронку продаж из другой — и она молча повиснет на Павле, где продавец её не
    видит. Именно так выглядела жалоба «если лид был не на мне, я не вижу звонок».
    """
    moved: list[int] = []
    # воронку проверяем в коде, а не только фильтром: 19.08 Kommo молча вернул
    # сделки всех воронок, и подметание сняло 44 карточки «Сборки» с Полины
    params = {"filter[pipeline_id]": settings.pipeline_id, "limit": 250}
    for lead in ctx.client.paginate("/leads", "leads", params=params):
        if len(moved) >= limit:
            break
        if int(lead.get("pipeline_id") or 0) != settings.pipeline_id:
            continue
        if not _is_open(lead):
            continue
        owner = lead.get("responsible_user_id")
        if holds_sales_card(ctx.client, owner):
            continue
        lead_id = int(lead["id"])
        if not ctx.flags.assignment or ctx.shadow:
            log_decision(ctx, "assign.sweep_skip", lead=lead_id, owner=owner,
                         shadow=ctx.shadow)
            continue
        ctx.client.update_lead(lead_id, {
            "responsible_user_id": settings.default_sales_owner_id})
        sync_contact_owner(ctx, lead, settings.default_sales_owner_id)
        log_decision(ctx, "assign.sweep_applied", lead=lead_id, prev=owner,
                     owner=settings.default_sales_owner_id)
        moved.append(lead_id)
    return moved


def _lead_contact_ids(lead: dict) -> list[int]:
    return [int(c["id"]) for c in ((lead.get("_embedded") or {}).get("contacts") or [])
            if c.get("id")]


def sync_contact_owner(ctx: Ctx, lead: dict, owner: int) -> None:
    """Контакт сделки — тот же ответственный, что и сделка.

    Иначе менеджер с правом «только свои» не видит телефон/почту в карточке:
    Make и хаб создают контакт под токеном Павла, а сделку потом отдают Илоне.
    «Сборку» сюда не пускаем — у контакта может висеть живая сделка продаж.
    """
    if not owner:
        return
    for cid in _lead_contact_ids(lead):
        contact = ctx.client.get_contact(int(cid), with_=None)
        if not contact:
            continue
        current = contact.get("responsible_user_id")
        if current and int(current) == int(owner):
            continue
        ctx.client.update_contact(int(cid), {"responsible_user_id": int(owner)})
        log_decision(ctx, "assign.contact_synced", contact=cid, owner=int(owner),
                     prev=current, lead=lead.get("id"))


def assign_new_lead(ctx: Ctx, lead: dict, sibling_leads: list[dict]) -> int:
    """Назначает ответственного новому лиду. Открытые сделки не трогаем."""
    lead_id = int(lead["id"])
    # Правило про Илону — про воронку продаж. В «Сборке» и прочих воронках
    # ответственного ставит свой процесс (проверено: Kommo сама назначает туда
    # клиентский отдел), перебивать его нельзя.
    if int(lead.get("pipeline_id") or 0) != settings.pipeline_id:
        current_owner = int(lead.get("responsible_user_id") or 0)
        log_decision(ctx, "assign.skip_funnel", lead=lead_id,
                     pipeline=lead.get("pipeline_id"), owner=current_owner)
        return current_owner
    owner = resolve_owner_for_new(ctx, [l for l in sibling_leads if int(l["id"]) != lead_id])
    current = lead.get("responsible_user_id")
    if not ctx.flags.assignment or ctx.shadow:
        log_decision(ctx, "assign.skip", lead=lead_id, owner=owner,
                     current=current, shadow=ctx.shadow)
        return owner
    if current and int(current) == owner:
        log_decision(ctx, "assign.noop", lead=lead_id, owner=owner)
    else:
        ctx.client.update_lead(lead_id, {"responsible_user_id": owner})
        log_decision(ctx, "assign.applied", lead=lead_id, owner=owner, prev=current)
    sync_contact_owner(ctx, lead, owner)
    return owner
