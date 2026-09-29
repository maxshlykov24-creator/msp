#!/usr/bin/env python3
"""Один вебхук amo на создание сделки и смену этапа.

Ссылка одна. Этап берётся из тела: pipeline_id и status_id.
Старые воронки отбрасываются до запроса в amo.
"""

from __future__ import annotations

import json
import re
import threading
import time
import traceback
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import assign_shift
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
STATE_LOCK = threading.Lock()
CHAIN_NOTE = "Подошёл срок следующей задачи"
# Сделки, созданные раньше этой минуты, на «Новой заявке» и «Взята в работу» не ведём.
QUIET_BEFORE = int(datetime(2026, 9, 29, 9, 40, tzinfo=TZ).timestamp())


def state_path() -> Path:
    folder = Path("/opt/2my")
    if folder.is_dir():
        return folder / "chain_state.json"
    return Path("/tmp/2my_chain_state.json")


def load_state() -> dict:
    path = state_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def save_state(data: dict) -> None:
    path = state_path()
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


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


def planned_rows(lead: dict, moment: datetime) -> list[tuple[str, datetime, int, bool]]:
    rows = []
    for text, due, who in stage_plan.jobs(lead["pipeline_id"], lead["status_id"], moment):
        rid = who or lead.get("responsible_user_id") or lib.USER_OKSANA
        if lead["status_id"] == lib.ST["sent"] and text.startswith("Проставить трек"):
            track = amo_track(lead)
            if track:
                continue
        rows.append((text, due, rid, who is not None))
    rows.sort(key=lambda row: row[1].timestamp())
    return rows


def amo_track(lead: dict) -> str:
    amo = lib.Amo()
    return amo.cf(lead, lib.FIELD_TRACK) or amo.cf(lead, lib.FIELD_CDEK) or amo.cf(lead, lib.FIELD_TRACK_MS)


def create_task(amo: lib.Amo, lead_id: int, text: str, due: datetime, rid: int) -> None:
    payload = {
        "task_type_id": 1,
        "text": text,
        "complete_till": int(due.timestamp()),
        "entity_id": lead_id,
        "entity_type": "leads",
        "responsible_user_id": rid,
    }
    st, _ = amo.req("POST", "/api/v4/tasks", [payload])
    print(f"  task {lead_id} [{st}] {text} -> {due:%d.%m %H:%M} user {rid}", flush=True)


def human_task(task: dict) -> bool:
    """Живой сотрудник. Систему (0) и Максима можно снимать, человека нет."""
    who = task.get("created_by")
    return bool(who) and who != lib.USER_MAXIM


def quiet_existing(lead: dict) -> bool:
    if int(lead.get("created_at") or 0) >= QUIET_BEFORE:
        return False
    pipeline = lead.get("pipeline_id")
    status = lead.get("status_id")
    if pipeline == lib.PIPELINE_SALES_NEW and status in (lib.ST["new"], lib.ST["in_work"]):
        return True
    if pipeline == lib.PIPELINE_MKT_NEW and status == lib.ST["mkt_talk"]:
        return True
    return False


def finish_task(amo: lib.Amo, task: dict, note: str, responsible: int | None = None) -> None:
    if human_task(task):
        return
    if responsible:
        amo.req("PATCH", f"/api/v4/tasks/{task['id']}", {"responsible_user_id": responsible})
    amo.req("PATCH", f"/api/v4/tasks/{task['id']}", {
        "is_completed": True,
        "result": {"text": note},
    })


def forget_chain(lead_id: int) -> None:
    with STATE_LOCK:
        data = load_state()
        if str(lead_id) not in data:
            return
        del data[str(lead_id)]
        save_state(data)


def remember_chain(
    lead: dict,
    steps: list[tuple[str, datetime, int, bool]],
    index: int,
    mode: str,
    moment: datetime,
) -> None:
    # Вход на этап. Повтор с «прошло N» получает этот дедлайн, чтобы просрочка равнялась N.
    anchor = int(stage_plan.snap(moment).timestamp())
    saved = []
    for pos, (text, due, rid, pin) in enumerate(steps):
        own = int(due.timestamp())
        # keep хранит свой день: «8 дней» не рождается уже просроченной.
        # stack и handoff считают просрочку от входа на этап.
        if mode == "keep" or pos == 0:
            till = own
        else:
            till = anchor
        saved.append({
            "text": text,
            "due": own,
            "till": till,
            "rid": rid,
            "pin": pin,
        })
    with STATE_LOCK:
        data = load_state()
        data[str(lead["id"])] = {
            "pipeline_id": lead["pipeline_id"],
            "status_id": lead["status_id"],
            "index": index,
            "mode": mode,
            "steps": saved,
        }
        save_state(data)


def replace_open(amo: lib.Amo, lead: dict, planned: list[tuple[str, datetime, int, bool]]) -> None:
    wanted = {(text, rid) for text, _, rid, _ in planned}
    for task in open_tasks(amo, lead["id"]):
        key = (task.get("text") or "", task.get("responsible_user_id"))
        if key in wanted:
            continue
        finish_task(amo, task, "этап сменился")
    existing = {(t.get("text") or "", t.get("responsible_user_id")) for t in open_tasks(amo, lead["id"])}
    for text, due, rid, _pin in planned:
        if (text, rid) in existing:
            continue
        create_task(amo, lead["id"], text, due, rid)


def ensure_tasks(amo: lib.Amo, lead: dict) -> None:
    if quiet_existing(lead):
        return
    moment = datetime.now(TZ)
    planned = planned_rows(lead, moment)
    mode = stage_plan.follow_mode(lead["pipeline_id"], lead["status_id"])
    if len(planned) <= 1:
        forget_chain(lead["id"])
        replace_open(amo, lead, planned)
        return
    opened = open_tasks(amo, lead["id"])
    with STATE_LOCK:
        rec = load_state().get(str(lead["id"]))
    same = (
        isinstance(rec, dict)
        and rec.get("status_id") == lead["status_id"]
        and rec.get("pipeline_id") == lead["pipeline_id"]
    )
    if same:
        return
    for task in opened:
        finish_task(amo, task, "этап сменился")
    text, due, rid, _pin = planned[0]
    create_task(amo, lead["id"], text, due, rid)
    remember_chain(lead, planned, 0, mode, moment)


def advance_chains() -> None:
    now_ts = int(datetime.now(TZ).timestamp())
    with STATE_LOCK:
        items = list(load_state().items())
    for key, rec in items:
        if not isinstance(rec, dict):
            continue
        steps = rec.get("steps") or []
        index = int(rec.get("index") or 0)
        target = index
        if rec.get("mode") == "keep":
            if index + 1 < len(steps) and now_ts >= int(steps[index].get("due") or 0):
                target = index + 1
                while target + 1 < len(steps) and now_ts >= int(steps[target].get("due") or 0):
                    target += 1
        else:
            while target + 1 < len(steps) and now_ts >= int(steps[target + 1].get("due") or 0):
                target += 1
        if target == index:
            continue
        try:
            advance_one(int(key), target)
        except Exception:
            traceback.print_exc()


def advance_one(lead_id: int, target: int) -> None:
    with lock_for(lead_id):
        amo = lib.Amo()
        st, lead = amo.req("GET", f"/api/v4/leads/{lead_id}")
        if not (200 <= st < 300) or not isinstance(lead, dict):
            return
        with STATE_LOCK:
            rec = load_state().get(str(lead_id))
        if not isinstance(rec, dict):
            return
        if lead.get("status_id") != rec.get("status_id") or lead.get("pipeline_id") != rec.get("pipeline_id"):
            forget_chain(lead_id)
            return
        index = int(rec.get("index") or 0)
        if target <= index:
            return
        if rec.get("mode") == "keep":
            opened = {(task.get("text") or "") for task in open_tasks(amo, lead_id)}
            for step in rec["steps"][index + 1:target + 1]:
                if (step.get("text") or "") in opened:
                    continue
                rid = lead.get("responsible_user_id") or step.get("rid") or lib.USER_OKSANA
                due = datetime.fromtimestamp(int(step.get("due") or 0), TZ)
                create_task(amo, lead_id, step["text"], due, rid)
            with STATE_LOCK:
                data = load_state()
                row = data.get(str(lead_id))
                if isinstance(row, dict):
                    row["index"] = target
                    data[str(lead_id)] = row
                    save_state(data)
            return
        if rec.get("mode") == "stack":
            passed = {step.get("text") for step in rec["steps"][:target]}
            for task in open_tasks(amo, lead_id):
                if human_task(task):
                    continue
                if (task.get("text") or "") not in passed:
                    continue
                if task.get("responsible_user_id") == lib.USER_MAXIM:
                    continue
                amo.req("PATCH", f"/api/v4/tasks/{task['id']}", {
                    "responsible_user_id": lib.USER_MAXIM,
                })
                print(f"  stack hold {lead_id} {task.get('text')} -> Maxim", flush=True)
            opened = {(task.get("text") or "") for task in open_tasks(amo, lead_id)}
            for pos, step in enumerate(rec["steps"][index + 1:target + 1], start=index + 1):
                if (step.get("text") or "") in opened:
                    continue
                last = pos == target
                if last and step.get("pin"):
                    rid = step.get("rid") or lib.USER_OKSANA
                elif last:
                    rid = lead.get("responsible_user_id") or step.get("rid") or lib.USER_OKSANA
                else:
                    rid = lib.USER_MAXIM
                due = datetime.fromtimestamp(int(step.get("till") or step["due"]), TZ)
                create_task(amo, lead_id, step["text"], due, rid)
            with STATE_LOCK:
                data = load_state()
                row = data.get(str(lead_id))
                if isinstance(row, dict):
                    row["index"] = target
                    data[str(lead_id)] = row
                    save_state(data)
            return
        current = (rec["steps"][index] or {}).get("text")
        for task in open_tasks(amo, lead_id):
            if (task.get("text") or "") == current:
                finish_task(amo, task, CHAIN_NOTE, lib.USER_MAXIM)
                print(f"  chain close {lead_id} {current} -> Maxim", flush=True)
        step = rec["steps"][target]
        opened_now = open_tasks(amo, lead_id)
        if not any((task.get("text") or "") == step.get("text") for task in opened_now):
            rid = lead.get("responsible_user_id") or step.get("rid") or lib.USER_OKSANA
            due = datetime.fromtimestamp(int(step.get("till") or step["due"]), TZ)
            create_task(amo, lead_id, step["text"], due, rid)
        with STATE_LOCK:
            data = load_state()
            row = data.get(str(lead_id))
            if isinstance(row, dict):
                row["index"] = target
                data[str(lead_id)] = row
                save_state(data)


def chain_loop() -> None:
    while True:
        try:
            advance_chains()
        except Exception:
            traceback.print_exc()
        threading.Event().wait(30)


def shift_loop() -> None:
    while True:
        try:
            nxt = assign_shift.maybe_morning()
        except Exception:
            traceback.print_exc()
            nxt = time.time() + 900
        wait = max(5, nxt - time.time())
        threading.Event().wait(wait)


def handle_lead(lead_id: int) -> None:
    with lock_for(lead_id):
        amo = lib.Amo()
        st, lead = amo.req("GET", f"/api/v4/leads/{lead_id}")
        if not (200 <= st < 300) or not isinstance(lead, dict):
            print(f"  lead {lead_id} read [{st}]", flush=True)
            return
        pipeline = lead.get("pipeline_id")
        if pipeline == lib.PIPELINE_SALES_OLD:
            if not wazzup_in.promote_if_pending(amo, lead):
                assign_shift.nudge_lead(amo, lead)
            return
        if pipeline not in (lib.PIPELINE_SALES_NEW, lib.PIPELINE_MKT_NEW):
            return
        if pipeline == lib.PIPELINE_SALES_NEW and wazzup_in.promote_if_pending(amo, lead):
            return
        if pipeline == lib.PIPELINE_SALES_NEW:
            nudged = assign_shift.nudge_lead(amo, lead)
            if nudged:
                lead["responsible_user_id"] = nudged
        if (
            pipeline == lib.PIPELINE_MKT_NEW
            and lead.get("status_id") == lib.ST["mkt_talk"]
            and not quiet_existing(lead)
        ):
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
    threading.Thread(target=chain_loop, daemon=True).start()
    threading.Thread(target=shift_loop, daemon=True).start()
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"hook {HOST}:{PORT}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
