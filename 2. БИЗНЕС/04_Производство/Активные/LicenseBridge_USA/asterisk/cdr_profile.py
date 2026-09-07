#!/usr/bin/env python3
"""Профиль дозвона по каждому нашему номеру из CDR Asterisk.

Это «report card» до всяких внешних сервисов: аналитические движки (Hiya, TNS,
First Orion) метят номер спамом не за содержание, а за поведение — много
коротких наборов с низкой долей ответов и повторы на один и тот же номер.
Скрипт показывает ровно эти цифры, чтобы разговор о метке шёл по фактам.

    scp root@159.65.97.156:/var/log/asterisk/cdr-csv/Master.csv /tmp/lb_master.csv
    python3 cdr_profile.py /tmp/lb_master.csv --from 2026-08-01 --to 2026-08-19

Колонки CSV — стандартный cdr-csv Asterisk. Номер, с которого звонили, лежит в
`src` (диалплан ставит CallerID = DID менеджера), набранный — в `lastdata`.
"""
from __future__ import annotations

import argparse
import collections
import csv
import re
import statistics
from datetime import datetime

FIELDS = ["accountcode", "src", "dst", "dcontext", "clid", "channel", "dstchannel",
          "lastapp", "lastdata", "start", "answer", "end", "duration", "billsec",
          "disposition", "amaflags", "uniqueid", "userfield"]
SHORT_TALK = 5          # разговор короче — «взяли и сбросили»
QUICK_REDIAL = 15 * 60  # повтор быстрее — движки видят навязчивый обзвон


def dialed(row: dict[str, str]) -> str:
    m = re.search(r"PJSIP/\+?(\d{7,15})@", row.get("lastdata") or "")
    return m.group(1)[-10:] if m else ""


def load(path: str, since: str, until: str) -> list[dict[str, str]]:
    out = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for r in csv.reader(f):
            if len(r) < len(FIELDS) - 1:
                continue
            row = dict(zip(FIELDS, r))
            day = (row.get("start") or "")[:10]
            if since <= day <= until:
                out.append(row)
    return out


def profile(rows: list[dict[str, str]]) -> None:
    out = [r for r in rows if r["dcontext"] in ("lb-outbound", "lb-ai-outbound")]
    by_did = collections.defaultdict(list)
    for r in out:
        by_did[r["src"] or "?"].append(r)

    print(f"исходящих наборов: {len(out)}\n")
    for did, rs in sorted(by_did.items(), key=lambda x: -len(x[1])):
        ans = [r for r in rs if r["disposition"] == "ANSWERED"]
        talk = [int(r["billsec"] or 0) for r in ans]
        short = [t for t in talk if t < SHORT_TALK]
        print(f"{did}: наборов {len(rs)}")
        print(f"  ответов {len(ans)} ({len(ans) * 100 // max(len(rs), 1)}%)")
        if talk:
            print(f"  разговор: средний {statistics.mean(talk):.0f} с, "
                  f"медиана {statistics.median(talk):.0f} с, "
                  f"короче {SHORT_TALK} с — {len(short)} из {len(ans)}")
        by_disp = collections.Counter(r["disposition"] for r in rs)
        print(f"  отбивки: {dict(by_disp)}")

        per_day = collections.Counter((r["start"][:10], dialed(r)) for r in rs if dialed(r))
        many = [(k, v) for k, v in per_day.items() if v > 1]
        print(f"  повторов на один номер за сутки: {len(many)}"
              f" (максимум {max([v for _, v in many], default=0)})")

        times = collections.defaultdict(list)
        for r in rs:
            if dialed(r):
                times[dialed(r)].append(datetime.strptime(r["start"], "%Y-%m-%d %H:%M:%S"))
        quick = 0
        for _, ts in times.items():
            ts.sort()
            quick += sum(1 for a, b in zip(ts, ts[1:]) if (b - a).total_seconds() < QUICK_REDIAL)
        print(f"  повторный набор быстрее {QUICK_REDIAL // 60} мин: {quick}\n")

    inb = [r for r in rows if r["dcontext"] == "lb-inbound"]
    ans_in = [r for r in inb if r["disposition"] == "ANSWERED"]
    print(f"входящих: {len(inb)}, отвечено {len(ans_in)}, "
          f"без разговора {len(inb) - len(ans_in)}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--from", dest="since", required=True)
    ap.add_argument("--to", dest="until", required=True)
    a = ap.parse_args()
    profile(load(a.csv, a.since, a.until))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
