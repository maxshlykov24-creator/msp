import argparse
from datetime import datetime, timezone

from db import (
    get_client,
    init_db,
    insert_cabinet,
    insert_client,
    list_cabinets,
    list_clients,
    set_setting,
    update_client_org,
)
from net import MS_BASE, OZON_BASE, WB_BASE, env, ms_headers, ozon_headers, req, wb_headers


def ms_org_id(forced=None):
    if forced:
        return forced
    r = req("GET", MS_BASE + "/entity/organization?limit=10", headers=ms_headers())
    r.raise_for_status()
    rows = r.json().get("rows") or []
    for i, row in enumerate(rows):
        print("%s. org %s  %s" % (i + 1, row.get("id"), row.get("name")))
    if not rows:
        raise SystemExit("в МойСклад нет юрлиц")
    if len(rows) > 1:
        print("беру первое юрлицо, иначе передай --org-id")
    return rows[0]["id"]


def find_or_create_agent(name):
    r = req(
        "GET",
        MS_BASE + "/entity/counterparty",
        headers=ms_headers(),
        params={"filter": "name~%s" % name, "limit": 10},
    )
    r.raise_for_status()
    rows = r.json().get("rows") or []
    for row in rows:
        print("контрагент найден: %s  %s" % (row.get("id"), row.get("name")))
        if row.get("name") == name:
            return row["id"]
    if rows:
        print("точное имя не совпало, беру первый результат")
        return rows[0]["id"]
    print("контрагента нет, создаю %s" % name)
    created = req(
        "POST",
        MS_BASE + "/entity/counterparty",
        headers=ms_headers(),
        json={"name": name},
    )
    if created.status_code not in (200, 201):
        raise SystemExit("не создал контрагента: %s %s" % (created.status_code, created.text[:300]))
    data = created.json()
    print("контрагент создан: %s" % data.get("id"))
    return data["id"]


def check_wb(token):
    r = req("GET", WB_BASE + "/ping", headers=wb_headers(token))
    return r.status_code, (r.text or "")[:240]


def check_ozon(client_id, api_key):
    r = req(
        "POST",
        OZON_BASE + "/v3/product/list",
        headers=ozon_headers(client_id, api_key),
        json={"filter": {"visibility": "ALL"}, "limit": 1},
    )
    return r.status_code, (r.text or "")[:240]


def cmd_add_client(args):
    init_db()
    code = args.code.strip().upper()
    existing = get_client(code)
    if existing:
        print("клиент %s уже есть, id=%s" % (code, existing["id"]))
        return
    store_id = args.store_id or env("MS_STORE_ID")
    org_id = ms_org_id(args.org_id)
    agent_id = find_or_create_agent(args.name)
    cid = insert_client(code, args.name, agent_id, org_id, store_id)
    print("клиент записан id=%s code=%s agent=%s org=%s store=%s" % (cid, code, agent_id, org_id, store_id))
    print("повесь на контрагента группу Фулфилмент и заполни токены в карточке")


def cmd_add_cabinet(args):
    init_db()
    mp = args.marketplace.lower()
    if mp not in ("wb", "ozon"):
        raise SystemExit("marketplace: wb или ozon")
    client = get_client(args.client.strip().upper())
    if not client:
        raise SystemExit("сначала add-client --code %s" % args.client)
    if mp == "ozon" and not args.client_id_ext:
        raise SystemExit("для Ozon нужен --client-id-ext")
    if mp == "wb":
        status, body = check_wb(args.token)
        ok = status == 200
    else:
        status, body = check_ozon(args.client_id_ext, args.token)
        ok = status == 200
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    error = "" if ok else ("%s %s" % (status, body))
    cab_id = insert_cabinet(
        client["id"],
        mp,
        args.name or ("%s %s" % (mp, client["code"])),
        args.token,
        args.client_id_ext,
        1 if ok else 0,
        now if ok else None,
        error,
    )
    print(
        "кабинет id=%s %s active=%s status=%s %s"
        % (cab_id, mp, 1 if ok else 0, status, "ok" if ok else body)
    )


def cmd_set_org(args):
    init_db()
    code = args.client.strip().upper()
    client = get_client(code)
    if not client:
        raise SystemExit("нет клиента %s" % code)
    update_client_org(code, args.org_id)
    set_setting("MS_ORG_ID", args.org_id)
    print("клиент %s: ms_org_id=%s" % (code, args.org_id))


def cmd_ensure_attrs(_args):
    init_db()
    from ms import ensure_all_attrs, ensure_projects, ensure_services

    ensure_all_attrs()
    ensure_projects()
    ensure_services()


def cmd_sync_agents(_args):
    init_db()
    from agents_sync import run

    notes = run()
    for code, note in notes:
        print("%s  %s" % (code, note))


def cmd_list(_args):
    init_db()
    print("клиенты:")
    for row in list_clients():
        print(
            "  %s  %s  %s  agent=%s active=%s"
            % (row["id"], row["code"], row["name"], row["ms_counterparty_id"], row["active"])
        )
    print("кабинеты:")
    for row in list_cabinets():
        print(
            "  %s  %s  %s  %s  active=%s last_ok=%s err=%s"
            % (
                row["id"],
                row["client_code"],
                row["marketplace"],
                row["name"],
                row["active"],
                row["last_ok_at"],
                (row["last_error"] or "")[:80],
            )
        )


def main():
    p = argparse.ArgumentParser(prog="cli.py")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("add-client")
    c.add_argument("--code", required=True)
    c.add_argument("--name", required=True)
    c.add_argument("--org-id")
    c.add_argument("--store-id")
    c.set_defaults(func=cmd_add_client)

    b = sub.add_parser("add-cabinet")
    b.add_argument("--client", required=True)
    b.add_argument("--marketplace", required=True)
    b.add_argument("--token", required=True)
    b.add_argument("--client-id-ext")
    b.add_argument("--name")
    b.set_defaults(func=cmd_add_cabinet)

    o = sub.add_parser("set-org")
    o.add_argument("--client", required=True)
    o.add_argument("--org-id", required=True)
    o.set_defaults(func=cmd_set_org)

    a = sub.add_parser("ensure-attrs")
    a.set_defaults(func=cmd_ensure_attrs)

    s = sub.add_parser("sync-agents")
    s.set_defaults(func=cmd_sync_agents)

    l = sub.add_parser("list")
    l.set_defaults(func=cmd_list)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
