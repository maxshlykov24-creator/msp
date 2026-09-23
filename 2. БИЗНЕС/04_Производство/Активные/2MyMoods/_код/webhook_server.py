#!/usr/bin/env python3
"""Один вебхук amo на создание сделки и смену этапа.

Ссылка одна. Этап берётся из тела: pipeline_id и status_id.
Старые воронки отбрасываются до запроса в amo.
"""

from __future__ import annotations

import json
import re
import threading
import traceback
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import lib
import route_stock
import stage_plan
import wazzup_in

TZ = ZoneInfo("Europe/Moscow")
HOST = "127.0.0.1"
PORT = 8791
LEAD_KEY = re.compile(r"leads\[(status|add)\]\[(\d+)\]\[(id|status_id|pipeline_id)\]")
LOCKS: dict[int, threading.Lock] = {}
LOCKS_GUARD = threading.Lock()


def token() -> str:
    return (lib.load_env().get("AMO_WEBHOOK_TOKEN") or "").strip()


def parse_events(raw: bytes, content_type: str) -> list[dict]:
    text = raw.decode("utf-8", "replace")
    if "json" in content_type:
        try:
            body = json.loads(text or "{}")
        except json.JSONDecodeError:
            return []
        leads = body.get("leads") if isinstance(body, dict) else None
        if not isinstance(leads, dict):
            return []
        out = []
        for kind in ("status", "add"):
            for row in leads.get(kind) or []:
                if isinstance(row, dict) and row.get("id"):
                    out.append(row)
        return out
    qs = parse_qs(text, keep_blank_values=False)
    bucket: dict[tuple[str, str], dict] = {}
    for key, vals in qs.items():
        match = LEAD_KEY.fullmatch(key)
        if not match or not vals:
            continue
        kind, idx, field = match.groups()
        bucket.setdefault((kind, idx), {})[field] = vals[0]
    return list(bucket.values())


def lock_for(lead_id: int) -> threading.Lock:
    with LOCKS_GUARD:
        return LOCKS.setdefault(lead_id, threading.Lock())


def open_tasks(amo: lib.Amo, lead_id: int) -> list[dict]:
    st, body = amo.req(
        "GET",
        f"/api/v4/tasks?filter[entity_id]={lead_id}&filter[is_completed]=0&limit=50",
    )
    if not (200 <= st < 300):
        return []
    return (body.get("_embedded") or {}).get("tasks") or []


def ensure_tasks(amo: lib.Amo, lead: dict) -> None:
    moment = datetime.now(TZ)
    planned = []
    for text, due, who in stage_plan.jobs(lead["pipeline_id"], lead["status_id"], moment):
        rid = who or lead.get("responsible_user_id") or lib.USER_OKSANA
        if lead["status_id"] == lib.ST["sent"] and text.startswith("Проставить трек"):
            track = amo.cf(lead, lib.FIELD_TRACK) or amo.cf(lead, lib.FIELD_CDEK) or amo.cf(lead, lib.FIELD_TRACK_MS)
            if track:
                continue
        planned.append((text, due, rid))
    planned.sort(key=lambda row: row[1].timestamp())
    wanted = {(text, rid) for text, _, rid in planned}
    for task in open_tasks(amo, lead["id"]):
        key = (task.get("text") or "", task.get("responsible_user_id"))
        if key in wanted:
            continue
        amo.req("PATCH", f"/api/v4/tasks/{task['id']}", {
            "is_completed": True,
            "result": {"text": "этап сменился"},
        })
    existing = {(t.get("text") or "", t.get("responsible_user_id")) for t in open_tasks(amo, lead["id"])}
    for text, due, rid in planned:
        if (text, rid) in existing:
            continue
        payload = {
            "task_type_id": 1,
            "text": text,
            "complete_till": int(due.timestamp()),
            "entity_id": lead["id"],
            "entity_type": "leads",
            "responsible_user_id": rid,
        }
        st, _ = amo.req("POST", "/api/v4/tasks", [payload])
        print(f"  task {lead['id']} [{st}] {text} -> {due:%d.%m %H:%M} user {rid}", flush=True)


def handle_lead(lead_id: int) -> None:
    with lock_for(lead_id):
        amo = lib.Amo()
        st, lead = amo.req("GET", f"/api/v4/leads/{lead_id}")
        if not (200 <= st < 300) or not isinstance(lead, dict):
            print(f"  lead {lead_id} read [{st}]", flush=True)
            return
        pipeline = lead.get("pipeline_id")
        if pipeline == lib.PIPELINE_SALES_OLD:
            wazzup_in.promote_if_pending(amo, lead)
            return
        if pipeline not in (lib.PIPELINE_SALES_NEW, lib.PIPELINE_MKT_NEW):
            return
        if pipeline == lib.PIPELINE_SALES_NEW and wazzup_in.promote_if_pending(amo, lead):
            return
        if pipeline == lib.PIPELINE_MKT_NEW and lead.get("status_id") == lib.ST["mkt_talk"]:
            if lead.get("responsible_user_id") != lib.USER_POLINA:
                amo.req("PATCH", f"/api/v4/leads/{lead_id}", {"responsible_user_id": lib.USER_POLINA})
                lead["responsible_user_id"] = lib.USER_POLINA
        if pipeline == lib.PIPELINE_SALES_NEW and lead.get("status_id") == lib.ST["paid"]:
            moved = route_stock.apply_lead(amo, lib.MS(), lead, apply=True)
            if moved in ("pack", "prod"):
                lead["status_id"] = lib.ST[moved]
                print(f"  route {lead_id} -> {moved}", flush=True)
        ensure_tasks(amo, lead)


def spawn(events: list[dict]) -> None:
    seen: set[int] = set()
    for row in events:
        try:
            pipeline = int(row.get("pipeline_id") or 0)
            lead_id = int(row["id"])
        except (TypeError, ValueError, KeyError):
            continue
        if pipeline and pipeline not in (lib.PIPELINE_SALES_NEW, lib.PIPELINE_SALES_OLD, lib.PIPELINE_MKT_NEW):
            continue
        if lead_id in seen:
            continue
        seen.add(lead_id)
        threading.Thread(target=_safe, args=(lead_id,), daemon=True).start()


def _wazzup(body: dict) -> None:
    try:
        wazzup_in.handle_body(body)
    except Exception:
        traceback.print_exc()


def _safe(lead_id: int) -> None:
    try:
        handle_lead(lead_id)
    except Exception:
        traceback.print_exc()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        path = urlparse(self.path).path
        if path.startswith("/wazzup/"):
            path = "/wazzup"
        print(f"{self.address_string()} {self.command} {path}", flush=True)

    def _send(self, code: int, body: bytes, content: str = "text/plain; charset=utf-8") -> None:
        self.send_response(code)
        self.send_header("Content-Type", content)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/health":
            self._send(200, b"ok")
            return
        self._send(404, b"no")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/wazzup/"):
            expect = token()
            if not expect or parsed.path != f"/wazzup/{expect}":
                self._send(403, b"no")
                return
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            self._send(200, b"ok")
            try:
                body = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                return
            if isinstance(body, dict):
                threading.Thread(target=_wazzup, args=(body,), daemon=True).start()
            return
        if parsed.path != "/hook":
            self._send(404, b"no")
            return
        got = (parse_qs(parsed.query).get("token") or [""])[0]
        expect = token()
        if not expect or got != expect:
            self._send(403, b"no")
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        events = parse_events(raw, self.headers.get("Content-Type") or "")
        self._send(200, b"ok")
        spawn(events)


def main() -> None:
    if not token():
        raise SystemExit("Нет AMO_WEBHOOK_TOKEN")
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"hook {HOST}:{PORT}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
