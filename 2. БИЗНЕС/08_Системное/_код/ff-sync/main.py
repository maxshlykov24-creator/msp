from apscheduler.schedulers.blocking import BlockingScheduler

from db import init_db, tables
from ms import ensure_all_attrs, ensure_projects, ensure_services


def job_agents():
    from agents_sync import run

    try:
        run()
    except Exception as exc:
        print("агенты ошибка:", exc)


def job_rota():
    """Шаг очереди контрагентов: один клиент за раз, круг за 30 минут."""
    import sync_rota

    try:
        sync_rota.step()
    except BlockingIOError:
        pass
    except Exception as exc:
        print("очередь ошибка:", exc)


def job_supplies():
    """Поставки WB в фоне. Раньше это делала страница «Заказы» на каждый клик."""
    import supply_flow

    try:
        supply_flow.sync_open()
    except Exception as exc:
        print("поставки ошибка:", exc)


def job_live():
    """Актуальные статусы каждые пять минут, без записи на маркетплейсы."""
    import night_check
    try:
        night_check.run_live()
    except BlockingIOError:
        pass
    except Exception as exc:
        print("оперативная сверка:", type(exc).__name__, flush=True)


def job_night():
    """Ночная сверка того, что ждёт отгрузки и уже уехало."""
    import night_check

    try:
        night_check.run(blocking=False)
    except BlockingIOError:
        print("ночная сверка: занято другим проходом")
    except Exception as exc:
        print("ночная сверка ошибка:", exc)


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


def job_stock():
    """Свои кабинеты: включение конфигурацией только после проверенного пилота."""
    import os
    from stock_push import own_path, own_config, run_own

    try:
        if not os.path.exists(own_path()) or not own_config().get("enabled"):
            return
        report = run_own("sync")
        print("свои остатки:", [(r["id"], r["status"]) for r in report.get("targets", [])], flush=True)
    except BlockingIOError:
        pass
    except Exception as exc:
        print("свои остатки ошибка:", type(exc).__name__, flush=True)


print("ff-sync: создаю базу")
path = init_db()
print("ff-sync: база %s, таблицы: %s" % (path, ", ".join(tables())))
try:
    ensure_all_attrs()
    ensure_projects()
    ensure_services()
except Exception as exc:
    print("поля, проекты и услуги МойСклад: %s" % exc)

import sync_rota

step = sync_rota.step_minutes()
print(
    "ff-sync: клиенты каждые 10 мин, очередь контрагентов шаг %s мин (круг %s мин), "
    "поставки каждые 5 мин, приёмка каждые 10 мин, ночная сверка в 03:30, свои остатки каждые 5 мин при включённой конфигурации"
    % (step, sync_rota.CIRCLE_MINUTES)
)
job_agents()
sched = BlockingScheduler(timezone="Europe/Moscow")
sched.add_job(job_agents, "interval", minutes=10, id="agents")
sched.add_job(job_rota, "interval", minutes=step, id="rota")
sched.add_job(job_supplies, "interval", minutes=5, id="supplies")
sched.add_job(job_live, "interval", minutes=5, id="live_statuses", max_instances=1, coalesce=True)
sched.add_job(job_accept, "interval", minutes=10, id="accept")
sched.add_job(job_night, "cron", hour=3, minute=30, id="night")
sched.add_job(job_stock, "interval", minutes=5, id="own_stock", max_instances=1, coalesce=True)
sched.start()
