"""Очередь контрагентов: полный круг за 30 минут, по одному за раз.

Раньше воркер каждые 15 минут шёл по всем кабинетам сразу. Проход по 17 тысячам
отправлений в это окно не влезал, планировщик писал «maximum number of running
instances reached» и пропускал запуск: 18.09 кабинет ИП Вахрушев не обновлялся
16 часов, в панели висело 3 новых задания из 7.

Теперь шаг маленький: за раз обновляется один контрагент — тот, кого не трогали
дольше всех. Шаг короткий, лог понятный, никто не голодает. Интервал шага
считается от числа контрагентов, поэтому круг остаётся тридцатиминутным и когда
клиентов станет больше.
"""

from datetime import datetime, timedelta, timezone

from db import get_setting, init_db, list_cabinets, list_clients, set_setting

CIRCLE_MINUTES = 30
RETRY_MINUTES = 5
KEY = "rota:client:%s"


def now():
    return datetime.now(timezone.utc)


def stamp(when):
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(text):
    try:
        return datetime.strptime(str(text or ""), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def queue():
    """Контрагенты с живым кабинетом, первым — тот, кого не трогали дольше всех."""
    live = {c["client_id"] for c in list_cabinets() if c["active"] and c["token"]}
    rows = [c for c in list_clients() if c["id"] in live]
    floor = now() - timedelta(days=1)
    return sorted(rows, key=lambda c: parse(get_setting(KEY % c["id"])) or floor)


def step_minutes():
    """Шаг очереди: круг делим на число контрагентов, но не мельче минуты."""
    n = len(queue()) or 1
    return max(1, CIRCLE_MINUTES // n)


def due(client, gap_minutes=CIRCLE_MINUTES):
    was = parse(get_setting(KEY % client["id"]))
    if not was:
        return True
    return now() - was >= timedelta(minutes=gap_minutes)


def sync_client(client_id, days=14):
    """Заказы и отправления одного контрагента.

    Метку времени ставим только если отправления реально забрали: иначе
    контрагент, чей проход столкнулся с замком, считался бы обновлённым и ждал
    следующего круга полчаса.
    """
    from orders_pull import run as run_orders
    from shipments_pull import run as run_ships

    notes = []
    done = False
    try:
        run_orders(client_id=client_id, blocking=False)
    except Exception as exc:
        notes.append("заказы: %s" % exc)
    try:
        res = run_ships(days=days, blocking=False, client_id=client_id)
        notes.extend(res.get("notes") or [])
        done = True
    except Exception as exc:
        notes.append("отправления: %s" % exc)
    if done:
        set_setting(KEY % int(client_id), stamp(now()))
    else:
        # проход не удался: метку сдвигаем так, чтобы вернуться к клиенту через
        # RETRY_MINUTES, но очередь тем же шагом ушла к следующему. Совсем без
        # метки первый в очереди зациклился бы на себе и заморозил остальных
        set_setting(KEY % int(client_id), stamp(now() - timedelta(minutes=CIRCLE_MINUTES - RETRY_MINUTES)))
    return notes


def step():
    """Один шаг очереди. Никто не просрочен — ничего не делаем.

    Свой замок здесь не берём: его берут сами проходы заказов и отправлений, а
    два замка на один файл в одном процессе конфликтуют между собой.
    """
    init_db()
    rows = queue()
    if not rows:
        return {"client": None, "notes": []}
    client = rows[0]
    if not due(client):
        return {"client": None, "notes": []}
    notes = sync_client(client["id"])
    print("очередь: %s обновлён" % client["name"])
    for note in notes:
        print("  %s" % note)
    return {"client": client["name"], "notes": notes}


if __name__ == "__main__":
    print(step())
