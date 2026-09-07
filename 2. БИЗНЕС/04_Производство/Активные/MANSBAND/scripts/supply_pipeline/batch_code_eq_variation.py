#!/usr/bin/env python3
"""
Массово: code модификации = характеристика «Вариация».

Надёжный ночной режим (медленнее, без обрывов из‑за Cursor):
  nohup python3 -u batch_code_eq_variation.py --apply --resume \
    > ../../_private/batch_code_eq_variation/nohup.out 2>&1 &

Параметры по умолчанию: 1 worker, chunk=40, timeout=180, полный verify при ошибке.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import MS_API, PRIVATE  # noqa: E402
from ms_api import MoySklad  # noqa: E402

STATE_DIR = PRIVATE / "batch_code_eq_variation"
WORKLIST = STATE_DIR / "worklist.jsonl"
CHECKPOINT = STATE_DIR / "checkpoint.json"
LOG = STATE_DIR / "run.log"
FAILS = STATE_DIR / "fails.jsonl"
DONE_IDS = STATE_DIR / "done_ids.txt"

# Надёжность > скорость
CHUNK = 20
POST_TIMEOUT = 90
GET_TIMEOUT = 25
MAX_ATTEMPTS = 5
SLEEP_BETWEEN = 0.2
SLEEP_RETRY = 3.0


def _log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def _variation_of(v: Dict[str, Any]) -> str:
    for c in v.get("characteristics") or []:
        if c.get("name") == "Вариация":
            return str(c.get("value") or "").strip()
    return ""


def scan(ms: MoySklad) -> Dict[str, int]:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if WORKLIST.exists():
        WORKLIST.unlink()

    total = need = skip_ok = skip_empty = 0
    offset = 0
    page = 1000
    _log("SCAN start")
    with WORKLIST.open("w", encoding="utf-8") as out:
        while True:
            data = ms.get(
                "/entity/variant",
                limit=page,
                offset=offset,
                expand="characteristics",
            )
            rows = data.get("rows") or []
            if not rows:
                break
            for v in rows:
                total += 1
                uid = v.get("id") or ""
                var = _variation_of(v)
                code = str(v.get("code") or "").strip()
                if not uid:
                    continue
                if not var:
                    skip_empty += 1
                    continue
                if code == var:
                    skip_ok += 1
                    continue
                out.write(
                    json.dumps(
                        {"id": uid, "code": var, "old_code": code},
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                need += 1
            offset += len(rows)
            size = (data.get("meta") or {}).get("size", offset)
            if offset % 2000 == 0 or offset >= size:
                _log(f"SCAN progress offset={offset}/{size} need={need} skip_ok={skip_ok}")
            if offset >= size:
                break
            time.sleep(0.03)

    stats = {
        "total": total,
        "need": need,
        "skip_ok": skip_ok,
        "skip_empty": skip_empty,
    }
    (STATE_DIR / "scan_stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _save_checkpoint({"ok": 0, "fail": 0, "finished": False})
    if DONE_IDS.exists():
        DONE_IDS.unlink()
    _log(f"SCAN done {stats}")
    return stats


def _load_worklist() -> List[Dict[str, Any]]:
    if not WORKLIST.exists():
        raise FileNotFoundError(f"Нет {WORKLIST}. Сначала --scan")
    items = []
    with WORKLIST.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def _load_done_ids() -> Set[str]:
    if not DONE_IDS.exists():
        # миграция со старого checkpoint с done_ids[]
        if CHECKPOINT.exists():
            try:
                cp = json.loads(CHECKPOINT.read_text(encoding="utf-8"))
                ids = cp.get("done_ids") or []
                if ids:
                    DONE_IDS.write_text("\n".join(ids) + "\n", encoding="utf-8")
                    return set(ids)
                # старый формат done=int
                if "done" in cp and isinstance(cp["done"], int) and cp["done"] > 0:
                    items = _load_worklist()
                    ids = [x["id"] for x in items[: int(cp["done"])]]
                    DONE_IDS.write_text("\n".join(ids) + "\n", encoding="utf-8")
                    return set(ids)
            except Exception:
                pass
        return set()
    return {
        line.strip()
        for line in DONE_IDS.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def _append_done_ids(ids: List[str]) -> None:
    with DONE_IDS.open("a", encoding="utf-8") as f:
        for uid in ids:
            f.write(uid + "\n")


def _save_checkpoint(cp: Dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    # компактно — без гигантского done_ids в JSON
    out = {
        "done": len(_load_done_ids()) if DONE_IDS.exists() else int(cp.get("done") or 0),
        "ok": int(cp.get("ok") or 0),
        "fail": int(cp.get("fail") or 0),
        "finished": bool(cp.get("finished")),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    if "done" in cp and not DONE_IDS.exists():
        out["done"] = int(cp["done"])
    CHECKPOINT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_checkpoint() -> Dict[str, Any]:
    if not CHECKPOINT.exists():
        return {"ok": 0, "fail": 0, "finished": False, "done": 0}
    return json.loads(CHECKPOINT.read_text(encoding="utf-8"))


def _get_code(ms: MoySklad, vid: str) -> str:
    row = ms._req("GET", f"/entity/variant/{vid}", timeout=GET_TIMEOUT) or {}
    return str(row.get("code") or "").strip()


def _post_chunk(
    ms: MoySklad, chunk: List[Dict[str, Any]]
) -> Tuple[List[str], List[str]]:
    """Возвращает (ok_ids, error_lines). Не-ok остаются для следующего retry."""
    if not chunk:
        return [], []
    body = [
        {
            "meta": {
                "href": f"{MS_API}/entity/variant/{it['id']}",
                "metadataHref": f"{MS_API}/entity/variant/metadata",
                "type": "variant",
                "mediaType": "application/json",
            },
            "code": it["code"],
        }
        for it in chunk
    ]
    ok_ids: List[str] = []
    errors: List[str] = []
    try:
        resp = ms.post("/entity/variant", body, timeout=POST_TIMEOUT)
        if not isinstance(resp, list):
            raise RuntimeError(f"bad resp type {type(resp)}")
        by_id = {it["id"]: it for it in chunk}
        # ответ batch может быть короче / в другом порядке — сверяем по href/id
        for row in resp:
            href = ((row.get("meta") or {}).get("href") or "")
            vid = href.rstrip("/").split("/")[-1] if href else ""
            it = by_id.get(vid)
            if not it:
                continue
            if str(row.get("code") or "").strip() == it["code"]:
                ok_ids.append(it["id"])
            else:
                errors.append(
                    f"{it['id']}: got={row.get('code')!r} want={it['code']!r}"
                )
        # кого нет в resp — verify точечно
        for it in chunk:
            if it["id"] in ok_ids:
                continue
            try:
                if _get_code(ms, it["id"]) == it["code"]:
                    ok_ids.append(it["id"])
                else:
                    if not any(e.startswith(it["id"]) for e in errors):
                        errors.append(f"{it['id']}: not confirmed after post")
            except Exception as e2:
                errors.append(f"{it['id']}: verify {e2}")
        return ok_ids, errors
    except Exception as e:
        errors = [f"post_error: {e}"]
        for it in chunk:
            try:
                if _get_code(ms, it["id"]) == it["code"]:
                    ok_ids.append(it["id"])
                else:
                    errors.append(f"{it['id']}: got≠want after post_error")
            except Exception as e2:
                errors.append(f"{it['id']}: verify {e2}")
        if len(ok_ids) == len(chunk):
            return ok_ids, [f"timeout_recovered_full_verify: {e}"]
        return ok_ids, errors


def apply(ms: MoySklad, *, resume: bool) -> Dict[str, Any]:
    items = _load_worklist()
    if not resume:
        if FAILS.exists():
            FAILS.unlink()
        if DONE_IDS.exists():
            DONE_IDS.unlink()
        cp = {"ok": 0, "fail": 0, "finished": False, "done": 0}
        _save_checkpoint(cp)
    else:
        cp = _load_checkpoint()

    done_set = _load_done_ids()
    pending = [x for x in items if x["id"] not in done_set]
    _log(
        f"APPLY reliable start total={len(items)} pending={len(pending)} "
        f"chunk={CHUNK} workers=1 timeout={POST_TIMEOUT}s get={GET_TIMEOUT}s "
        f"resume={resume}"
    )
    t0 = time.time()
    ok_total = int(cp.get("ok") or 0)
    fail_total = int(cp.get("fail") or 0)

    deferred: List[Dict[str, Any]] = []

    def _process_chunk(
        chunk: List[Dict[str, Any]], *, label: str, collect_defer: bool
    ) -> List[Dict[str, Any]]:
        nonlocal ok_total
        remaining = list(chunk)
        confirmed: List[str] = []
        last_errors: List[str] = []

        for attempt in range(1, MAX_ATTEMPTS + 1):
            if not remaining:
                break
            ok_ids, errors = _post_chunk(ms, remaining)
            last_errors = errors
            if ok_ids:
                confirmed.extend(ok_ids)
                ok_set = set(ok_ids)
                remaining = [x for x in remaining if x["id"] not in ok_set]
            if not remaining:
                break
            if attempt < MAX_ATTEMPTS:
                _log(
                    f"RETRY {label} attempt={attempt} ok={len(confirmed)}/{len(chunk)} "
                    f"left={len(remaining)} sleep {SLEEP_RETRY:.0f}s"
                )
                time.sleep(SLEEP_RETRY)

        if confirmed:
            _append_done_ids(confirmed)
            ok_total += len(confirmed)
        if remaining:
            with FAILS.open("a", encoding="utf-8") as ff:
                for it in remaining:
                    ff.write(
                        json.dumps(
                            {
                                "id": it["id"],
                                "code": it["code"],
                                "error": "max_attempts",
                                "pass": label,
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                for e in last_errors:
                    if e.startswith("timeout_recovered") or e.startswith("post_error"):
                        continue
                    ff.write(json.dumps({"error": e}, ensure_ascii=False) + "\n")
            _log(
                f"CHUNK left={len(remaining)} pass={label} "
                f"ids={[x['id'][:8] for x in remaining]}"
            )
            if collect_defer:
                deferred.extend(remaining)

        _save_checkpoint({"ok": ok_total, "fail": 0, "finished": False})
        done_n = len(_load_done_ids())
        elapsed = time.time() - t0
        rate = max(done_n - int(cp.get("ok") or 0), 0) / elapsed if elapsed > 0 else 0
        # rate по прогрессу этого запуска
        progressed = done_n - (len(items) - len(pending))
        rate = progressed / elapsed if elapsed > 0 and progressed > 0 else rate
        left = len(items) - done_n
        eta = left / rate if rate > 0 else 0
        _log(
            f"APPLY {done_n}/{len(items)} ok={ok_total} "
            f"rate={rate:.2f}/s eta={eta/60:.1f}m"
        )
        time.sleep(SLEEP_BETWEEN)
        return remaining

    for i in range(0, len(pending), CHUNK):
        _process_chunk(pending[i : i + CHUNK], label="chunk", collect_defer=True)

    if deferred:
        _log(f"DEFER pass start n={len(deferred)}")
        time.sleep(5)
        still: List[Dict[str, Any]] = []
        for i in range(0, len(deferred), max(5, CHUNK // 2)):
            left = _process_chunk(
                deferred[i : i + max(5, CHUNK // 2)],
                label="defer",
                collect_defer=False,
            )
            still.extend(left)
        fail_total = len(still)
        if still:
            _log(f"STILL failed after defer pass: {len(still)}")

    all_done = len(_load_done_ids()) >= len(items)
    _save_checkpoint({"ok": ok_total, "fail": fail_total, "finished": all_done})
    _log(
        f"APPLY done ok={ok_total} fail={fail_total} finished={all_done} "
        f"elapsed={(time.time()-t0)/60:.1f}m"
    )
    return {"ok": ok_total, "fail": fail_total, "finished": all_done}


def _daemonize() -> None:
    """Double-fork: процесс переживает закрытие Cursor/терминала."""
    import os

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if os.fork() > 0:
        sys.exit(0)
    os.setsid()
    if os.fork() > 0:
        sys.exit(0)
    sys.stdout.flush()
    sys.stderr.flush()
    logf = open(STATE_DIR / "nohup.out", "a", encoding="utf-8")
    os.dup2(logf.fileno(), 1)
    os.dup2(logf.fileno(), 2)
    (STATE_DIR / "pid.txt").write_text(str(os.getpid()), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--scan-apply", action="store_true")
    ap.add_argument(
        "--daemon",
        action="store_true",
        help="Отвязать процесс (double-fork), писать в nohup.out",
    )
    args = ap.parse_args()
    if not (args.scan or args.apply or args.scan_apply):
        ap.print_help()
        sys.exit(2)

    if args.daemon:
        _daemonize()
        _log(f"DAEMON pid={Path(STATE_DIR / 'pid.txt').read_text().strip()}")

    ms = MoySklad()
    if args.scan or args.scan_apply:
        scan(ms)
    if args.apply or args.scan_apply:
        apply(ms, resume=args.resume)


if __name__ == "__main__":
    main()
