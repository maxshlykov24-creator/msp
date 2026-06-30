#!/usr/bin/env python3
"""
Переименование отгрузок (demand) в МойСклад: после якорного номера — подряд ВА-n по полю moment.

Организация по умолчанию: ООО «ВАН ПАК» (Wangpack). Якорь по умолчанию: ВА-18066.
Документы с moment строго позже якоря получают ВА-18067, ВА-18068, …
Якорный документ и все более ранние не меняются.

По умолчанию: все отгрузки организации, доступные через filter=organization=… (как в основном
списке API). Опция «только неархивные» — отдельный фильтр; если API вернёт 412, выполняется
запасной запрос только по организации.

Коллизия имён: если целевой номер уже занят другой отгрузкой, сначала та получает
имя «как было + дефис в конце» (при занятости — добавляются ещё дефисы: --, ---, …).

Токен только из окружения.

При заданном окне по полю moment: проходится полная цепочка после якоря (для коллизий имён),
а PUT нового номера выполняется только у документов, попадающих в окно.

Примеры:
  MS_TOKEN=... python3 ms_renumber_demands_after_anchor.py
  MS_TOKEN=... python3 ms_renumber_demands_after_anchor.py --expect-moment "2026-02-27 13:18:00"
  MS_TOKEN=... python3 ms_renumber_demands_after_anchor.py --apply --i-know
  MS_TOKEN=... python3 ms_renumber_demands_after_anchor.py --verify-only
  MS_TOKEN=... python3 ms_renumber_demands_after_anchor.py --moment-from 2026-05-01 --moment-to 2026-05-31 --verify-only
  MS_TOKEN=... python3 ms_renumber_demands_after_anchor.py --moment-from 2026-05-01 --moment-to 2026-05-31 --apply --i-know

Переменные:
  MS_OUTPUT_DIR — каталог журнала (по умолчанию /tmp)
  MS_APPLY_DELAY — пауза после PUT, сек (по умолчанию 0.08)
  MS_PREFETCH_DELAY — пауза между filter=name запросами (по умолчанию 0.12)
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import time
from datetime import datetime

import requests
from requests.exceptions import ChunkedEncodingError, ConnectTimeout, ReadTimeout

TOKEN = os.environ.get("MS_TOKEN", "")
API_BASE = "https://api.moysklad.ru/api/remap/1.2"
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/json;charset=utf-8",
    "Accept-Encoding": "gzip",
    "Content-Type": "application/json",
}
RATE_LIMIT_S = float(os.environ.get("MS_APPLY_DELAY", "0.08"))
PREFETCH_DELAY_S = float(os.environ.get("MS_PREFETCH_DELAY", "0.12"))

_TRANSIENT_NET = (
    ConnectTimeout,
    ReadTimeout,
    ChunkedEncodingError,
    requests.exceptions.ConnectionError,
)


def api_get(url_or_path: str, params: dict | None = None) -> dict:
    url = url_or_path if str(url_or_path).startswith("http") else f"{API_BASE}{url_or_path}"
    for attempt in range(8):
        try:
            r = requests.get(url, headers=HEADERS, params=params, timeout=120)
        except _TRANSIENT_NET as e:
            time.sleep(min(2**attempt, 30))
            if attempt == 7:
                raise RuntimeError(f"GET сеть/таймаут: {e}") from e
            continue
        if r.status_code == 429:
            time.sleep(2 ** min(attempt + 1, 5))
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("GET failed")


def api_put(url_or_path: str, body: dict) -> dict:
    url = url_or_path if str(url_or_path).startswith("http") else f"{API_BASE}{url_or_path}"
    for attempt in range(8):
        try:
            r = requests.put(url, headers=HEADERS, json=body, timeout=120)
        except _TRANSIENT_NET as e:
            time.sleep(min(2**attempt, 30))
            if attempt == 7:
                raise RuntimeError(f"PUT сеть/таймаут: {e}") from e
            continue
        if r.status_code == 429:
            time.sleep(2 ** min(attempt + 1, 5))
            continue
        if r.status_code == 412:
            err = r.json().get("errors", [{}])[0].get("error", "")
            raise RuntimeError(f"412: {err}")
        r.raise_for_status()
        return r.json()
    raise RuntimeError("PUT failed")


def parse_moment(s: str) -> datetime:
    s = (s or "").strip().replace(" ", "T", 1)
    return datetime.fromisoformat(s)


def parse_moment_bound(raw: str, *, end_of_day: bool) -> datetime:
    """YYYY-MM-DD или полная дата-время как у поля moment."""
    s = raw.strip()
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        base = datetime.strptime(s, "%Y-%m-%d")
        if end_of_day:
            return base.replace(hour=23, minute=59, second=59, microsecond=999999)
        return base.replace(hour=0, minute=0, second=0, microsecond=0)
    return parse_moment(s.replace(" ", "T", 1) if "T" not in s else s)


def resolve_moment_window(moment_from: str, moment_to: str) -> tuple[datetime | None, datetime | None]:
    a = (moment_from or "").strip()
    b = (moment_to or "").strip()
    if not a and not b:
        return None, None
    if not a or not b:
        raise RuntimeError("Задайте оба --moment-from и --moment-to или ни одного.")
    mf = parse_moment_bound(a, end_of_day=False)
    mt = parse_moment_bound(b, end_of_day=True)
    if mf > mt:
        raise RuntimeError("moment-from позже moment-to.")
    return mf, mt


def moment_in_window(mm: datetime, window: tuple[datetime | None, datetime | None]) -> bool:
    mf, mt = window
    if mf is None:
        return True
    return mf <= mm <= mt


def fetch_organizations() -> list[dict]:
    rows: list[dict] = []
    offset = 0
    while True:
        d = api_get("/entity/organization", {"limit": 100, "offset": offset})
        chunk = d.get("rows", [])
        rows.extend(chunk)
        if len(chunk) < 100:
            break
        offset += 100
        time.sleep(0.25)
    return rows


def resolve_organization_by_name(exact_name: str) -> dict:
    all_orgs = fetch_organizations()
    matches = [o for o in all_orgs if (o.get("name") or "") == exact_name]
    if not matches:
        raise RuntimeError(
            f'Организация с точным именем «{exact_name}» не найдена '
            f"(проверено записей: {len(all_orgs)})."
        )
    if len(matches) > 1:
        ids = ", ".join(m.get("id", "?") for m in matches[:10])
        raise RuntimeError(f"Неоднозначность: несколько организаций с именем «{exact_name}»: {ids}")
    return matches[0]


def organization_href(org: dict) -> str:
    meta = org.get("meta") or {}
    href = meta.get("href")
    if not href:
        raise RuntimeError("У организации нет meta.href")
    return href


def fetch_demands_for_organization(org_href: str, *, exclude_archived: bool) -> list[dict]:
    def pull(filter_str: str) -> list[dict]:
        rows: list[dict] = []
        offset = 0
        while True:
            d = api_get(
                "/entity/demand",
                {"limit": 100, "offset": offset, "filter": filter_str},
            )
            chunk = d.get("rows", [])
            rows.extend(chunk)
            if len(chunk) < 100:
                break
            offset += 100
            time.sleep(0.25)
        return rows

    base = f"organization={org_href}"
    if exclude_archived:
        try:
            return pull(f"{base};archived=false")
        except requests.exceptions.HTTPError as e:
            code = e.response.status_code if e.response is not None else 0
            if code == 412:
                return pull(base)
            raise
    return pull(base)


def fetch_demands_by_exact_name(exact_name: str) -> list[dict]:
    """Отгрузки с точным именем (для поиска «чужих» держателей номера)."""
    rows: list[dict] = []
    offset = 0
    filt = f"name={exact_name}"
    while True:
        d = api_get("/entity/demand", {"limit": 100, "offset": offset, "filter": filt})
        chunk = d.get("rows", [])
        rows.extend(chunk)
        if len(chunk) < 100:
            break
        offset += 100
        time.sleep(0.25)
    return rows


def build_id_to_name_index(
    demands_org: list[dict],
    planned_target_names: set[str],
    log_sink,
) -> dict[str, str]:
    """Снимок имён по орг-документам + точечные запросы по каждому целевому номеру (без скан всего аккаунта)."""
    id_to_name: dict[str, str] = {}
    for d in demands_org:
        i = d.get("id")
        if i:
            id_to_name[i] = (d.get("name") or "")
    names_sorted = sorted(planned_target_names)
    log_sink(f"Подготовка коллизий: запрос имён документов по filter=name= ({len(names_sorted)} уникальных)…")
    for j, nm in enumerate(names_sorted):
        time.sleep(PREFETCH_DELAY_S)
        chunk = fetch_demands_by_exact_name(nm)
        for r in chunk:
            i = r.get("id")
            if i:
                id_to_name[i] = (r.get("name") or "")
        if (j + 1) % 50 == 0:
            log_sink(f"  … prefetch {j + 1}/{len(names_sorted)}")
    return id_to_name


def split_anchor_name(anchor_name: str) -> tuple[str, int]:
    m = re.match(r"^(.+?)(\d+)\s*$", (anchor_name or "").strip())
    if not m:
        raise RuntimeError(
            f"Якорное имя «{anchor_name}» не разбирается на префикс + номер "
            '(ожидается вид вроде «ВА-18066»)'
        )
    prefix, num_s = m.group(1), m.group(2)
    return prefix, int(num_s)


def put_demand_name(demand_id: str, new_name: str, log) -> None:
    path = f"/entity/demand/{demand_id}"
    doc = api_get(path)
    cur = doc.get("name") or ""
    if cur == new_name:
        log.write(f"SKIP {demand_id}: уже «{new_name}»\n")
        log.flush()
        return
    body = {"meta": doc["meta"], "name": new_name}
    log.write(f"PUT {demand_id}: «{cur}» → «{new_name}»\n")
    log.flush()
    api_put(path, body)
    time.sleep(RATE_LIMIT_S)


def frees_name(
    target_name: str,
    keeper_id: str,
    *,
    id_to_name: dict[str, str],
    log,
    apply: bool,
) -> None:
    """Если имя занято документами с id != keeper_id — освободить (суффикс '-')."""
    holders = sorted(
        [did for did, nm in id_to_name.items() if nm == target_name and did != keeper_id]
    )
    for hid in holders:
        old = id_to_name[hid]
        new_nm = old + "-"
        while new_nm in set(id_to_name.values()):
            new_nm += "-"
        if apply:
            put_demand_name(hid, new_nm, log)
        else:
            log.write(
                f"[dry-run] освободить имя «{target_name}»: отгрузка {hid} «{old}» → «{new_nm}»\n"
            )
        id_to_name[hid] = new_nm


def main() -> None:
    parser = argparse.ArgumentParser(
        description="После якорной отгрузки — подряд ВА-n по времени документа (одна организация)."
    )
    parser.add_argument(
        "--organization-name",
        default='ООО "ВАН ПАК"',
        help="Точное имя организации в МойСклад",
    )
    parser.add_argument(
        "--anchor-name",
        default="ВА-18066",
        help="Якорный номер отгрузки (не переименовывается)",
    )
    parser.add_argument(
        "--expect-moment",
        default="",
        help='Ожидаемый moment якоря, например "2026-02-27 13:18:00" (пусто = не проверять)',
    )
    parser.add_argument(
        "--moment-from",
        default="",
        help='Начало окна по полю moment документа, напр. 2026-05-01 или "2026-05-01 00:00:00"',
    )
    parser.add_argument(
        "--moment-to",
        default="",
        help='Конец окна, напр. 2026-05-31 (конец дня) или полная метка времени',
    )
    parser.add_argument(
        "--exclude-archived",
        action="store_true",
        help="Только неархивные: filter archived=false (если API отклонит — только по организации)",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Только сверка: ожидаемые ВА-n vs факт в API, без записи и без prefetch по номерам",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Выполнить переименования (без этого — только отчёт)",
    )
    parser.add_argument(
        "--i-know",
        action="store_true",
        help="Подтверждение записи в API (обязательно вместе с --apply)",
    )
    args = parser.parse_args()

    if not TOKEN:
        print("Задайте MS_TOKEN", file=sys.stderr)
        sys.exit(1)

    if args.apply and not args.i_know:
        print("При --apply укажите также --i-know", file=sys.stderr)
        sys.exit(1)

    if args.verify_only and args.apply:
        print("Нельзя одновременно --verify-only и --apply", file=sys.stderr)
        sys.exit(1)

    out_dir = os.environ.get("MS_OUTPUT_DIR", "/tmp").strip() or "/tmp"
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(out_dir, f"ms_renumber_demands_{stamp}.log")
    tsv_path = os.path.join(out_dir, f"ms_renumber_demands_{stamp}.tsv")

    org = resolve_organization_by_name(args.organization_name.strip())
    org_href = organization_href(org)
    demands_org = fetch_demands_for_organization(org_href, exclude_archived=args.exclude_archived)
    prefix, anchor_num = split_anchor_name(args.anchor_name.strip())

    anchors = [
        d
        for d in demands_org
        if (d.get("name") or "").strip() == args.anchor_name.strip() and d.get("id")
    ]
    if not anchors:
        print(
            f"Якорь «{args.anchor_name}» среди отгрузок организации не найден "
            f"(всего отгрузок с фильтром: {len(demands_org)}).",
            file=sys.stderr,
        )
        sys.exit(2)
    if len(anchors) > 1:
        ids = ", ".join(a["id"] for a in anchors[:20])
        print(
            f"Несколько отгрузок с именем «{args.anchor_name}»: задайте уникальный якорь.\nids: {ids}",
            file=sys.stderr,
        )
        sys.exit(2)

    anchor_doc = anchors[0]
    anchor_moment = parse_moment(anchor_doc["moment"])

    exp = args.expect_moment.strip()
    if exp:
        try:
            em = parse_moment(exp.replace(" ", "T", 1) if "T" not in exp else exp)
        except ValueError:
            print(f"Не удалось распарсить --expect-moment: {exp!r}", file=sys.stderr)
            sys.exit(1)
        anchor_ms = anchor_doc.get("moment", "")
        try:
            am = parse_moment(anchor_ms)
        except ValueError:
            print(f"Не удалось распарсить moment якоря из API: {anchor_ms!r}", file=sys.stderr)
            sys.exit(1)
        if am.replace(microsecond=0) != em.replace(microsecond=0):
            print(
                f"Момент якоря из API ({anchor_ms!r}) не совпадает с --expect-moment ({exp!r}).",
                file=sys.stderr,
            )
            sys.exit(2)

    try:
        moment_window = resolve_moment_window(args.moment_from, args.moment_to)
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        sys.exit(2)

    to_fix: list[dict] = []
    for d in demands_org:
        if not d.get("id"):
            continue
        if d["id"] == anchor_doc["id"]:
            continue
        try:
            m = parse_moment(d["moment"])
        except (KeyError, ValueError):
            continue
        if m > anchor_moment:
            to_fix.append(d)

    to_fix.sort(key=lambda x: (parse_moment(x["moment"]), x.get("id") or ""))

    plans_full: list[tuple[str, str, str, str]] = []
    for i, d in enumerate(to_fix):
        new_name = f"{prefix}{anchor_num + i + 1}"
        plans_full.append(
            (
                d["id"],
                d["moment"],
                d.get("name") or "",
                new_name,
            )
        )

    print(f'Организация: «{args.organization_name}» → id={org.get("id")}', flush=True)
    print(f'Якорь: {args.anchor_name} @ {anchor_doc.get("moment")} id={anchor_doc["id"]}', flush=True)
    arch_note = (
        "только неархив (при 412 — все по организации)"
        if args.exclude_archived
        else "все по организации"
    )
    n_put_window = sum(
        1
        for _, mom, _, _ in plans_full
        if moment_in_window(parse_moment(mom), moment_window)
    )
    win_human = (
        f"{args.moment_from} … {args.moment_to}"
        if moment_window[0] is not None
        else "(без ограничения)"
    )
    print(
        f"После якоря полная цепочка: {len(plans_full)} ({arch_note}); "
        f"окно moment для PUT: {win_human} → документов {n_put_window}",
        flush=True,
    )

    if args.verify_only:
        bad: list[tuple[str, str, str, str]] = []
        scoped = 0
        for i, d in enumerate(to_fix):
            mm = parse_moment(d["moment"])
            if not moment_in_window(mm, moment_window):
                continue
            scoped += 1
            exp = f"{prefix}{anchor_num + i + 1}"
            got = (d.get("name") or "").strip()
            did = d.get("id") or ""
            mom = d.get("moment") or ""
            if got != exp:
                bad.append((did, mom, got, exp))
        rep = os.path.join(out_dir, f"ms_renumber_verify_{stamp}.tsv")
        with open(rep, "w", encoding="utf-8", newline="") as vf:
            wv = csv.writer(vf, delimiter="\t", lineterminator="\n")
            wv.writerow(["demand_id", "moment", "name_actual", "name_expected"])
            for row in bad:
                wv.writerow(row[:4])
        win_lab = (
            "полная цепочка после якоря"
            if moment_window[0] is None
            else f"окно moment {args.moment_from!r} … {args.moment_to!r}"
        )
        print(f"Сверка ({win_lab}): проверено {scoped}, несовпадений {len(bad)}. Отчёт: {rep}", flush=True)
        if bad:
            for row in bad[:25]:
                print(f"  {row[0]} … «{row[2]}» → ожид. «{row[3]}»  moment={row[1]}", flush=True)
            if len(bad) > 25:
                print(f"  … и ещё {len(bad) - 25}", flush=True)
            sys.exit(3)
        print("Имена в проверенном объёме совпадают с ожидаемой нумерацией.", flush=True)
        sys.exit(0)

    if not plans_full:
        print("Нечего переименовывать.", flush=True)
        sys.exit(0)

    planned_names = {p[3] for p in plans_full}

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"Режим: {mode}. Журнал: {log_path}", flush=True)

    def slog(msg: str) -> None:
        print(msg, flush=True)

    id_to_name = build_id_to_name_index(demands_org, planned_names, slog)

    with open(log_path, "w", encoding="utf-8") as log, open(
        tsv_path, "w", encoding="utf-8", newline=""
    ) as tsvf:
        log.write(f"{mode} organization={args.organization_name!r} anchor={args.anchor_name!r}\n")
        if moment_window[0] is not None:
            log.write(f"moment window PUT: {args.moment_from!r} … {args.moment_to!r}\n")
        w = csv.writer(tsvf, delimiter="\t", lineterminator="\n")
        w.writerow(["demand_id", "moment", "old_name", "new_name"])

        for did, mom, old_nm, new_nm in plans_full:
            mm = parse_moment(mom)
            in_win = moment_in_window(mm, moment_window)
            frees_name(new_nm, did, id_to_name=id_to_name, log=log, apply=args.apply)
            if not in_win:
                continue
            cur = id_to_name.get(did, old_nm)
            if args.apply:
                put_demand_name(did, new_nm, log)
            else:
                log.write(
                    f"[dry-run] целевое переименование: {did} moment={mom} «{cur}» → «{new_nm}»\n"
                )
            id_to_name[did] = new_nm
            w.writerow([did, mom, old_nm, new_nm])

    if moment_window[0] is None:
        print(f"TSV: {tsv_path}", flush=True)
    else:
        print(f"TSV (только строки окна PUT): {tsv_path}", flush=True)
    if not args.apply:
        print("Был dry-run. Для записи: добавьте --apply --i-know", flush=True)


if __name__ == "__main__":
    main()
