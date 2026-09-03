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
    from orders_pull import run

    try:
        run()
    except Exception as exc:
        print("заказы ошибка:", exc)


print("ff-sync: создаю базу")
path = init_db()
print("ff-sync: база %s, таблицы: %s" % (path, ", ".join(tables())))
try:
    ensure_all_attrs()
    ensure_projects()
    ensure_services()
except Exception as exc:
    print("поля, проекты и услуги МойСклад: %s" % exc)

print("ff-sync: клиенты каждые 10 мин, заказы каждые 15 мин")
job_agents()
sched = BlockingScheduler(timezone="Europe/Moscow")
sched.add_job(job_agents, "interval", minutes=10, id="agents")
sched.add_job(job_orders, "interval", minutes=15, id="orders")
sched.start()
