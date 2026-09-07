from apscheduler.schedulers.blocking import BlockingScheduler

from db import init_db, tables
from ms import ensure_all_attrs, ensure_projects, ensure_services


def job_agents():
    from agents_sync import run

    try:
        run()
    except Exception as exc:
        print("агенты ошибка:", exc)


def job_orders():
    from orders_pull import run as run_orders
    from shipments_pull import run as run_ships

    try:
        run_orders()
    except Exception as exc:
        print("заказы ошибка:", exc)
    try:
        run_ships(days=14)
    except Exception as exc:
        print("отгрузки ошибка:", exc)


def job_accept():
    from accept import run as run_accept

    try:
        res = run_accept(blocking=False)
        if res["accepted"]:
            print("приёмка: встало на счётчик партий %s" % len(res["accepted"]))
    except BlockingIOError:
        pass
    except Exception as exc:
        print("приёмка ошибка:", exc)


print("ff-sync: создаю базу")
path = init_db()
print("ff-sync: база %s, таблицы: %s" % (path, ", ".join(tables())))
try:
    ensure_all_attrs()
    ensure_projects()
    ensure_services()
except Exception as exc:
    print("поля, проекты и услуги МойСклад: %s" % exc)

print("ff-sync: клиенты каждые 10 мин, заказы и отгрузки каждые 15 мин, приёмка каждые 10 мин")
job_agents()
sched = BlockingScheduler(timezone="Europe/Moscow")
sched.add_job(job_agents, "interval", minutes=10, id="agents")
sched.add_job(job_orders, "interval", minutes=15, id="orders")
sched.add_job(job_accept, "interval", minutes=10, id="accept")
sched.start()
