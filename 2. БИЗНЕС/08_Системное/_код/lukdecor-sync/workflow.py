"""Демо-журнал в своей SQLite. Ни одного вызова записи во внешние системы."""
import contextlib
import json
import sqlite3
import time
import uuid
from pathlib import Path

from ms_read import READY, ReadError, decode_sticker, fetch_label


class Workflow:
    def __init__(self, db_path, ms):
        self.path, self.ms = str(db_path), ms
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS snapshot (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS waves (id TEXT PRIMARY KEY, employee TEXT, cart TEXT, store TEXT, box TEXT, created REAL);
                CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, wave TEXT, state TEXT, station TEXT, cell_ok INTEGER DEFAULT 0, pdf BLOB, qr TEXT, sticker_ok INTEGER DEFAULT 0);
                CREATE UNIQUE INDEX IF NOT EXISTS pending_station ON jobs(station) WHERE station IS NOT NULL AND state='printing';
                CREATE UNIQUE INDEX IF NOT EXISTS unique_sticker ON jobs(qr) WHERE qr IS NOT NULL;
                CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, at REAL, employee TEXT, station TEXT, job TEXT, action TEXT, detail TEXT);
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
            ''')

    @contextlib.contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=45)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def event(db, actor, job, action, detail=""):
        db.execute("INSERT INTO events(at,employee,station,job,action,detail) VALUES(?,?,?,?,?,?)",
                   (time.time(), actor["employee"], actor["station"], job, action, detail))

    def refresh(self, actor):
        snapshot = self.ms.collect()
        with self.db() as db:
            # Не меняем состав активной волны при повторном обновлении.
            for o in snapshot["orders"]:
                if not db.execute("SELECT 1 FROM jobs WHERE id=?", (o["id"],)).fetchone():
                    db.execute("INSERT OR REPLACE INTO snapshot VALUES(?,?)", (o["id"], json.dumps(o, ensure_ascii=False)))
            db.execute("INSERT OR REPLACE INTO settings VALUES('updated',?)", (str(time.time()),))
            self.event(db, actor, "", "refresh", f'{len(snapshot["orders"])} заданий, {snapshot["scanned_orders"]} заказов просмотрено')
        return snapshot

    def view(self):
        with self.db() as db:
            orders = []
            for row in db.execute("SELECT s.payload,j.wave,j.state,j.station,j.sticker_ok FROM snapshot s LEFT JOIN jobs j ON s.id=j.id"):
                item = json.loads(row["payload"])
                item.pop("label_url", None)
                item.update({k: row[k] for k in ("wave", "state", "station", "sticker_ok")})
                orders.append(item)
            updated = db.execute("SELECT value FROM settings WHERE key='updated'").fetchone()
            return {"orders": orders, "waves": [dict(r) for r in db.execute("SELECT * FROM waves ORDER BY created DESC")],
                    "events": [dict(r) for r in db.execute("SELECT * FROM events ORDER BY id DESC LIMIT 100")],
                    "updated": float(updated[0]) if updated else None,
                    "demo": True, "status_source": "МойСклад через TopSeller, возможна задержка отмен",
                    "supply_source": "Демо-короб. Реальная поставка WB и дедлайн не получены"}

    def wave(self, actor, ids, cart):
        ids = list(dict.fromkeys(ids))
        if not ids or not cart.strip():
            raise ReadError("Выбери задания и отсканируй тележку")
        with self.db() as db:
            orders = []
            for oid in ids:
                row = db.execute("SELECT payload FROM snapshot WHERE id=?", (oid,)).fetchone()
                if not row or db.execute("SELECT 1 FROM jobs WHERE id=?", (oid,)).fetchone():
                    raise ReadError("Задание уже в волне или отсутствует в снимке")
                order = json.loads(row[0])
                if self.ms.status(oid) != READY:
                    raise ReadError("Статус задания изменился. Обнови снимок")
                orders.append(order)
            if len({o["store"] for o in orders}) != 1:
                raise ReadError("Одна волна должна относиться к одному складу")
            wid = uuid.uuid4().hex[:8]
            box = "DEMO-BOX-" + wid
            db.execute("INSERT INTO waves VALUES(?,?,?,?,?,?)", (wid, actor["employee"], cart, orders[0]["store"], box, time.time()))
            for o in orders:
                db.execute("INSERT INTO jobs(id,wave,state) VALUES(?,?,'picking')", (o["id"], wid))
            self.event(db, actor, wid, "wave", "Тележка " + cart + "; " + box)
            return {"wave": wid, "box": box}

    def action(self, actor, oid, action, code="", target=""):
        # Все изменения задания и закрепление станции атомарны, в т.ч. при двух HTTP-запросах.
        with self.db() as db:
            row = db.execute("SELECT j.*,s.payload,w.employee,w.box FROM jobs j JOIN snapshot s ON j.id=s.id JOIN waves w ON j.wave=w.id WHERE j.id=?", (oid,)).fetchone()
            if not row:
                raise ReadError("Задание не найдено в волне")
            o, j = json.loads(row["payload"]), dict(row)
            if action in ("cell", "pick", "shortage") and j["employee"] != actor["employee"]:
                raise ReadError("Волна закреплена за другим сборщиком")
            if action in ("print", "sticker", "box"):
                if j["state"] in ("packed", "return_pending", "returned", "shortage"):
                    raise ReadError("Текущий этап задания не позволяет эту операцию")
                if j["station"] and j["station"] != actor["station"]:
                    raise ReadError("Задание закреплено за другой станцией. Требуется явная передача")
                # Ошибка связи блокирует действие. Любой статус кроме разрешённого требует возврата.
                if self.ms.status(oid) != READY:
                    db.execute("UPDATE jobs SET state='return_pending' WHERE id=?", (oid,))
                    self.event(db, actor, oid, "return_pending", "Статус МС изменился, отложить товар для проверки")
                    return {"message": "Статус изменился. Не класть в короб. Требуется возврат и решение склада.", "blocked": True}
            if action == "cell":
                if j["state"] != "picking" or not o["cell_verified"] or code != o["cell"]:
                    raise ReadError("Ячейка не совпадает или физическое размещение не подтверждено")
                db.execute("UPDATE jobs SET cell_ok=1 WHERE id=?", (oid,))
            elif action == "pick":
                if j["state"] != "picking" or code not in o["barcodes"]:
                    raise ReadError("Не тот товар или отбор уже завершён")
                if o["cell_verified"] and not j["cell_ok"]:
                    raise ReadError("Сначала отсканируй ячейку")
                db.execute("UPDATE jobs SET state='picked' WHERE id=?", (oid,))
            elif action == "shortage":
                if j["state"] != "picking":
                    raise ReadError("Дефицит можно отметить во время отбора")
                if not target.strip():
                    raise ReadError("Укажи ответственного за дефицит")
                db.execute("UPDATE jobs SET state='shortage' WHERE id=?", (oid,))
                self.event(db, actor, oid, "shortage_owner", target)
            elif action == "print":
                if j["state"] not in ("picked", "printing") or code not in o["barcodes"]:
                    raise ReadError("Сначала отбери и отсканируй нужный товар")
                other = db.execute("SELECT id FROM jobs WHERE station=? AND state='printing' AND id<>?", (actor["station"], oid)).fetchone()
                if other:
                    raise ReadError("Сначала закрой неподтверждённый стикер этой станции")
                if j["pdf"]:
                    self.event(db, actor, oid, "reprint", "Тот же PDF и то же задание")
                    return {"message": "Повторная печать того же стикера", "pdf": "/api/label/" + oid}
                pdf = fetch_label(o["label_url"])
                qr, mm = decode_sticker(pdf)
                if not (abs(mm[0] - 58) < 2 and abs(mm[1] - 40) < 2):
                    raise ReadError("Размер оригинального стикера отличается от 58 × 40 мм")
                # Повторная проверка после загрузки PDF.
                if not self.ms.verify_label(oid, qr):
                    raise ReadError("QR не совпал с полем ШККОД128 заказа или статус изменился")
                if db.execute('SELECT 1 FROM jobs WHERE qr=? AND id<>?', (qr, oid)).fetchone():
                    raise ReadError('Этот стикер уже связан с другим заданием')
                db.execute("UPDATE jobs SET state='printing',station=?,pdf=?,qr=? WHERE id=?", (actor["station"], pdf, qr, oid))
                self.event(db, actor, oid, "print", "Оригинальный PDF; открытие ещё не доказывает физическую печать")
                return {"message": "Оригинальный стикер подготовлен. Открой PDF и напечатай вручную.", "pdf": "/api/label/" + oid}
            elif action == "sticker":
                if j["state"] != "printing" or not j["qr"] or code != j["qr"]:
                    raise ReadError("Стикер не относится к этому заданию")
                db.execute("UPDATE jobs SET sticker_ok=1 WHERE id=?", (oid,))
            elif action == "box":
                if j["state"] != "printing" or not j["sticker_ok"] or code != j["box"]:
                    raise ReadError("Сначала проверь стикер, затем отсканируй свой демо-короб")
                db.execute("UPDATE jobs SET state='packed' WHERE id=?", (oid,))
            elif action == "transfer":
                if j["station"] != actor["station"] or j["state"] != "printing" or not target.strip():
                    raise ReadError("Передать можно только незакрытое задание своей станции")
                if db.execute("SELECT 1 FROM jobs WHERE station=? AND state='printing'", (target,)).fetchone():
                    raise ReadError("У целевой станции уже есть незакрытый стикер")
                db.execute("UPDATE jobs SET station=? WHERE id=?", (target, oid))
            elif action == "return":
                if j["state"] != "return_pending" or code not in o["barcodes"] or not target.strip():
                    raise ReadError("Скан товара и ячейка возврата обязательны")
                db.execute("UPDATE jobs SET state='returned' WHERE id=?", (oid,))
                self.event(db, actor, oid, "return_cell", target)
            else:
                raise ReadError("Неизвестная операция")
            self.event(db, actor, oid, action, "Проверка скана пройдена" if code else target)
            return {"message": "Сохранено в демо-журнале"}

    def label(self, actor, oid):
        with self.db() as db:
            row = db.execute("SELECT pdf,station,state FROM jobs WHERE id=?", (oid,)).fetchone()
            if not row or row["station"] != actor["station"] or row["state"] != "printing":
                raise ReadError("Стикер недоступен этой станции")
            if self.ms.status(oid) != READY:
                raise ReadError("Статус изменился. PDF заблокирован, проверь задание")
            return bytes(row["pdf"])
