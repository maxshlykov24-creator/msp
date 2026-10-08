from __future__ import annotations
import json,sqlite3,time
from pathlib import Path

class Storage:
    def __init__(self,path):
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(str(path));self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS sessions (chat INTEGER PRIMARY KEY, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, chat INTEGER, created INTEGER, kind TEXT, data TEXT);
        CREATE TABLE IF NOT EXISTS updates (id INTEGER PRIMARY KEY, status TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS ledger (id INTEGER PRIMARY KEY, day TEXT, reserved REAL, actual REAL);
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
        ''');self.db.commit()
    def session(self,chat):
        row=self.db.execute('SELECT data FROM sessions WHERE chat=?',(chat,)).fetchone()
        return json.loads(row[0]) if row else {'messages':[]}
    def save(self,chat,data):
        self.db.execute('INSERT OR REPLACE INTO sessions VALUES (?,?)',(chat,json.dumps(data,ensure_ascii=False)));self.db.commit()
    def event(self,chat,kind,data):
        self.db.execute('INSERT INTO events(chat,created,kind,data) VALUES (?,?,?,?)',(chat,int(time.time()),kind,json.dumps(data,ensure_ascii=False)));self.db.commit()
    def transcript(self,chat):
        return [{'time':t,'kind':k,'data':json.loads(d)} for t,k,d in self.db.execute('SELECT created,kind,data FROM events WHERE chat=? ORDER BY id',(chat,))]
    def claim(self,update):
        try:
            self.db.execute('INSERT INTO updates VALUES (?,?)',(update,'processing'));self.db.commit();return True
        except sqlite3.IntegrityError:return False
    def complete(self,update,status='done'):
        self.db.execute('UPDATE updates SET status=? WHERE id=?',(status,update));self.db.commit()
    def meta(self,key,value=None):
        if value is not None:
            self.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',(key,str(value)));self.db.commit()
        row=self.db.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone();return row[0] if row else None
    def reserve(self,limit,amount=.02):
        day=time.strftime('%Y-%m-%d',time.gmtime())
        self.db.execute('BEGIN IMMEDIATE')
        used=self.db.execute('SELECT COALESCE(SUM(COALESCE(actual,reserved)),0) FROM ledger WHERE day=?',(day,)).fetchone()[0]
        if used+amount>limit:
            self.db.rollback();raise RuntimeError('Daily lab budget reached')
        cur=self.db.execute('INSERT INTO ledger(day,reserved) VALUES (?,?)',(day,amount));self.db.commit();return cur.lastrowid
    def settle(self,key,cost):
        if not isinstance(cost,(int,float)) or cost<0:return # keep conservative reservation if usage absent
        self.db.execute('UPDATE ledger SET actual=? WHERE id=?',(cost,key));self.db.commit()
    def spent(self):
        return self.db.execute('SELECT COALESCE(SUM(COALESCE(actual,reserved)),0) FROM ledger').fetchone()[0]
