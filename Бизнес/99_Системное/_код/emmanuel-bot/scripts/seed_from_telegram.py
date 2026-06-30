#!/usr/bin/env python3
import asyncio
import json
import re
import sys
from datetime import datetime
from collections import defaultdict
from pathlib import Path

# Add project root to sys.path so we can import app modules
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import select
from app.database import get_session_factory, Base, get_engine
from app.models import User, Report, Revelation, RevelationArchive
from app.time_utils import week_start_from_date, now_msk

DB_URL = "sqlite+aiosqlite:///data/emmanuel.sqlite3"
os.environ["DATABASE_URL"] = DB_URL

JSON_PATH = Path("/Users/max/Downloads/Telegram Desktop/ChatExport_2026-05-16/result.json")

def flatten(txt):
    if txt is None: return ""
    if isinstance(txt, str): return txt
    if isinstance(txt, list): return "".join(flatten(x) for x in txt)
    if isinstance(txt, dict): return flatten(txt.get("text", ""))
    return ""

def get_hashtag(t):
    m = re.search(r"#[\w\dА-яа-ёЁІіЇїЄєҐґ]+", t)
    return m.group(0) if m else None

def strip_noise(s):
    return (s or "").strip()

def blocks_from_body(body):
    parts = list(re.finditer(r"(?m)^(\d+)\.\s*([^\n]+)\n", body))
    out = []
    for i, m in enumerate(parts):
        n, title = m.group(1), m.group(2)
        start = m.end()
        end = parts[i + 1].start() if i + 1 < len(parts) else len(body)
        ans = body[start:end].rstrip("\n")
        out.append((int(n), strip_noise(title), ans))
    return out

def classify(title: str):
    t = title.lower()
    if "сколько времени" in t or ("часы" in t and "молитв" in t): return "prayer"
    if "проповед" in t: return "sermons"
    if "постил" in t or (t.startswith("пост") and "?" in t): return "fast"
    if "помочь" in t or "нуждаешься" in t or "помолиться" in t: return "help"
    if "откровен" in t: return "revelations"
    if "обхватил" in t or "личных встреч" in t or ("встречи" in t and "сколько" in t): return "meetings"
    if "процент" in t or ("план" in t and "прошедш" in t): return "plan_pct"
    if "план на след" in t or "план на следующ" in t: return "plan_next"
    return None

def first_line(s):
    s = strip_noise(s)
    return strip_noise(s.split("\n")[0]) if s else ""

def parse_hours_val(s):
    s = first_line(s).replace(",", ".")
    if not s or s in ("-", "—"): return None
    rng = re.search(r"(\d+(?:\.\d+)?)\s*[-–~]\s*(\d+(?:\.\d+)?)", s)
    if rng: return (float(rng.group(1)) + float(rng.group(2))) / 2
    m = re.search(r"(\d+(?:\.\d+)?)", s)
    return float(m.group(1)) if m else None

def parse_count(s, cap=99):
    s = first_line(s).replace(",", ".").removesuffix("+")
    if not s or s in ("-", "—"): return None
    m = re.search(r"(\d+)", s)
    if not m: return None
    v = int(m.group(1))
    return v if v <= cap else None

def parse_fast(s):
    s = first_line(s).lower()
    if not s or s == "-": return None
    if s.startswith("да") or s.startswith("yes") or s == "д": return True
    if s.startswith("нет") or s.startswith("no") or s == "н": return False
    if "нет" in s[:12]: return False
    if "да" in s[:12]: return True
    return None

def parse_report(body):
    rec = {}
    for _n, title, ans in blocks_from_body(body):
        kind = classify(title)
        if kind and kind not in rec:
            rec[kind] = ans
    return rec

async def main():
    if not JSON_PATH.exists():
        print(f"File not found: {JSON_PATH}")
        sys.exit(1)
        
    with JSON_PATH.open(encoding="utf-8") as f:
        data = json.load(f)
    
    # Check DB
    db_path = Path("data/emmanuel.sqlite3")
    if not db_path.exists():
        print(f"DB not found at {db_path}. Please download it from VPS first: scp root@85.192.38.49:/opt/emmanuel-report-bot/data/emmanuel.sqlite3 data/")
        sys.exit(1)
    
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
    async with get_session_factory()() as session:
        # Load existing users
        users = (await session.scalars(select(User))).all()
        user_by_hashtag = {u.report_hashtag().lower(): u for u in users}
        user_by_id = {u.tg_user_id: u for u in users}
        
        inserted_reports = 0
        
        for m in data.get("messages", []):
            if m.get("type") != "message": continue
            raw = flatten(m.get("text"))
            if "Сколько времени потратили" not in raw: continue
            
            tag = get_hashtag(raw)
            if not tag: continue
            
            rec = parse_report(raw)
            if "prayer" not in rec: continue
            
            try:
                dt = datetime.fromisoformat(m["date"])
            except Exception:
                continue
                
            week_start = week_start_from_date(dt.date())
            
            tg_user_id = m.get("from_id")
            if isinstance(tg_user_id, str) and tg_user_id.startswith("user"):
                tg_user_id = int(tg_user_id[4:])
            
            # Find user
            u = None
            if tg_user_id and tg_user_id in user_by_id:
                u = user_by_id[tg_user_id]
            else:
                u = user_by_hashtag.get(tag.lower())
                
            if not u:
                if not tg_user_id:
                    tg_user_id = abs(hash(tag)) % (10**10)
                u = User(
                    tg_user_id=tg_user_id,
                    tg_first_name=m.get("from"),
                    report_hashtag_override=tag,
                    created_at=now_msk()
                )
                session.add(u)
                await session.flush()
                user_by_id[tg_user_id] = u
                user_by_hashtag[tag.lower()] = u
            
            # Check if report exists
            existing = await session.scalar(select(Report).where(Report.user_id == u.id, Report.week_start == week_start))
            if existing:
                continue # Do not overwrite existing database reports
                
            # Create report
            pr_val = parse_hours_val(rec.get("prayer"))
            pr_str = str(pr_val) if pr_val is not None else "0"
            serm_val = parse_count(rec.get("sermons", ""), cap=35)
            serm_str = str(serm_val) if serm_val is not None else "0"
            fast_val = parse_fast(rec.get("fast", ""))
            meet_val = parse_count(rec.get("meetings", ""), cap=35)
            meet_str = str(meet_val) if meet_val is not None else "0"
            
            rep = Report(
                user_id=u.id,
                week_start=week_start,
                q1_prayer_hours=pr_str,
                q1b_bible_days="—",
                q2_sermons=serm_str,
                q3_fasted=bool(fast_val),
                q4_help=rec.get("help", "—").strip()[:1000],
                q5_revelations=rec.get("revelations", "—").strip()[:4000],
                q6_meetings=meet_str,
                q7_plan_pct="—",
                q8_next_plan="—",
                submitted_at=dt,
                on_time=True # Historical
            )
            session.add(rep)
            
            # Add to archive
            for line in rep.q5_revelations.replace("\r", "").split("\n"):
                line = line.strip().lstrip("-•●").strip()
                if len(line) > 5 and line != "—":
                    session.add(RevelationArchive(
                        user_id=u.id,
                        text=line,
                        report_week_start=week_start,
                        created_at=dt
                    ))
                    
            inserted_reports += 1
            
            # Update user streak safely (just sequential)
            u.last_report_week_start = week_start
            u.streak += 1
            
        await session.commit()
        print(f"Вставлено отчётов из Telegram: {inserted_reports}")

if __name__ == "__main__":
    asyncio.run(main())