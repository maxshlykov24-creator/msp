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
OUT_KEY = re.compile(r"outgoing_message\[add\]\[(\d+)\]\[(.+)\]")
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


def quiet_path() -> Path:
    folder = Path("/opt/2my")
    if folder.is_dir():
        return folder / "move_quiet.json"
    return Path("/tmp/2my_move_quiet.json")


QUIET_LOCK = threading.Lock()


def take_move_quiet(lead_id: int) -> bool:
    """Один перенос без задач и без смены ответственного. Следующий живой этап уже обычный."""
    path = quiet_path()
    with QUIET_LOCK:
        if not path.is_file():
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return False
        ids = data.get("ids") if isinstance(data, dict) else None
        if not isinstance(ids, list):
            return False
        key = str(lead_id)
        if key not in {str(item) for item in ids}:
            return False
        data["ids"] = [item for item in ids if str(item) != key]
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    print(f"  quiet move {lead_id}", flush=True)
    return True


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


def _put_out(bucket: dict, idx: str, field: str, value) -> None:
    row = bucket.setdefault(idx, {})
    if "[" in field:
        return
    row[field] = value


def parse_outgoing(raw: bytes, content_type: str) -> list[dict]:
    text = raw.decode("utf-8", "replace")
    if "json" in content_type:
        try:
            body = json.loads(text or "{}")
        except json.JSONDecodeError:
            return []
        block = body.get("outgoing_message") if isinstance(body, dict) else None
        rows = block.get("add") if isinstance(block, dict) else None
        return [row for row in rows or [] if isinstance(row, dict)]
    qs = parse_qs(text, keep_blank_values=False)
    bucket: dict[str, dict] = {}
    for key, vals in qs.items():
        match = OUT_KEY.fullmatch(key)
        if not match or not vals:
            continue
        _put_out(bucket, match.group(1), match.group(2), vals[0])
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
        if pipeline == lib.PIPELINE_SALES_NEW and take_move_quiet(lead_id):
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


def _outgoing(row: dict) -> None:
    try:
        wazzup_in.reply_from_amo(row)
    except Exception:
        traceback.print_exc()


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
        content_type = self.headers.get("Content-Type") or ""
        events = parse_events(raw, content_type)
        outgoing = parse_outgoing(raw, content_type)
        self._send(200, b"ok")
        spawn(events)
        for row in outgoing:
            threading.Thread(target=_outgoing, args=(row,), daemon=True).start()


def main() -> None:
    if not token():
        raise SystemExit("Нет AMO_WEBHOOK_TOKEN")
    threading.Thread(target=chain_loop, daemon=True).start()
    threading.Thread(target=shift_loop, daemon=True).start()
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"hook {HOST}:{PORT}", flush=True)
    httpd.serve_forever()


# Site-channel processing is an independent CLI mode, never an HTTP handler.
SITE_START = '2026-10-28 00:00:00'
SITE_ID = '07ff5606-dc11-11ef-0a80-03cd002298d2'
SITE_ATTR = '48be380c-dc17-11ef-0a80-03cd0022c50b'
SITE_UA = 'MSProduct-2MY-SiteChannels/1.0'


class SiteDeferred(Exception):
    pass


class SiteFatal(Exception):
    pass


def site_stamp(value):
    return datetime.fromisoformat(value).replace(tzinfo=TZ).timestamp()


def site_now():
    return datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]


def site_hash(value):
    import hashlib
    def canonical(v):
        if isinstance(v, dict):
            return {k: canonical(x) for k, x in v.items() if k != 'meta'}
        if isinstance(v, list):
            return sorted([canonical(x) for x in v], key=lambda x: json.dumps(x, sort_keys=True))
        return v
    # Retain reference identity, but discard volatile metadata (size/limit/offset).
    def refs(v):
        if isinstance(v, dict):
            out = {k: refs(x) for k, x in v.items() if k != 'meta'}
            if isinstance(v.get('meta'), dict):
                out['_ref'] = v['meta'].get('href', '').split('?')[0]
            return out
        if isinstance(v, list):
            return [refs(x) for x in v]
        return v
    return hashlib.sha256(json.dumps(canonical(refs(value)), ensure_ascii=False,
                                    sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def site_core(order):
    return site_hash({k: v for k, v in order.items() if k not in ('meta', 'updated', 'salesChannel')})


def site_channel(order):
    return lib.href_id((order.get('salesChannel') or {}).get('meta'))


def site_events_signature(events):
    return site_hash([{k: e.get(k) for k in ('moment', 'eventType', 'source', 'audit', 'diff')}
                      for e in events])


def site_channel_change(event):
    diff = event.get('diff')
    if event.get('eventType') == 'create':
        return False
    if not isinstance(diff, dict):
        # An unknown change cannot prove that a channel was untouched.
        raise SiteDeferred('audit_change_without_diff')
    return any(k in ('salesChannel', 'Канал продажи', 'Канал продаж', SITE_ATTR, 'attributes') for k in diff)


def site_source(info):
    # Parse the audit's application identifier, not arbitrary document text.
    text = str((info or {}).get('additionalInfo') or '')
    match = re.fullmatch(r'\([^,]+, WordPress/[^;\s]+;\s*(https?://[^\s)]+)\)', text)
    if not match:
        return False
    parsed = urlparse(match.group(1))
    return (parsed.hostname == '2mymoods.ru' and not parsed.username and not parsed.password
            and parsed.netloc == '2mymoods.ru' and parsed.path in ('', '/')
            and not parsed.query and not parsed.fragment)


def site_decision(order, events, creation_info):
    if not order.get('created') or site_stamp(order['created']) < site_stamp(SITE_START):
        return 'skip_before_period'
    if order.get('deleted') or order.get('archived'):
        return 'skip_archived'
    if site_channel(order):
        return 'skip_filled'
    creates = [e for e in events if e.get('eventType') == 'create']
    if len(creates) != 1:
        raise SiteDeferred('creation_history_incomplete')
    source = creates[0].get('source')
    if not source:
        raise SiteDeferred('creation_source_missing')
    if source == 'remap-1.2' and ',' not in str((creation_info or {}).get('additionalInfo') or ''):
        raise SiteDeferred('creation_client_not_available')
    if source != 'remap-1.2' or not site_source(creation_info):
        return 'skip_other_source'
    if any(site_channel_change(e) for e in events):
        return 'skip_prior_channel_change'
    for attr in order.get('attributes') or []:
        if attr.get('id') == SITE_ATTR or attr.get('name') in ('Канал продажи', 'Канал продаж'):
            value = attr.get('value')
            if value and (not isinstance(value, dict) or value.get('name') != 'Сайт'):
                return 'conflict_extra_channel'
    return 'eligible'


class SiteAPI:
    def __init__(self):
        import urllib.request
        self.token = lib.load_env().get('MOYSKLAD_TOKEN', '').strip()
        if not self.token:
            raise SiteFatal('missing_ms_token')
        self.last = 0.0
        self.opener = urllib.request.build_opener()

    def request(self, path, params=None, body=None):
        import gzip
        import urllib.error
        import urllib.request
        from urllib.parse import urlencode
        if not (path.startswith('/entity/customerorder') or path.startswith('/audit/')
                or path == '/entity/saleschannel/' + SITE_ID):
            raise SiteFatal('disallowed_endpoint')
        if body is not None and (not re.fullmatch(r'/entity/customerorder/[0-9a-f-]{36}', path)
                                 or set(body) != {'salesChannel'}):
            raise SiteFatal('disallowed_write')
        time.sleep(max(0, 1.0 - (time.monotonic() - self.last)))
        url = lib.MS_BASE + path
        if params:
            url += '?' + urlencode(params)
        headers = {'Authorization': 'Bearer ' + self.token, 'Accept': 'application/json;charset=utf-8',
                   'Accept-Encoding': 'gzip', 'User-Agent': SITE_UA}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers.update({'Content-Type': 'application/json', 'X-Lognex-WebHook-Disable': 'true'})
        req = urllib.request.Request(url, data=data, headers=headers, method='PUT' if data else 'GET')
        try:
            with self.opener.open(req, timeout=30) as r:
                raw = r.read()
                if r.headers.get('Content-Encoding') == 'gzip':
                    raw = gzip.decompress(raw)
                return json.loads(raw)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise SiteFatal('ms_access_' + str(e.code)) from None
            if e.code == 429:
                try:
                    wait = max(60, float(e.headers.get('X-Lognex-Retry-After', '0')) / 1000,
                               float(e.headers.get('Retry-After', '0')))
                except ValueError:
                    wait = 60
                # Persisted by the worker; no other request may bypass this cooldown.
                err = SiteDeferred('ms_429')
                err.retry_after = min(wait, 86400)
                raise err from None
            raise SiteDeferred('ms_http_' + str(e.code)) from None
        except (SiteFatal, SiteDeferred):
            raise
        except Exception as e:
            raise SiteDeferred('ms_transport_' + type(e).__name__) from None
        finally:
            self.last = time.monotonic()

    def rows(self, path, params=None):
        params = dict(params or {})
        offset, total, result, seen = 0, None, [], set()
        for _ in range(10000):
            page = self.request(path, {**params, 'limit': 100, 'offset': offset})
            size = (page.get('meta') or {}).get('size')
            chunk = page.get('rows')
            if not isinstance(size, int) or not isinstance(chunk, list):
                raise SiteDeferred('invalid_page')
            if total is not None and size != total:
                raise SiteDeferred('page_count_changed')
            total = size
            for row in chunk:
                key = row.get('id') or site_hash({k: row.get(k) for k in ('audit', 'moment', 'eventType', 'diff')})
                if key in seen:
                    raise SiteDeferred('duplicate_page_row')
                seen.add(key)
                result.append(row)
            offset += len(chunk)
            if offset == total:
                return result
            if not chunk or offset > total:
                raise SiteDeferred('incomplete_pages')
        raise SiteDeferred('page_limit')


class SiteWorker:
    def __init__(self, root, api=None):
        import os
        import sqlite3
        import fcntl
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self.lock = (self.root / 'worker.lock').open('a')
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            raise SiteFatal('worker_already_running') from None
        self.db = sqlite3.connect(self.root / 'state.sqlite3')
        os.chmod(self.root / 'state.sqlite3', 0o600)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, created TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending', reason TEXT, due REAL NOT NULL DEFAULT 0,
                attempts INTEGER NOT NULL DEFAULT 0, intent_at REAL, before_hash TEXT,
                before_updated TEXT, audit_before TEXT, verified_at REAL);
            CREATE TABLE IF NOT EXISTS journal(seq INTEGER PRIMARY KEY, at TEXT NOT NULL,
                order_id TEXT, state TEXT, reason TEXT);
        ''')
        self.api = api or SiteAPI()

    def setting(self, key, default=''):
        row = self.db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return row[0] if row else default

    def set(self, key, value):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, str(value)))

    def mark(self, oid, state, reason, due=0):
        with self.db:
            self.db.execute('UPDATE jobs SET state=?, reason=?, due=? WHERE id=?', (state, reason, due, oid))
            self.db.execute('INSERT INTO journal(at,order_id,state,reason) VALUES (?,?,?,?)',
                            (site_now(), oid, state, reason))
        print(json.dumps({'order_id': oid, 'state': state, 'reason': reason}), flush=True)

    def enqueue(self, rows):
        with self.db:
            for row in rows:
                if not re.fullmatch(r'[0-9a-f-]{36}', row.get('id', '')) or not row.get('created'):
                    raise SiteDeferred('invalid_discovery_row')
                self.db.execute('INSERT OR IGNORE INTO jobs(id,created,state,reason) VALUES (?,?,?,?)',
                    (row['id'], row['created'], 'skipped' if site_channel(row) else 'pending',
                     'skip_filled' if site_channel(row) else None))

    def discover(self):
        from datetime import timedelta
        upper = datetime.now(TZ) - timedelta(seconds=120)
        daily = time.time() - float(self.setting('full_scan_at', '0')) > 86400
        since = SITE_START if daily else self.setting('cursor', SITE_START)
        start = max(site_stamp(SITE_START), site_stamp(since) - 600)
        lower = datetime.fromtimestamp(start, TZ).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        end = upper.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        rows = self.api.rows('/entity/customerorder', {'filter': 'created>='+lower+';created<='+end,
                                                     'order': 'created,asc'})
        self.enqueue(rows)
        self.set('cursor', end)
        if daily:
            self.set('full_scan_at', time.time())
        self.set('discovery_ok_at', site_now())

    def evidence(self, oid, order=None):
        path = '/entity/customerorder/' + oid
        order = order if order is not None else self.api.request(path)
        if order.get('id') != oid:
            raise SiteDeferred('wrong_order')
        events = self.api.rows(path + '/audit')
        creates = [e for e in events if e.get('eventType') == 'create']
        info = {}
        if len(creates) == 1:
            aid = lib.href_id((creates[0].get('audit') or {}).get('meta'))
            if not re.fullmatch(r'[0-9a-f-]{36}', aid):
                raise SiteDeferred('missing_creation_audit')
            record = self.api.request('/audit/' + aid)
            if record.get('id') != aid or record.get('eventType') != 'create' or record.get('entityType') != 'customerorder':
                raise SiteDeferred('invalid_creation_audit')
            info = record.get('info') or {}
        return order, events, info

    def verify(self, job):
        oid = job['id']
        order = self.api.request('/entity/customerorder/' + oid)
        if site_channel(order) != SITE_ID:
            if time.time() - job['intent_at'] < 300:
                self.mark(oid, 'verifying', 'await_write_result', time.time() + 60)
            else:
                self.mark(oid, 'conflict', 'write_unconfirmed_or_channel_changed')
            return
        if site_core(order) != job['before_hash']:
            self.mark(oid, 'conflict', 'other_fields_changed_needs_review')
            return
        events = self.api.rows('/entity/customerorder/' + oid + '/audit')
        previous = set(json.loads(job['audit_before']))
        ours = False
        for event in events:
            aid = lib.href_id((event.get('audit') or {}).get('meta'))
            if not aid:
                raise SiteDeferred('missing_audit_id')
            if aid in previous or not site_channel_change(event):
                continue
            record = self.api.request('/audit/' + aid)
            info = str((record.get('info') or {}).get('additionalInfo') or '')
            diff = event.get('diff') or {}
            new = (diff.get('salesChannel') or {}).get('newValue') or {}
            if (event.get('source') == 'remap-1.2' and SITE_UA in info
                    and set(diff) == {'salesChannel'}
                    and not (diff.get('salesChannel') or {}).get('oldValue')
                    and lib.href_id(new.get('meta')) == SITE_ID):
                ours = True
            else:
                self.mark(oid, 'conflict', 'concurrent_channel_change')
                return
        if not ours:
            if time.time() - job['intent_at'] > 600:
                self.mark(oid, 'conflict', 'own_audit_not_confirmed')
            else:
                self.mark(oid, 'verifying', 'await_own_audit', time.time() + 60)
            return
        if time.time() - job['intent_at'] < 120:
            self.mark(oid, 'verifying', 'immediate_verified', job['intent_at'] + 120)
            return
        with self.db:
            self.db.execute('UPDATE jobs SET verified_at=? WHERE id=?', (time.time(), oid))
        self.mark(oid, 'done', 'delayed_verified')

    def process(self, job, apply=False):
        oid = job['id']
        if job['state'] in ('intent', 'verifying'):
            self.verify(job)
            return
        current = self.api.request('/entity/customerorder/' + oid)
        if site_channel(current):
            self.mark(oid, 'skipped', 'skip_filled')
            return
        order, events, info = self.evidence(oid, current)
        decision = site_decision(order, events, info)
        if decision != 'eligible':
            self.mark(oid, 'conflict' if decision.startswith('conflict') else 'skipped', decision)
            return
        if time.time() - site_stamp(order.get('updated') or order['created']) < 120:
            raise SiteDeferred('order_still_changing')
        if not apply:
            self.mark(oid, 'pending', 'dry_run_eligible', time.time() + 60)
            return
        history = self.api.rows('/entity/customerorder/' + oid + '/audit')
        if site_events_signature(history) != site_events_signature(events):
            raise SiteDeferred('evidence_changed')
        # The creation audit is immutable and its reference was just rechecked.
        # Read the card after the second complete audit, immediately before PUT.
        final = self.api.request('/entity/customerorder/' + oid)
        if site_hash(final) != site_hash(order):
            raise SiteDeferred('card_changed_before_put')
        if site_decision(final, history, info) != 'eligible':
            raise SiteDeferred('decision_changed')
        aids = [lib.href_id((e.get('audit') or {}).get('meta')) for e in history]
        if not all(aids):
            raise SiteDeferred('missing_audit_id')
        with self.db:
            self.db.execute('UPDATE jobs SET state=?,intent_at=?,before_hash=?,before_updated=?,audit_before=? WHERE id=?',
                ('intent', time.time(), site_core(final), final.get('updated'), json.dumps(aids), oid))
        body = {'salesChannel': {'meta': {'href': lib.MS_BASE + '/entity/saleschannel/' + SITE_ID,
                                         'type': 'saleschannel', 'mediaType': 'application/json'}}}
        self.api.request('/entity/customerorder/' + oid, body=body)
        self.verify(self.db.execute('SELECT * FROM jobs WHERE id=?', (oid,)).fetchone())

    def cycle(self, apply=False, ids=None):
        if self.setting('halt'):
            raise SiteFatal(self.setting('halt'))
        if time.time() < float(self.setting('cooldown_until', '0')):
            return
        target = self.api.request('/entity/saleschannel/' + SITE_ID)
        if target.get('id') != SITE_ID or target.get('name') != 'Сайт' or target.get('archived'):
            raise SiteFatal('site_channel_invalid')
        if ids:
            self.enqueue([self.api.request('/entity/customerorder/' + oid) for oid in ids])
        else:
            self.discover()
        query = "SELECT * FROM jobs WHERE state IN ('pending','intent','verifying') AND due<=?"
        args = [time.time()]
        if ids:
            query += ' AND id IN (' + ','.join('?' for _ in ids) + ')'
            args.extend(ids)
        query += " ORDER BY CASE WHEN state IN ('intent','verifying') THEN 0 ELSE 1 END, created DESC LIMIT 15"
        for job in self.db.execute(query, args).fetchall():
            try:
                self.process(job, apply)
            except SiteDeferred as exc:
                current = self.db.execute('SELECT * FROM jobs WHERE id=?', (job['id'],)).fetchone()
                state = 'verifying' if current['state'] in ('intent', 'verifying') else 'pending'
                attempts = current['attempts'] + 1
                delay = min(3600, 60 * 2 ** min(attempts - 1, 6))
                self.mark(job['id'], state, str(exc), time.time() + delay)
                with self.db:
                    self.db.execute('UPDATE jobs SET attempts=? WHERE id=?', (attempts, job['id']))
                if str(exc) == 'ms_429':
                    self.set('cooldown_until', time.time() + getattr(exc, 'retry_after', 60))
                    raise
        self.set('last_cycle_at', site_now())
        self.status()

    def status(self):
        counts = dict(self.db.execute('SELECT state,count(*) FROM jobs GROUP BY state').fetchall())
        print(json.dumps({'counts': counts, 'last_cycle_at': self.setting('last_cycle_at'),
                          'discovery_ok_at': self.setting('discovery_ok_at'),
                          'halt': self.setting('halt'), 'last_error': self.setting('last_error')}, ensure_ascii=False), flush=True)


def site_main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--site-channels', action='store_true')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--loop', action='store_true')
    parser.add_argument('--status', action='store_true')
    parser.add_argument('--root', default='/var/lib/2my-site-channels')
    parser.add_argument('--ids', nargs='*')
    args = parser.parse_args()
    if args.ids and any(not re.fullmatch(r'[0-9a-f-]{36}', oid) for oid in args.ids):
        parser.error('invalid order UUID')
    if args.status:
        import sqlite3
        db = sqlite3.connect('file:' + str(Path(args.root) / 'state.sqlite3') + '?mode=ro', uri=True)
        print(json.dumps({'settings': dict(db.execute('SELECT key,value FROM settings')),
                          'counts': dict(db.execute('SELECT state,count(*) FROM jobs GROUP BY state')),
                          'reasons': dict(db.execute('SELECT reason,count(*) FROM jobs GROUP BY reason'))}, ensure_ascii=False))
        db.close()
        return
    worker = SiteWorker(args.root)
    while True:
        try:
            worker.cycle(args.apply, args.ids)
            worker.set('last_error', '')
        except SiteFatal as exc:
            worker.set('halt', str(exc))
            worker.status()
            raise SystemExit(1)
        except Exception as exc:
            reason = str(exc) if isinstance(exc, SiteDeferred) else type(exc).__name__
            worker.set('last_error', reason)
            if reason == 'ms_429':
                worker.set('cooldown_until', time.time() + getattr(exc, 'retry_after', 60))
            worker.status()
            if not args.loop:
                raise SystemExit(1)
        if not args.loop:
            break
        time.sleep(60)


if __name__ == "__main__":
    import sys
    if "--site-channels" in sys.argv:
        site_main()
    else:
        main()
