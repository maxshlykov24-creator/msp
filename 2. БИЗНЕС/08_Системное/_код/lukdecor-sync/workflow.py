"""Журнал сборки в своей SQLite. Ни одного вызова записи во внешние системы."""
import contextlib
import json
import shutil
import sqlite3
import time
import uuid
from pathlib import Path

from ms_read import READY, ReadError, decode_sticker, fetch_label

SHIFT = "Смена (очередь Разобрать)"


class Workflow:
    def __init__(self, db_path, ms):
        self.path, self.ms = str(db_path), ms
        path = Path(self.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._backup_if_needed(path)
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS snapshot (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS waves (id TEXT PRIMARY KEY, employee TEXT, cart TEXT, store TEXT, box TEXT, created REAL);
                CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, wave TEXT, state TEXT, station TEXT, cell_ok INTEGER DEFAULT 0, pdf BLOB, qr TEXT, sticker_ok INTEGER DEFAULT 0);
                CREATE UNIQUE INDEX IF NOT EXISTS pending_station ON jobs(station) WHERE station IS NOT NULL AND state='printing';
                CREATE UNIQUE INDEX IF NOT EXISTS unique_sticker ON jobs(qr) WHERE qr IS NOT NULL;
                CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, at REAL, employee TEXT, station TEXT, job TEXT, action TEXT, detail TEXT);
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS scan_requests (id TEXT PRIMARY KEY, result TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS station_bind (station TEXT PRIMARY KEY, wave TEXT NOT NULL);
            ''')
            self._migrate(db)

    def _backup_if_needed(self, path):
        if not path.exists() or path.stat().st_size == 0:
            return
        con = sqlite3.connect(path)
        try:
            cols = [r[1] for r in con.execute("PRAGMA table_info(waves)")]
        except sqlite3.OperationalError:
            cols = []
        finally:
            con.close()
        if cols and "num" not in cols:
            bak = path.with_name(path.name + ".before-wave-num")
            if not bak.exists():
                shutil.copy2(path, bak)

    def _migrate(self, db):
        cols = {r["name"] for r in db.execute("PRAGMA table_info(waves)")}
        if "num" not in cols:
            db.execute("ALTER TABLE waves ADD COLUMN num INTEGER")
        if "created_by" not in cols:
            db.execute("ALTER TABLE waves ADD COLUMN created_by TEXT")
        pending = db.execute("SELECT id FROM waves WHERE num IS NULL ORDER BY created, id").fetchall()
        base = db.execute("SELECT COALESCE(MAX(num), 0) FROM waves").fetchone()[0]
        for offset, row in enumerate(pending, 1):
            db.execute("UPDATE waves SET num=? WHERE id=? AND num IS NULL", (base + offset, row["id"]))
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS wave_num ON waves(num) WHERE num IS NOT NULL")
        job_cols = {r["name"] for r in db.execute("PRAGMA table_info(jobs)")}
        if "note" not in job_cols:
            db.execute("ALTER TABLE jobs ADD COLUMN note TEXT")

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

    @staticmethod
    def _cached(db, request_id):
        if not request_id:
            return None
        row = db.execute("SELECT result FROM scan_requests WHERE id=?", (request_id,)).fetchone()
        return json.loads(row["result"]) if row else None

    @staticmethod
    def _store(db, request_id, result):
        if request_id:
            db.execute("INSERT INTO scan_requests(id, result) VALUES(?,?)",
                       (request_id, json.dumps(result, ensure_ascii=False)))
        return result

    def refresh(self, actor):
        snapshot = self.ms.collect()
        with self.db() as db:
            # Не меняем состав активной волны при повторном обновлении.
            for o in snapshot["orders"]:
                if not db.execute("SELECT 1 FROM jobs WHERE id=?", (o["id"],)).fetchone():
                    db.execute("INSERT OR REPLACE INTO snapshot VALUES(?,?)", (o["id"], json.dumps(o, ensure_ascii=False)))
            db.execute("INSERT OR REPLACE INTO settings VALUES('updated',?)", (str(time.time()),))
            db.execute("INSERT OR REPLACE INTO settings VALUES('current_ids',?)", (json.dumps([o['id'] for o in snapshot['orders']]),))
            db.execute("INSERT OR REPLACE INTO settings VALUES('ekb_skipped',?)", (str(snapshot.get("ekb_skipped", 0)),))
            db.execute("INSERT OR REPLACE INTO settings VALUES('scanned',?)", (str(snapshot.get("scanned_orders", 0)),))
            self.event(db, actor, "", "refresh", f'{len(snapshot["orders"])} заданий, {snapshot["scanned_orders"]} заказов просмотрено')
        return snapshot

    def _setting(self, db, key, default=None):
        row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def view(self, actor=None):
        with self.db() as db:
            orders = []
            current = self._setting(db, "current_ids")
            current_ids = set(json.loads(current)) if current else None
            waves = [dict(r) for r in db.execute("SELECT * FROM waves ORDER BY created DESC")]
            by_id = {w["id"]: w for w in waves}
            for row in db.execute("SELECT s.payload,j.wave,j.state,j.station,j.sticker_ok,j.note FROM snapshot s LEFT JOIN jobs j ON s.id=j.id"):
                item = json.loads(row["payload"])
                if current_ids is not None and item['id'] not in current_ids and not row['wave']:
                    continue
                item.pop("label_url", None)
                item.update({k: row[k] for k in ("wave", "state", "station", "sticker_ok", "note")})
                wave = by_id.get(row["wave"])
                if wave:
                    item["picker"] = wave.get("employee") or ""
                    item["wave_num"] = wave.get("num")
                    item["box"] = wave.get("box")
                    item["box_label"] = f"Короб {wave['num']}" if wave.get("num") else wave.get("box")
                item.setdefault("brand", "")
                item.setdefault("color", "")
                orders.append(item)
            for wave in waves:
                wave["box_label"] = f"Короб {wave['num']}" if wave.get("num") else wave.get("box")
            updated = self._setting(db, "updated")
            accepted = None
            if actor and actor.get("role") == "station":
                bind = db.execute("SELECT wave FROM station_bind WHERE station=?", (actor["station"],)).fetchone()
                accepted = bind["wave"] if bind else None
                if accepted:
                    orders = [o for o in orders if o.get("wave") == accepted]
                    waves = [w for w in waves if w["id"] == accepted]
                else:
                    orders, waves = [], []
            elif actor and actor.get("role") == "picker":
                mine = {w["id"] for w in waves if w.get("employee") == actor["employee"]}
                orders = [o for o in orders if o.get("wave") in mine]
                waves = [w for w in waves if not w.get("employee") or w.get("employee") == actor["employee"]]
            ekb = self._setting(db, "ekb_skipped", "0")
            scanned = self._setting(db, "scanned", "0")
            return {
                "orders": orders,
                "waves": waves,
                "events": [dict(r) for r in db.execute("SELECT * FROM events ORDER BY id DESC LIMIT 100")],
                "updated": float(updated) if updated else None,
                "demo": True,
                "sample_limited": True,
                "ekb_skipped": int(ekb or 0),
                "scanned_orders": int(scanned or 0),
                "accepted_wave": accepted,
                "status_source": "МойСклад через TopSeller, возможна задержка отмен",
                "supply_source": "Короб условный. Реальная поставка WB и дедлайн не получены",
            }

    def wave(self, actor, ids, cart, picker=""):
        ids = list(dict.fromkeys(ids))
        if not ids or not cart.strip():
            raise ReadError("Выбери задания и отсканируй тележку")
        role = actor.get("role") or ""
        owner = (picker or "").strip()
        if not owner:
            if role == "shift":
                owner = ""
            else:
                owner = actor["employee"]
        with self.db() as db:
            orders = []
            for oid in ids:
                row = db.execute("SELECT payload FROM snapshot WHERE id=?", (oid,)).fetchone()
                if not row or db.execute("SELECT 1 FROM jobs WHERE id=?", (oid,)).fetchone():
                    raise ReadError("Задание уже в волне или отсутствует в снимке")
                order = json.loads(row[0])
                if order.get("store") == "Склад Екатеринбург":
                    raise ReadError("Екатеринбург не входит в волну Лобни")
                if self.ms.status(oid) != READY:
                    raise ReadError("Статус задания изменился. Обнови выборку")
                orders.append(order)
            if len({o["store"] for o in orders}) != 1:
                raise ReadError("Одна волна должна относиться к одному складу")
            num = db.execute("SELECT COALESCE(MAX(num),0)+1 FROM waves").fetchone()[0]
            wid = uuid.uuid4().hex[:8]
            box = f"KOROB-{num}"
            db.execute(
                "INSERT INTO waves(id, employee, cart, store, box, created, num, created_by) VALUES(?,?,?,?,?,?,?,?)",
                (wid, owner, cart.strip(), orders[0]["store"], box, time.time(), num, actor["employee"]),
            )
            for o in orders:
                db.execute("INSERT INTO jobs(id,wave,state) VALUES(?,?,'picking')", (o["id"], wid))
            self.event(db, actor, wid, "wave", f"Волна {num}. Тележка {cart.strip()}. Сборщик {owner or 'не назначен'}")
            return {"wave": wid, "box": box, "num": num, "label": f"Волна {num}", "box_label": f"Короб {num}"}

    def claim(self, actor, wave_id, request_id=""):
        with self.db() as db:
            hit = self._cached(db, request_id)
            if hit:
                return hit
            if (actor.get("role") or "") not in ("", "picker"):
                raise ReadError("Волну берёт сборщик")
            row = db.execute("SELECT * FROM waves WHERE id=?", (wave_id,)).fetchone()
            if not row:
                raise ReadError("Волна не найдена")
            if row["employee"] and row["employee"] != actor["employee"]:
                raise ReadError("Волна закреплена за другим сборщиком")
            if not row["employee"]:
                db.execute("UPDATE waves SET employee=? WHERE id=?", (actor["employee"], wave_id))
            self.event(db, actor, wave_id, "claim", actor["employee"])
            return self._store(db, request_id, {"wave": wave_id, "num": row["num"], "label": f"Волна {row['num']}"})

    def accept_cart(self, actor, cart, request_id=""):
        with self.db() as db:
            hit = self._cached(db, request_id)
            if hit:
                return hit
            if (actor.get("role") or "") not in ("", "station"):
                raise ReadError("Тележку принимает станция")
            pending = db.execute("SELECT wave FROM jobs WHERE station=? AND state='printing'", (actor["station"],)).fetchone()
            row = db.execute("SELECT * FROM waves WHERE cart=? ORDER BY created DESC LIMIT 1", (cart.strip(),)).fetchone()
            if not row:
                raise ReadError("Тележка не найдена")
            if pending and pending["wave"] != row["id"]:
                raise ReadError("Сначала закрой стикер текущей волны")
            db.execute(
                "INSERT INTO station_bind(station, wave) VALUES(?,?) ON CONFLICT(station) DO UPDATE SET wave=excluded.wave",
                (actor["station"], row["id"]),
            )
            self.event(db, actor, row["id"], "accept", cart.strip())
            result = {"wave": row["id"], "num": row["num"], "box": row["box"], "label": f"Волна {row['num']}", "box_label": f"Короб {row['num']}"}
            return self._store(db, request_id, result)

    def _progress(self, db, wave_id, product_id=None):
        rows = db.execute(
            "SELECT j.state, s.payload FROM jobs j JOIN snapshot s ON s.id=j.id WHERE j.wave=?",
            (wave_id,),
        ).fetchall()
        done_states = ("picked", "printing", "packed", "returned", "excluded")
        done = sum(1 for r in rows if r["state"] in done_states)
        left = None
        if product_id:
            left = 0
            for r in rows:
                if r["state"] == "picking" and json.loads(r["payload"]).get("product_id") == product_id:
                    left += 1
        return {"done": done, "total": len(rows), "left": left}

    def pick_scan(self, actor, wave, code, request_id=""):
        with self.db() as db:
            hit = self._cached(db, request_id)
            if hit:
                return hit
            if (actor.get("role") or "") not in ("", "picker"):
                raise ReadError("Отбор делает сборщик")
            rows = db.execute(
                "SELECT j.id, s.payload FROM jobs j JOIN snapshot s ON s.id=j.id JOIN waves w ON w.id=j.wave WHERE j.wave=? AND j.state='picking' AND w.employee=?",
                (wave, actor["employee"]),
            ).fetchall()
            if not rows:
                raise ReadError("В этой волне нечего отбирать")
            rows.sort(key=lambda r: (json.loads(r["payload"]).get("moment", ""), r["id"]))
            current = json.loads(rows[0]["payload"])
            if code not in current["barcodes"]:
                raise ReadError("Не тот товар")
            result = self._apply(db, actor, rows[0]["id"], "pick", code, "")
            result.update(self._progress(db, wave, current.get("product_id")))
            result["job"] = rows[0]["id"]
            return self._store(db, request_id, result)

    def station_scan(self, actor, wave, code, request_id=""):
        with self.db() as db:
            hit = self._cached(db, request_id)
            if hit:
                return hit
            if (actor.get("role") or "") not in ("", "station"):
                raise ReadError("Стикер выдаёт станция")
            pending = db.execute(
                "SELECT j.id,j.wave,s.payload FROM jobs j JOIN snapshot s ON s.id=j.id WHERE j.station=? AND j.state='printing'",
                (actor["station"],),
            ).fetchone()
            if pending:
                if code not in json.loads(pending["payload"])["barcodes"]:
                    raise ReadError("На станции есть незакрытый стикер другого товара")
                oid = pending["id"]
            else:
                bind = db.execute("SELECT wave FROM station_bind WHERE station=?", (actor["station"],)).fetchone()
                if bind and bind["wave"] != wave:
                    raise ReadError("Станция приняла другую тележку")
                rows = db.execute(
                    "SELECT j.id,j.state,s.payload FROM jobs j JOIN snapshot s ON s.id=j.id WHERE j.wave=?",
                    (wave,),
                ).fetchall()
                candidates = [(json.loads(r["payload"]).get("moment", ""), r["id"]) for r in rows
                              if r["state"] == "picked" and code in json.loads(r["payload"])["barcodes"]]
                if not candidates:
                    raise ReadError("Нет отобранного товара с таким штрихкодом в этой волне")
                oid = min(candidates)[1]
            result = self._apply(db, actor, oid, "print", code, "")
            result["job"] = oid
            return self._store(db, request_id, result)

    def resolve(self, actor, oid, decision, reason="", request_id=""):
        with self.db() as db:
            hit = self._cached(db, request_id)
            if hit:
                return hit
            if actor.get("role") != "shift":
                raise ReadError("Решение по дефициту принимает Смена")
            row = db.execute("SELECT j.state, s.payload FROM jobs j JOIN snapshot s ON s.id=j.id WHERE j.id=?", (oid,)).fetchone()
            if not row or row["state"] != "shortage":
                raise ReadError("Это задание не в дефиците")
            if decision == "found":
                if self.ms.status(oid) != READY:
                    db.execute("UPDATE jobs SET state='return_pending', note=? WHERE id=?", ("Статус изменился", oid))
                    self.event(db, actor, oid, "shortage_blocked", "Статус изменился, в отбор не возвращать")
                    result = {"message": "Статус изменился. Не возвращать в отбор.", "blocked": True}
                else:
                    db.execute("UPDATE jobs SET state='picking', note='' WHERE id=?", (oid,))
                    self.event(db, actor, oid, "shortage_found", "Товар найден, вернуть в отбор")
                    result = {"message": "Товар снова в отборе"}
            elif decision == "missing":
                text = reason.strip() or "Не найден"
                db.execute("UPDATE jobs SET state='excluded', note=? WHERE id=?", (text, oid))
                self.event(db, actor, oid, "shortage_missing", text)
                result = {"message": "Исключение закрыто. В МойСклад это не записано."}
            else:
                raise ReadError("Неизвестное решение")
            return self._store(db, request_id, result)

    def action(self, actor, oid, action, code="", target="", request_id=""):
        with self.db() as db:
            hit = self._cached(db, request_id)
            if hit:
                return hit
            return self._store(db, request_id, self._apply(db, actor, oid, action, code, target))

    def _apply(self, db, actor, oid, action, code="", target=""):
        role = actor.get("role") or ""
        if role == "picker" and action not in ("cell", "pick", "shortage"):
            raise ReadError("Это действие не для сборщика")
        if role == "station" and action not in ("print", "sticker", "box"):
            raise ReadError("Это действие не для станции")
        if role == "shift" and action not in ("transfer", "return"):
            raise ReadError("Это действие не для смены")
        row = db.execute("SELECT j.*,s.payload,w.employee,w.box FROM jobs j JOIN snapshot s ON j.id=s.id JOIN waves w ON j.wave=w.id WHERE j.id=?", (oid,)).fetchone()
        if not row:
            raise ReadError("Задание не найдено в волне")
        o, j = json.loads(row["payload"]), dict(row)
        if action in ("cell", "pick", "shortage") and j["employee"] != actor["employee"]:
            raise ReadError("Волна закреплена за другим сборщиком")
        if action in ("print", "sticker", "box"):
            if j["state"] in ("packed", "return_pending", "returned", "shortage", "excluded"):
                raise ReadError("Текущий этап задания не позволяет эту операцию")
            if j["station"] and j["station"] != actor["station"]:
                raise ReadError("Задание закреплено за другой станцией. Требуется явная передача")
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
            who = target.strip() or SHIFT
            db.execute("UPDATE jobs SET state='shortage', note=? WHERE id=?", (who, oid))
            self.event(db, actor, oid, "shortage_owner", who)
        elif action == "print":
            if j["state"] not in ("picked", "printing") or code not in o["barcodes"]:
                raise ReadError("Сначала отбери и отсканируй нужный товар")
            other = db.execute("SELECT id FROM jobs WHERE station=? AND state='printing' AND id<>?", (actor["station"], oid)).fetchone()
            if other:
                raise ReadError("Сначала закрой неподтверждённый стикер этой станции")
            if j["pdf"]:
                self.event(db, actor, oid, "reprint", "Тот же PDF и то же задание")
                return {"message": "Тот же стикер. Открытие файла ещё не значит, что принтер напечатал.", "pdf": "/api/label/" + oid}
            pdf = fetch_label(o["label_url"])
            qr, mm = decode_sticker(pdf)
            if not (abs(mm[0] - 58) < 2 and abs(mm[1] - 40) < 2):
                raise ReadError("Размер оригинального стикера отличается от 58 на 40 мм")
            if not self.ms.verify_label(oid, qr):
                raise ReadError("QR не совпал с полем ШККОД128 заказа или статус изменился")
            if db.execute("SELECT 1 FROM jobs WHERE qr=? AND id<>?", (qr, oid)).fetchone():
                raise ReadError("Этот стикер уже связан с другим заданием")
            db.execute("UPDATE jobs SET state='printing',station=?,pdf=?,qr=? WHERE id=?", (actor["station"], pdf, qr, oid))
            self.event(db, actor, oid, "print", "Стикер подготовлен. Открытие файла не доказывает печать")
            return {"message": "Стикер готов. Напечатай его. Открытие файла ещё не значит, что принтер напечатал.", "pdf": "/api/label/" + oid}
        elif action == "sticker":
            if j["state"] != "printing" or not j["qr"] or code != j["qr"]:
                raise ReadError("Стикер не относится к этому заданию")
            db.execute("UPDATE jobs SET sticker_ok=1 WHERE id=?", (oid,))
        elif action == "box":
            if j["state"] != "printing" or not j["sticker_ok"] or code != j["box"]:
                raise ReadError("Сначала проверь стикер, затем отсканируй свой короб")
            db.execute("UPDATE jobs SET state='packed' WHERE id=?", (oid,))
        elif action == "transfer":
            if j["state"] != "printing" or not target.strip():
                raise ReadError("Передать можно только незакрытый стикер")
            if role != "shift" and j["station"] != actor["station"]:
                raise ReadError("Передать можно только незакрытое задание своей станции")
            if db.execute("SELECT 1 FROM jobs WHERE station=? AND state='printing'", (target,)).fetchone():
                raise ReadError("У целевой станции уже есть незакрытый стикер")
            db.execute("UPDATE jobs SET station=? WHERE id=?", (target.strip(), oid))
            self.event(db, actor, oid, "transfer_by", actor["employee"])
        elif action == "return":
            if role in ("picker", "station"):
                raise ReadError("Возврат подтверждает Смена")
            if j["state"] != "return_pending" or code not in o["barcodes"] or not target.strip():
                raise ReadError("Скан товара и ячейка возврата обязательны")
            db.execute("UPDATE jobs SET state='returned', note=? WHERE id=?", (target.strip(), oid))
            self.event(db, actor, oid, "return_cell", target.strip())
        else:
            raise ReadError("Неизвестная операция")
        self.event(db, actor, oid, action, "Проверка скана пройдена" if code else target)
        if action == "pick":
            return {"message": "Штука отобрана"}
        if action == "shortage":
            return {"message": "Нет на месте. Смена разберёт это задание, остальные штуки артикула остаются."}
        if action == "box":
            return {"message": "В коробе"}
        return {"message": "Готово"}

    def label(self, actor, oid):
        with self.db() as db:
            row = db.execute("SELECT pdf,station,state FROM jobs WHERE id=?", (oid,)).fetchone()
            if not row or row["station"] != actor["station"] or row["state"] != "printing":
                raise ReadError("Стикер недоступен этой станции")
            if self.ms.status(oid) != READY:
                raise ReadError("Статус изменился. PDF заблокирован, проверь задание")
            return bytes(row["pdf"])
