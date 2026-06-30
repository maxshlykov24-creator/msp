"""
Перенос документов OLD → NEW за конкретный день (moment) с полными полями:
name, attributes, salesChannel, owner, group, agent, shipmentAddress (адрес доставки), связи demand/paymentin.

  python3 catchup_today.py --dry --date 2026-06-03
  python3 catchup_today.py --date 2026-06-03
  python3 catchup_today.py --date 2026-06-03 --only customerorder
  python3 catchup_today.py --date 2026-06-03 --link-payments
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from migrate import (
    BASE,
    Migrator,
    entity_type_from_href,
    log,
    meta,
    uuid_from_href,
)

SCRIPT_DIR = Path(__file__).parent
REPORT_FILE = SCRIPT_DIR / "catchup_today_report.json"
LOG_FILE = SCRIPT_DIR / "catchup_today.log"

GROUP_NAME = "Головной офис"
RESP_ATTR = "Ответственный"

TODAY_DOC_ORDER: list[tuple[str, bool]] = [
    ("supply", True),
    ("customerorder", True),
    ("demand", True),
    ("invoiceout", True),
    ("paymentin", False),
    ("paymentout", False),
    ("cashin", False),
    ("cashout", False),
    ("move", True),
    ("loss", True),
]

file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
if not any(isinstance(h, logging.FileHandler) for h in log.handlers):
    log.addHandler(file_handler)


class TodayCatchup(Migrator):
    def __init__(self):
        super().__init__(catchup=True)
        self._sales_channels: dict[str, dict] | None = None
        self._group_meta: dict | None = None
        self._preferred_owners: dict[str, dict] | None = None
        self._login_by_href: dict[str, bool] | None = None
        self._emp_cache: dict[str, dict | None] = {}
        self._today_old_uuids: dict[str, set[str]] = {}

    def _moment_range(self, date_str: str) -> tuple[str, str]:
        return f"{date_str} 00:00:00", f"{date_str} 23:59:59"

    def _load_sales_channels(self) -> dict[str, dict]:
        if self._sales_channels is not None:
            return self._sales_channels
        rows = self.dst.get_all("entity/saleschannel")
        self._sales_channels = {r["name"]: r["meta"] for r in rows if r.get("name")}
        return self._sales_channels

    def _load_group_meta(self) -> dict:
        if self._group_meta is not None:
            return self._group_meta
        for g in self.dst.get_all("entity/group"):
            if g.get("name") == GROUP_NAME:
                self._group_meta = g["meta"]
                return self._group_meta
        raise RuntimeError(f"Группа {GROUP_NAME!r} не найдена в NEW")

    def _load_owner_helpers(self) -> None:
        if self._preferred_owners is not None:
            return
        preferred: dict[str, tuple[dict, bool]] = {}
        login_by_href: dict[str, bool] = {}
        for emp in self.dst.get_all("entity/employee"):
            href = emp["meta"]["href"]
            has_login = bool(emp.get("uid"))
            login_by_href[href] = has_login
            name = emp.get("name", "")
            if name not in preferred or (has_login and not preferred[name][1]):
                preferred[name] = (emp["meta"], has_login)
        self._preferred_owners = {n: m for n, (m, _) in preferred.items()}
        self._login_by_href = login_by_href

    def _resolve_owner_meta(self, responsible: dict) -> dict | None:
        href = responsible.get("meta", {}).get("href", "")
        if not href:
            return None
        cache_key = href
        if cache_key in self._emp_cache:
            return self._emp_cache[cache_key]

        old_emp_uuid = uuid_from_href(href)
        new_emp_uuid = self.umap.get("employee", old_emp_uuid)
        if not new_emp_uuid:
            self._emp_cache[cache_key] = None
            return None

        try:
            emp = self.dst.get(f"entity/employee/{new_emp_uuid}")
        except Exception:
            self._emp_cache[cache_key] = None
            return None

        emp_meta = emp.get("meta") or {}
        if emp.get("uid"):
            self._emp_cache[cache_key] = emp_meta
        elif emp.get("name") in (self._preferred_owners or {}):
            pref = self._preferred_owners[emp.get("name", "")]
            if self._login_by_href.get(pref.get("href", ""), False):
                self._emp_cache[cache_key] = pref
            else:
                self._emp_cache[cache_key] = None
        else:
            self._emp_cache[cache_key] = None
        return self._emp_cache[cache_key]

    def _employee_meta_for_old_href(self, emp_href: str) -> dict | None:
        resp = {"meta": {"href": emp_href}}
        if (resp.get("meta") or {}).get("href"):
            return self._resolve_owner_meta(resp)
        return None

    def _apply_responsible(self, full: dict, doc_type: str, body: dict) -> None:
        """Атрибут «Ответственный» + owner из OLD (employee через uuid_map)."""
        resp = self._get_responsible_from_doc(full)
        if not resp:
            return
        resp_href = (resp.get("meta") or {}).get("href", "")
        if not resp_href:
            return
        emp_meta = self._employee_meta_for_old_href(resp_href)
        if not emp_meta:
            return

        try:
            rows = self.dst.get(f"entity/{doc_type}/metadata/attributes").get("rows", [])
        except Exception:
            rows = []
        attr_href = None
        for a in rows:
            if a.get("name") == RESP_ATTR:
                attr_href = a["meta"]["href"]
                break
        if attr_href:
            entry = {
                "meta": {
                    "href": attr_href,
                    "type": "attributemetadata",
                    "mediaType": "application/json",
                },
                "value": {"meta": emp_meta},
            }
            attrs = [
                a for a in (body.get("attributes") or [])
                if a.get("name") != RESP_ATTR and (a.get("meta") or {}).get("href") != attr_href
            ]
            body["attributes"] = attrs + [entry]

        om = self._resolve_owner_meta(resp)
        if om:
            body["owner"] = {"meta": om}

    def _get_responsible_from_doc(self, doc: dict) -> dict | None:
        for attr in doc.get("attributes", []):
            if attr.get("name") != RESP_ATTR:
                continue
            val = attr.get("value")
            if isinstance(val, dict) and (val.get("meta") or {}).get("href"):
                return val
        owner = doc.get("owner") or {}
        if owner.get("meta", {}).get("href"):
            return owner
        return None

    def _ensure_counterparty(self, old_cp_uuid: str) -> str | None:
        if self.umap.has("counterparty", old_cp_uuid):
            return self.umap.get("counterparty", old_cp_uuid)
        try:
            cp = self.src.get(f"entity/counterparty/{old_cp_uuid}")
        except Exception as e:
            log.warning("  CP get old %s: %s", old_cp_uuid[:8], str(e)[:80])
            return None
        body = self._copy_fields(cp, [
            "name", "companyType", "inn", "kpp", "ogrn", "ogrnip",
            "legalTitle", "legalAddress", "actualAddress", "email", "phone",
            "description", "code",
        ])
        body["externalCode"] = old_cp_uuid
        if not body.get("name") or body.get("name") == "?":
            log.warning("  CP old %s: пустое имя, пропуск создания", old_cp_uuid[:8])
            return None
        try:
            r = self.dst.post("entity/counterparty", body)
            new_uuid = uuid_from_href(r["meta"]["href"])
            self.umap.set("counterparty", old_cp_uuid, new_uuid, save=False)
            log.info("  + CP %s → %s", body.get("name"), new_uuid[:8])
            return new_uuid
        except Exception as e:
            log.warning("  ✗ POST CP %s: %s", body.get("name", "?"), str(e)[:200])
            return None

    def _ensure_agents_in_doc(self, full: dict) -> None:
        agent_href = (full.get("agent") or {}).get("meta", {}).get("href", "")
        if not agent_href:
            return
        if entity_type_from_href(agent_href) == "counterparty":
            self._ensure_counterparty(uuid_from_href(agent_href))

    def _extend_body(self, full: dict, doc_type: str, body: dict) -> dict:
        channels = self._load_sales_channels()
        group_meta = self._load_group_meta()
        self._load_owner_helpers()

        sc = full.get("salesChannel") or {}
        sc_name = sc.get("name")
        if sc_name and sc_name in channels:
            body["salesChannel"] = {"meta": channels[sc_name]}

        body["group"] = {"meta": group_meta}

        self._apply_responsible(full, doc_type, body)

        if doc_type == "demand":
            co_href = (full.get("customerOrder") or {}).get("meta", {}).get("href", "")
            if co_href:
                new_co = self.umap.get("customerorder", uuid_from_href(co_href))
                if new_co:
                    body["customerOrder"] = meta(
                        f"{BASE}/entity/customerorder/{new_co}", "customerorder"
                    )

        if doc_type == "paymentin":
            body["incomingNumber"] = full.get("incomingNumber", "") or ""
            if full.get("sum") is not None:
                body["sum"] = full["sum"]
            ops = full.get("operations") or []
            if isinstance(ops, dict):
                ops = ops.get("rows", [])
            new_ops = []
            for op in ops:
                op_href = op.get("meta", {}).get("href", "")
                op_type = entity_type_from_href(op_href)
                op_old = uuid_from_href(op_href)
                op_new = self.umap.get(op_type, op_old)
                if not op_new:
                    continue
                entry = {
                    "meta": {
                        "href": f"{BASE}/entity/{op_type}/{op_new}",
                        "type": op_type,
                        "mediaType": "application/json",
                    }
                }
                if op.get("linkedSum") is not None:
                    entry["linkedSum"] = op["linkedSum"]
                new_ops.append(entry)
            if new_ops:
                body["operations"] = new_ops

        state_href = (full.get("state") or {}).get("meta", {}).get("href", "")
        if state_href:
            state_key = f"state_{doc_type}"
            new_state = self.umap.get(state_key, uuid_from_href(state_href))
            if new_state:
                body["state"] = meta(f"{BASE}/entity/state/{new_state}", "state")

        return {k: v for k, v in body.items() if v is not None}

    def _fetch_old_today(self, doc_type: str, date_str: str) -> list[dict]:
        moment_from, moment_to = self._moment_range(date_str)
        filt = f"moment>={moment_from};moment<={moment_to}"
        return self.src.get_all(f"entity/{doc_type}", params={"filter": filt})

    def _get_full_old(self, doc_type: str, old_uuid: str, has_positions: bool) -> dict:
        expand_parts = ["attributes", "project", "contract", "owner", "group", "salesChannel"]
        if has_positions:
            expand_parts.insert(0, "positions")
        if doc_type == "paymentin":
            expand_parts.append("operations")
        if doc_type == "demand":
            expand_parts.append("customerOrder")
        expand = ",".join(expand_parts)
        return self.src.get(f"entity/{doc_type}/{old_uuid}", params={"expand": expand})

    def migrate_today(
        self,
        date_str: str,
        dry: bool = False,
        only_type: str | None = None,
    ) -> dict:
        report: dict = {"date": date_str, "dry": dry, "types": {}}
        order = TODAY_DOC_ORDER
        if only_type:
            order = [(t, hp) for t, hp in TODAY_DOC_ORDER if t == only_type]

        for doc_type, has_positions in order:
            log.info("\n%s\n%s %s\n%s", "=" * 55, doc_type, date_str, "=" * 55)
            type_stat = {"created": 0, "patched": 0, "skipped": 0, "errors": 0, "items": []}
            old_docs = self._fetch_old_today(doc_type, date_str)
            log.info("  OLD за день: %s", len(old_docs))
            self._today_old_uuids[doc_type] = set()

            try:
                old_attrs = self.src.get(f"entity/{doc_type}/metadata/attributes").get("rows", [])
            except Exception:
                old_attrs = []
            attrs_index = {uuid_from_href(a["meta"]["href"]): a for a in old_attrs}

            for doc in old_docs:
                old_uuid = uuid_from_href(doc["meta"]["href"])
                name = doc.get("name", "?")
                self._today_old_uuids[doc_type].add(old_uuid)

                try:
                    full = self._get_full_old(doc_type, old_uuid, has_positions)
                except Exception as e:
                    log.warning("  ✗ GET %s/%s: %s", doc_type, name, str(e)[:150])
                    type_stat["errors"] += 1
                    continue

                self._ensure_agents_in_doc(full)
                body = self._build_doc_body(full, doc_type, has_positions, attrs_index)
                if body is None:
                    type_stat["errors"] += 1
                    type_stat["items"].append({"name": name, "action": "error", "reason": "no body"})
                    continue
                body = self._extend_body(full, doc_type, body)

                new_uuid = self.umap.get(doc_type, old_uuid)
                if not new_uuid and doc.get("name"):
                    new_uuid = self._find_existing_doc(doc_type, doc["name"])
                    if new_uuid:
                        self.umap.set(doc_type, old_uuid, new_uuid, save=False)

                if dry:
                    action = "patch" if new_uuid else "create"
                    log.info("  [DRY] %s %s/%s", action, doc_type, name)
                    type_stat["patched" if new_uuid else "created"] += 1
                    type_stat["items"].append({"name": name, "action": action})
                    continue

                if new_uuid:
                    put_body = dict(body)
                    put_body.pop("name", None)
                    if has_positions and put_body.get("positions"):
                        del put_body["positions"]
                    try:
                        self.dst.put(f"entity/{doc_type}/{new_uuid}", put_body)
                        type_stat["patched"] += 1
                        log.info("  ↻ PUT %s/%s", doc_type, name)
                        type_stat["items"].append({"name": name, "action": "patched"})
                    except Exception as e:
                        if "3006" in str(e) and "name" in put_body:
                            put_body.pop("name", None)
                            try:
                                self.dst.put(f"entity/{doc_type}/{new_uuid}", put_body)
                                type_stat["patched"] += 1
                                log.info("  ↻ PUT (no name) %s/%s", doc_type, name)
                                type_stat["items"].append({"name": name, "action": "patched"})
                                continue
                            except Exception as e2:
                                log.warning("  ✗ PUT %s/%s: %s", doc_type, name, str(e2)[:250])
                                type_stat["errors"] += 1
                                continue
                        log.warning("  ✗ PUT %s/%s: %s", doc_type, name, str(e)[:250])
                        type_stat["errors"] += 1
                else:
                    try:
                        r = self.dst.post(f"entity/{doc_type}", body)
                        new_uuid = uuid_from_href(r["meta"]["href"])
                        self.umap.set(doc_type, old_uuid, new_uuid, save=False)
                        type_stat["created"] += 1
                        log.info("  ✓ POST %s/%s → %s", doc_type, name, new_uuid[:8])
                        type_stat["items"].append({"name": name, "action": "created"})
                    except Exception as e:
                        err_str = str(e)
                        if "3006" in err_str and doc.get("name"):
                            existing = self._find_existing_doc(doc_type, doc["name"])
                            if existing:
                                self.umap.set(doc_type, old_uuid, existing, save=False)
                                try:
                                    put_body = dict(body)
                                    put_body.pop("name", None)
                                    if has_positions and put_body.get("positions"):
                                        del put_body["positions"]
                                    self.dst.put(f"entity/{doc_type}/{existing}", put_body)
                                    type_stat["patched"] += 1
                                    log.info("  ↻ duplicate→PUT %s/%s", doc_type, name)
                                except Exception as e2:
                                    log.warning("  ✗ PUT dup %s/%s: %s", doc_type, name, str(e2)[:200])
                                    type_stat["errors"] += 1
                                continue
                        log.warning("  ✗ POST %s/%s: %s", doc_type, name, err_str[:250])
                        type_stat["errors"] += 1

            self.umap.save()
            report["types"][doc_type] = type_stat
            log.info(
                "  ИТОГ %s: created=%s patched=%s errors=%s",
                doc_type,
                type_stat["created"],
                type_stat["patched"],
                type_stat["errors"],
            )

        REPORT_FILE.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("Отчёт: %s", REPORT_FILE)
        return report

    def link_payments_today(self, date_str: str, dry: bool = False) -> int:
        moment_from, moment_to = self._moment_range(date_str)
        old_pis = self.src.get_all(
            "entity/paymentin",
            params={"filter": f"moment>={moment_from};moment<={moment_to}"},
        )
        linked = 0
        for old_doc in old_pis:
            old_uuid = uuid_from_href(old_doc["meta"]["href"])
            new_uuid = self.umap.get("paymentin", old_uuid)
            if not new_uuid:
                continue
            try:
                full = self.src.get(
                    f"entity/paymentin/{old_uuid}",
                    params={"expand": "operations"},
                )
            except Exception as e:
                log.warning("  link get %s: %s", old_uuid[:8], str(e)[:80])
                continue
            ops = full.get("operations") or []
            if isinstance(ops, dict):
                ops = ops.get("rows", [])
            if not ops:
                continue
            new_ops = []
            for op in ops:
                op_href = op.get("meta", {}).get("href", "")
                op_type = entity_type_from_href(op_href)
                op_new = self.umap.get(op_type, uuid_from_href(op_href))
                if not op_new:
                    continue
                entry = {
                    "meta": {
                        "href": f"{BASE}/entity/{op_type}/{op_new}",
                        "type": op_type,
                        "mediaType": "application/json",
                    }
                }
                if op.get("linkedSum") is not None:
                    entry["linkedSum"] = op["linkedSum"]
                new_ops.append(entry)
            if not new_ops:
                continue
            if dry:
                linked += 1
                continue
            try:
                self.dst.put(f"entity/paymentin/{new_uuid}", {"operations": new_ops})
                linked += 1
            except Exception as e:
                log.warning("  ✗ link %s: %s", full.get("name", "?"), str(e)[:150])
        log.info("Связано paymentin за %s: %s", date_str, linked)
        return linked


def main() -> None:
    parser = argparse.ArgumentParser(description="Catchup документов за день (полный body)")
    parser.add_argument("--date", default="2026-06-03", help="YYYY-MM-DD")
    parser.add_argument("--dry", action="store_true")
    parser.add_argument("--only", choices=[t for t, _ in TODAY_DOC_ORDER])
    parser.add_argument("--link-payments", action="store_true", help="Только связи paymentin за день")
    args = parser.parse_args()

    mig = TodayCatchup()
    if args.link_payments:
        mig.link_payments_today(args.date, dry=args.dry)
        return

    mig.migrate_today(args.date, dry=args.dry, only_type=args.only)
    if not args.dry:
        mig.link_payments_today(args.date, dry=False)


if __name__ == "__main__":
    main()
