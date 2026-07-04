#!/usr/bin/env python3
"""
Собирает из data/raw/chat_*.json удобный вьюер переписок для менеджера.

Результат (папка viewer/):
  viewer/index.html   — адаптивный интерфейс (ПК + телефон), поиск, фильтры;
  viewer/data.js      — window.CHATS = [...] (метаданные + сообщения);
  viewer/README.txt   — как открыть.

Вьюер работает и локально (двойной клик по index.html), и на приватном хосте
(данные грузятся через <script src=data.js>, без fetch — значит без CORS на file://).

Запуск:
    python build_viewer.py                # чаты с активностью за 6 мес
    python build_viewer.py --months 0     # все, что есть в data/raw
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
VIEWER_DIR = ROOT / "viewer"


def parse_dt(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


def build_managers_index():
    """Пытаемся достать имена менеджеров из любого raw (from.id -> label не нужен,
    берём напрямую сохранённые сообщения). Здесь просто заглушка на будущее."""
    return {}


def sender_label(message: dict, customer_id) -> str:
    frm = message.get("from") or {}
    from_id = frm.get("id")
    if from_id is not None and customer_id is not None and from_id == customer_id:
        return "Клиент"
    name = " ".join(filter(None, [frm.get("first_name"), frm.get("last_name")])).strip()
    ftype = (frm.get("type") or "").lower()
    if ftype == "customer":
        return "Клиент"
    if name:
        return name
    if ftype in ("user", "manager"):
        return "Менеджер"
    if ftype == "bot":
        return "Бот"
    return "Система"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=6, help="фильтр по активности, 0 = все")
    args = ap.parse_args()

    cutoff = None
    if args.months:
        cutoff = datetime.now(timezone.utc) - timedelta(days=int(args.months * 30.5))

    VIEWER_DIR.mkdir(exist_ok=True)
    chats_out = []
    skipped = 0
    files = sorted(RAW_DIR.glob("chat_*.json"))
    for fp in files:
        try:
            dump = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            skipped += 1
            continue
        chat = dump.get("chat") or {}
        messages = dump.get("messages") or []
        last_act = parse_dt(chat.get("last_activity"))
        if cutoff is not None:
            if last_act is None or last_act < cutoff:
                continue
        customer = chat.get("customer") or {}
        cid = customer.get("id")
        name = " ".join(filter(None, [customer.get("first_name"), customer.get("last_name")])).strip()
        name = name or customer.get("username") or customer.get("phone") or f"чат {chat.get('id')}"

        msgs = []
        times = []
        for m in sorted(messages, key=lambda x: x.get("time") or x.get("created_at") or ""):
            t = m.get("time") or m.get("created_at") or ""
            times.append(t)
            who = sender_label(m, cid)
            text = m.get("content") or ""
            atts = []
            for it in (m.get("items") or []):
                cap = it.get("caption") or it.get("type") or "вложение"
                atts.append(str(cap))
            if atts:
                text = (text + " " if text else "") + "[" + ", ".join(atts) + "]"
            role = "c" if who == "Клиент" else ("m" if who in ("Менеджер",) or (who not in ("Бот", "Система")) else "s")
            msgs.append([t, who, text, role])

        chats_out.append({
            "id": chat.get("id"),
            "name": name,
            "phone": customer.get("phone") or "",
            "username": customer.get("username") or "",
            "channel": (chat.get("channel") or {}).get("type") or "",
            "channel_name": (chat.get("channel") or {}).get("name") or "",
            "first": (times[0] if times else "") or "",
            "last": chat.get("last_activity") or (times[-1] if times else "") or "",
            "count": len(msgs),
            "msgs": msgs,
        })

    chats_out.sort(key=lambda c: c.get("last") or "", reverse=True)

    data_js = "window.CHATS=" + json.dumps(chats_out, ensure_ascii=False, separators=(",", ":")) + ";"
    (VIEWER_DIR / "data.js").write_text(data_js, encoding="utf-8")
    (VIEWER_DIR / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (VIEWER_DIR / "README.txt").write_text(README_TXT, encoding="utf-8")

    total_msgs = sum(c["count"] for c in chats_out)
    size_mb = (VIEWER_DIR / "data.js").stat().st_size / 1024 / 1024
    print(f"Готово. Диалогов: {len(chats_out)}, сообщений: {total_msgs}")
    print(f"data.js: {size_mb:.1f} MB")
    print(f"Открыть: {VIEWER_DIR / 'index.html'}")
    if skipped:
        print(f"Пропущено битых файлов: {skipped}")


README_TXT = """История переписок MartaChe (Telegram / Instagram / WhatsApp)

КАК ОТКРЫТЬ НА КОМПЬЮТЕРЕ
  Дважды кликните index.html — откроется в браузере. Установка не нужна.

КАК ОТКРЫТЬ НА ТЕЛЕФОНЕ
  Откройте выданную ссылку (приватный доступ под паролем) в браузере телефона.

ПОИСК
  Строка поиска ищет одновременно по имени, телефону, @юзернейму и тексту сообщений.
  Можно фильтровать по каналу. Клик по диалогу — открывается вся переписка.

Данные — персональные, не пересылайте посторонним.
"""


INDEX_HTML = r"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>История переписок — MartaChe</title>
<style>
:root{
  --bg:#f5f6f8; --panel:#fff; --line:#e6e8eb; --muted:#8a9099; --accent:#7c3aed;
  --c-client:#ecfdf5; --c-client-b:#10b981; --c-mgr:#eef2ff; --c-mgr-b:#6366f1; --c-sys:#f3f4f6;
}
*{box-sizing:border-box}
html,body{margin:0;height:100%;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:var(--bg);color:#1f2328}
.app{display:flex;height:100dvh;overflow:hidden}
.list{width:380px;min-width:300px;background:var(--panel);border-right:1px solid var(--line);display:flex;flex-direction:column}
.head{padding:12px;border-bottom:1px solid var(--line)}
.head h1{font-size:15px;margin:0 0 8px}
.search{width:100%;padding:10px 12px;border:1px solid var(--line);border-radius:10px;font-size:15px;outline:none}
.search:focus{border-color:var(--accent)}
.filters{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
.chip{padding:5px 10px;border:1px solid var(--line);border-radius:20px;font-size:12px;background:#fff;cursor:pointer;user-select:none}
.chip.on{background:var(--accent);color:#fff;border-color:var(--accent)}
.stat{font-size:12px;color:var(--muted);margin-top:8px}
.rows{flex:1;overflow-y:auto;-webkit-overflow-scrolling:touch}
.row{padding:11px 12px;border-bottom:1px solid var(--line);cursor:pointer}
.row:hover{background:#faf9ff}
.row.active{background:#f3efff}
.row .top{display:flex;justify-content:space-between;gap:8px;align-items:baseline}
.row .nm{font-weight:600;font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.row .dt{font-size:11px;color:var(--muted);white-space:nowrap}
.row .sub{font-size:12px;color:var(--muted);margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.badge{display:inline-block;font-size:10px;padding:1px 6px;border-radius:6px;margin-right:5px;vertical-align:middle}
.b-telegram{background:#e5f3ff;color:#1d7fd1}
.b-instagram{background:#ffe9f3;color:#c2338a}
.b-whatsapp{background:#e6f8ec;color:#1a9e4b}
.b-other{background:#eee;color:#666}
.detail{flex:1;display:flex;flex-direction:column;overflow:hidden;background:var(--bg)}
.dhead{padding:12px 16px;background:var(--panel);border-bottom:1px solid var(--line);display:flex;align-items:center;gap:10px}
.back{display:none;border:none;background:#f0f0f3;border-radius:8px;padding:8px 12px;font-size:16px;cursor:pointer}
.dhead .info h2{font-size:15px;margin:0}
.dhead .info div{font-size:12px;color:var(--muted)}
.msgs{flex:1;overflow-y:auto;padding:16px;-webkit-overflow-scrolling:touch}
.m{max-width:78%;margin:8px 0;padding:8px 12px;border-radius:12px;font-size:14px;line-height:1.4;white-space:pre-wrap;word-break:break-word}
.m .meta{font-size:11px;color:var(--muted);margin-bottom:3px}
.m.c{background:var(--c-client);border:1px solid var(--c-client-b)33;margin-right:auto}
.m.m{background:var(--c-mgr);border:1px solid var(--c-mgr-b)33;margin-left:auto}
.m.s{background:var(--c-sys);color:#555;margin:8px auto;text-align:center;font-size:12px;max-width:90%}
.empty{margin:auto;color:var(--muted);text-align:center;padding:40px}
mark{background:#fde68a;padding:0 1px;border-radius:2px}
@media(max-width:760px){
  .list{width:100%}
  .detail{position:fixed;inset:0;z-index:10;transform:translateX(100%);transition:transform .2s}
  .app.open .detail{transform:translateX(0)}
  .back{display:block}
}
</style>
</head>
<body>
<div class="app" id="app">
  <div class="list">
    <div class="head">
      <h1>История переписок</h1>
      <input class="search" id="q" placeholder="Поиск: имя, телефон, @ник, текст…" autocomplete="off">
      <div class="filters" id="filters"></div>
      <div class="stat" id="stat"></div>
    </div>
    <div class="rows" id="rows"></div>
  </div>
  <div class="detail">
    <div class="dhead">
      <button class="back" id="back">←</button>
      <div class="info" id="dinfo"><h2>Выберите диалог</h2><div></div></div>
    </div>
    <div class="msgs" id="msgs"><div class="empty">Слева выберите переписку или воспользуйтесь поиском.</div></div>
  </div>
</div>
<script src="data.js"></script>
<script>
const CH = (window.CHATS||[]);
CH.forEach(c=>{ c._s = ((c.name||"")+" "+(c.phone||"")+" "+(c.username||"")+" "+c.msgs.map(m=>m[2]).join(" ")).toLowerCase(); });
const chans = [...new Set(CH.map(c=>c.channel).filter(Boolean))].sort();
let curChan="", curQ="", active=null;

const el=id=>document.getElementById(id);
const RU={telegram:"Telegram",instagram:"Instagram",whatsapp:"WhatsApp"};
function badge(ch){const k=(ch||"other");const cls=["telegram","instagram","whatsapp"].includes(k)?k:"other";return `<span class="badge b-${cls}">${RU[k]||ch||"—"}</span>`;}
function fdate(s){if(!s)return"";const d=new Date(s);if(isNaN(d))return"";return d.toLocaleDateString("ru-RU",{day:"2-digit",month:"2-digit",year:"2-digit"});}
function ftime(s){if(!s)return"";const d=new Date(s);if(isNaN(d))return s;return d.toLocaleString("ru-RU",{day:"2-digit",month:"2-digit",year:"2-digit",hour:"2-digit",minute:"2-digit"});}
function esc(s){return (s||"").replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));}
function hl(s,q){s=esc(s);if(!q)return s;try{return s.replace(new RegExp("("+q.replace(/[.*+?^${}()|[\]\\]/g,"\\$&")+")","ig"),"<mark>$1</mark>");}catch(e){return s;}}

function buildFilters(){
  const f=el("filters");
  const mk=(id,label)=>`<div class="chip${curChan===id?' on':''}" data-c="${id}">${label}</div>`;
  f.innerHTML=mk("","Все")+chans.map(c=>mk(c,RU[c]||c)).join("");
  f.querySelectorAll(".chip").forEach(ch=>ch.onclick=()=>{curChan=ch.dataset.c;buildFilters();render();});
}

function render(){
  const q=curQ.trim().toLowerCase();
  let list=CH;
  if(curChan) list=list.filter(c=>c.channel===curChan);
  if(q) list=list.filter(c=>c._s.includes(q));
  el("stat").textContent=`Показано ${list.length} из ${CH.length} диалогов`;
  const rows=el("rows");
  rows.innerHTML=list.slice(0,600).map(c=>`
    <div class="row${active===c.id?' active':''}" data-id="${c.id}">
      <div class="top"><div class="nm">${badge(c.channel)}${esc(c.name)}</div><div class="dt">${fdate(c.last)}</div></div>
      <div class="sub">${esc(c.phone||c.username||"")} · ${c.count} сообщ.</div>
    </div>`).join("") || `<div class="empty">Ничего не найдено</div>`;
  if(list.length>600) rows.innerHTML+=`<div class="stat" style="padding:12px">…и ещё ${list.length-600}. Уточните поиск.</div>`;
  rows.querySelectorAll(".row").forEach(r=>r.onclick=()=>open(+r.dataset.id));
}

function open(id){
  const c=CH.find(x=>x.id===id); if(!c)return;
  active=id; render();
  el("dinfo").innerHTML=`<h2>${esc(c.name)}</h2><div>${badge(c.channel)}${esc(c.phone||"")} ${c.username?("@"+esc(c.username)):""} · ${c.count} сообщений</div>`;
  const q=curQ.trim();
  el("msgs").innerHTML=c.msgs.map(m=>{
    const role=m[3]||"s";
    return `<div class="m ${role}"><div class="meta">${esc(m[1])} · ${ftime(m[0])}</div>${hl(m[2],q)}</div>`;
  }).join("") || `<div class="empty">Нет сообщений</div>`;
  el("msgs").scrollTop=0;
  document.getElementById("app").classList.add("open");
}

let t;
el("q").addEventListener("input",e=>{curQ=e.target.value;clearTimeout(t);t=setTimeout(render,120);});
el("back").onclick=()=>document.getElementById("app").classList.remove("open");
buildFilters();
render();
</script>
</body>
</html>"""


if __name__ == "__main__":
    main()
