const USERS = {
  manager: {pass:'demo2026'},
  demo:    {pass:'demo2026'}
};

const CLIENTS = [
  { id:'sev',  name:'ТД «Северный»', note:'Ozon FBS + WB', contract:'№ 14-Ф от 12.02.2026',
    sku:142, units:18400, volNow:42300, rate:.038, opsBase:28400, invoice:'Выставлен', invState:'warn', longVol:0,
    ops:[ {n:'Приёмка поставок', d:'4 поставки', v:9800},{n:'Сборка FBS', d:'612 заказов', v:17381},{n:'Упаковка', d:'612 отправлений', v:1219} ],
    orders:{ sent:612, pick:23, sla:'99,3%', ret:14 } },
  { id:'avr',  name:'«Аврора»', note:'поставщик Fix Price', contract:'№ 09-Ф от 20.01.2026',
    sku:96, units:12250, volNow:31800, rate:.040, opsBase:21900, invoice:'Оплачен', invState:'ok', longVol:1800,
    ops:[ {n:'Приёмка', d:'3 поставки', v:7400},{n:'Сборка FBS', d:'438 заказов', v:12439},{n:'Упаковка', d:'438 отправлений', v:2061} ],
    orders:{ sent:438, pick:11, sla:'99,1%', ret:9 } },
  { id:'grip', name:'GripOn', note:'свой бренд', contract:'внутр.',
    sku:74, units:9600, volNow:24900, rate:.030, opsBase:18600, invoice:'Внутренний', invState:'neutral', longVol:2600,
    ops:[ {n:'Сборка FBS', d:'520 заказов', v:14768},{n:'Упаковка', d:'520 отправлений', v:3832} ],
    orders:{ sent:520, pick:8, sla:'99,5%', ret:6 } },
  { id:'kuz',  name:'ИП Кузнецов', note:'Ozon FBS', contract:'№ 21-Ф от 03.04.2026',
    sku:38, units:4180, volNow:18600, rate:.045, opsBase:14200, invoice:'Выставлен', invState:'warn', longVol:5600,
    ops:[ {n:'Приёмка', d:'1 поставка', v:2600},{n:'Сборка FBS', d:'392 заказа', v:11133},{n:'Долгое хранение', d:'коэфф. 1,6', v:467} ],
    orders:{ sent:392, pick:0, sla:'98,4%', ret:21 } },
  { id:'mts',  name:'«Мастер Тулс»', note:'Я.Маркет + WB', contract:'№ 11-Ф от 15.03.2026',
    sku:29, units:2740, volNow:10850, rate:.048, opsBase:13200, invoice:'Черновик', invState:'bad', longVol:4200,
    ops:[ {n:'Сборка FBS', d:'280 заказов', v:7952},{n:'Упаковка', d:'280 отправлений', v:5248} ],
    orders:{ sent:280, pick:5, sla:'98,8%', ret:11 } },
];

const DONUT_COLORS = ['#005BFF','#00A2FF','#3d7cff','#7aa8ff','#a8c6ff'];
const MIN = '2026-05-01', MAX = '2026-07-31';
const VOL_NOW = CLIENTS.reduce((s,c)=>s+c.volNow,0);

const STOCK = [
  { art:'10143', name:'Пистолет для полива, 7 режимов', client:'ТД «Северный»', wh:1840, res:260, ozon:900, wb:500, ym:180, sync:'ok' },
  { art:'10180', name:'Щётка УШМ 65 мм латунная', client:'«Аврора»', wh:640, res:90, ozon:320, wb:180, ym:50, sync:'ok' },
  { art:'10248', name:'Мойка высокого давления, шланг', client:'ИП Кузнецов', wh:210, res:35, ozon:140, wb:60, ym:15, sync:'risk' },
  { art:'10102', name:'Коса складная ручная', client:'«Мастер Тулс»', wh:480, res:40, ozon:0, wb:260, ym:180, sync:'ok' },
  { art:'10107', name:'Штуцерный адаптер 1/2 латунь', client:'GripOn', wh:3200, res:410, ozon:1900, wb:700, ym:190, sync:'wait' },
];
const CAPACITY = [
  { name:'Зона А — стеллажи', total:96000, used:78400 },
  { name:'Зона Б — паллеты', total:62000, used:38900 },
  { name:'Зона В — негабарит', total:22000, used:11150 },
];
const SKU_BY_CLIENT = {
  sev:[
    { art:'10143', name:'Пистолет для полива, 7 режимов', wh:1840, res:260, vol:5520, days:34, ch:['Ozon','WB'], st:'ok' },
    { art:'10107', name:'Штуцерный адаптер 1/2 латунь', wh:3200, res:410, vol:4800, days:21, ch:['Ozon','WB','ЯМ'], st:'ok' },
    { art:'10118', name:'Шланг armored 1/2, 25 м', wh:640, res:70, vol:9600, days:12, ch:['Ozon'], st:'ok' },
    { art:'10151', name:'Опрыскиватель садовый 5 л', wh:410, res:35, vol:6150, days:47, ch:['Ozon','WB'], st:'ok' },
    { art:'10166', name:'Набор садовых перчаток, 12 пар', wh:980, res:120, vol:2940, days:96, ch:['WB'], st:'long' },
    { art:'10172', name:'Мойка ручная, аксессуары', wh:220, res:0, vol:3300, days:8, ch:[], st:'new' },
  ],
  avr:[
    { art:'10180', name:'Щётка УШМ 65 мм латунная', wh:640, res:90, vol:1920, days:29, ch:['Ozon','WB','ЯМ'], st:'ok' },
    { art:'10181', name:'Щётка УШМ 65 мм стальная', wh:820, res:110, vol:2460, days:26, ch:['Ozon','WB'], st:'ok' },
    { art:'10195', name:'Диск отрезной 125 мм, 10 шт', wh:1450, res:180, vol:4350, days:15, ch:['Ozon'], st:'ok' },
    { art:'10203', name:'Перчатки нитрил, 100 шт', wh:2100, res:240, vol:8400, days:104, ch:['WB'], st:'long' },
  ],
  kuz:[
    { art:'10248', name:'Мойка высокого давления, шланг', wh:210, res:35, vol:4200, days:62, ch:['Ozon'], st:'risk' },
    { art:'10251', name:'Насадка-турбо для мойки', wh:340, res:40, vol:2040, days:58, ch:['Ozon','WB'], st:'ok' },
    { art:'10260', name:'Комплект фильтров, 4 шт', wh:180, res:0, vol:1080, days:112, ch:[], st:'long' },
  ],
  grip:[
    { art:'10107', name:'Штуцерный адаптер 1/2 латунь', wh:3200, res:410, vol:4800, days:21, ch:['Ozon','WB'], st:'ok' },
    { art:'10190', name:'Ключ разводной 200 мм', wh:540, res:40, vol:1620, days:18, ch:['WB'], st:'ok' },
  ],
  mts:[
    { art:'10102', name:'Коса складная ручная', wh:480, res:40, vol:2880, days:40, ch:['WB','ЯМ'], st:'ok' },
    { art:'10111', name:'Точильный камень', wh:210, res:20, vol:630, days:95, ch:['ЯМ'], st:'long' },
  ],
};
const RECEIPTS_BY_CLIENT = {
  sev:[
    { d:'2026-07-04', dec:420, acc:420, vol:6300 },
    { d:'2026-07-11', dec:280, acc:274, vol:4110 },
    { d:'2026-07-19', dec:340, acc:340, vol:5100 },
    { d:'2026-07-26', dec:200, acc:200, vol:3000 },
  ],
  avr:[
    { d:'2026-07-06', dec:360, acc:360, vol:5400 },
    { d:'2026-07-17', dec:300, acc:288, vol:4320 },
    { d:'2026-07-28', dec:320, acc:320, vol:4800 },
  ],
  kuz:[{ d:'2026-07-09', dec:320, acc:320, vol:4800 }],
  grip:[{ d:'2026-07-15', dec:180, acc:180, vol:2700 }],
  mts:[{ d:'2026-06-22', dec:90, acc:90, vol:1350 }],
};

const fmt = n => Math.round(n).toLocaleString('ru-RU');
const fmt2 = n => n.toLocaleString('ru-RU',{minimumFractionDigits:3,maximumFractionDigits:3});
function addDays(iso,n){ const d=new Date(iso+'T12:00:00'); d.setDate(d.getDate()+n); return d.toISOString().slice(0,10); }
function daysBetween(a,b){ return Math.round((new Date(b+'T12:00:00')-new Date(a+'T12:00:00'))/86400000)+1; }
function fmtDate(iso){ const [y,m,d]=iso.split('-'); return d+'.'+m+'.'+y; }
function clamp(v,a,b){ return Math.max(a,Math.min(b,v)); }
function hash(n){ let x = (n*1103515245+12345)>>>0; return (x%10000)/10000; }

function buildSeries(){
  const days = daysBetween(MIN, MAX);
  const shape = [];
  for(let i=0;i<days;i++){
    const t = i/(days-1);
    shape.push(0.82 + t*0.18 + Math.sin(i/5.1)*0.025 + Math.sin(i/13)*0.015 + (hash(i+3)-0.5)*0.02);
  }
  const shapeLast = shape[shape.length-1];
  const series = [];
  for(let i=0;i<days;i++){
    const volI = Math.round(shape[i]/shapeLast * VOL_NOW);
    const prev = i===0 ? Math.round(volI*0.99) : Math.round(shape[i-1]/shapeLast*VOL_NOW);
    const out = Math.round(1600 + hash(i+11)*2800);
    let inc = out + (volI - prev);
    series.push({ iso: addDays(MIN,i), vol: volI, in: Math.max(0,inc), out });
  }
  return series;
}
const SERIES = buildSeries();

let STATE = { from: addDays(MAX,-29), to: MAX, view:'manager', clientId:'sev' };

function sliceSeries(from,to){ return SERIES.filter(d => d.iso >= from && d.iso <= to); }
function periodFactor(from,to){ return clamp(daysBetween(from,to)/30, 0.2, 3); }

function computeManager(from,to){
  const days = sliceSeries(from,to);
  const dayVol = days.map(d=>d.vol);
  const literDays = dayVol.reduce((s,v)=>s+v,0);
  const endVol = dayVol[dayVol.length-1] || VOL_NOW;
  const pf = periodFactor(from,to);
  const rows = CLIENTS.map(c=>{
    const share = c.volNow / VOL_NOW;
    const vol = Math.round(endVol * share);
    const ld = Math.round(literDays * share);
    const storage = Math.round(ld * c.rate);
    const ops = Math.round(c.opsBase * pf);
    return Object.assign({}, c, { vol, literDays:ld, storage, ops, total: storage+ops });
  });
  const T = {
    literDays: rows.reduce((s,c)=>s+c.literDays,0),
    storage: rows.reduce((s,c)=>s+c.storage,0),
    ops: rows.reduce((s,c)=>s+c.ops,0),
    sku: rows.reduce((s,c)=>s+c.sku,0),
    units: rows.reduce((s,c)=>s+c.units,0),
    longVol: rows.reduce((s,c)=>s+c.longVol,0),
    vol: endVol,
  };
  T.total = T.storage + T.ops;
  T.avgRate = T.literDays ? T.storage / T.literDays : 0;
  return { days, rows, T, pf };
}

function drawLineChart(svgId, tipId, boxId, days, rate){
  const svg = document.getElementById(svgId);
  const tip = document.getElementById(tipId);
  const box = document.getElementById(boxId);
  const N = days.length;
  if(!N){ svg.innerHTML=''; return; }
  const W=1000,H=240,padT=18,padB=28,padL=8,padR=8;
  const plotH=H-padT-padB, plotW=W-padL-padR;
  const vals = days.map(d=>d.vol);
  const maxV = Math.max(...vals)*1.04;
  const minV = Math.min(...vals)*0.96;
  const x = i => padL + (N===1? plotW/2 : i*(plotW/(N-1)));
  const y = v => padT + plotH - (v-minV)/(maxV-minV||1)*plotH;
  let g = `<defs><linearGradient id="volGrad" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%" stop-color="rgba(0,91,255,.22)"/>
    <stop offset="100%" stop-color="rgba(0,91,255,0)"/>
  </linearGradient></defs>`;
  for(let k=0;k<=3;k++){
    const yy = padT + plotH*k/3;
    g += `<line class="grid-line" x1="${padL}" y1="${yy.toFixed(1)}" x2="${W-padR}" y2="${yy.toFixed(1)}"/>`;
  }
  const pts = vals.map((v,i)=>`${x(i).toFixed(1)},${y(v).toFixed(1)}`);
  g += `<path class="area" d="M${padL},${padT+plotH} L${pts.join(' L')} L${W-padR},${padT+plotH} Z"/>`;
  g += `<polyline class="line" points="${pts.join(' ')}"/>`;
  vals.forEach((v,i)=>{ g += `<circle class="dot" data-i="${i}" cx="${x(i).toFixed(1)}" cy="${y(v).toFixed(1)}" r="4"/>`; });
  const step = N>40 ? 14 : N>20 ? 7 : Math.max(1, Math.floor(N/6));
  for(let i=0;i<N;i+=step){
    const lab = days[i].iso.slice(8)+'.'+days[i].iso.slice(5,7);
    g += `<text class="axis-lbl" x="${x(i).toFixed(1)}" y="${H-8}" text-anchor="middle">${lab}</text>`;
  }
  if(N>1){
    const last=N-1, lab=days[last].iso.slice(8)+'.'+days[last].iso.slice(5,7);
    g += `<text class="axis-lbl" x="${x(last).toFixed(1)}" y="${H-8}" text-anchor="middle">${lab}</text>`;
  }
  svg.innerHTML = g;
  svg.onpointermove = e=>{
    const r = box.getBoundingClientRect();
    const i = N===1 ? 0 : Math.round(clamp((e.clientX-r.left)/r.width,0,1)*(N-1));
    svg.querySelectorAll('.dot').forEach(d=>d.classList.toggle('is-hot', +d.dataset.i===i));
    const d = days[i];
    tip.innerHTML = `<b>${fmtDate(d.iso)}</b> — остаток <b>${fmt(d.vol)} л</b>
      <i>приход ${fmt(d.in)} л · отгрузка ${fmt(d.out)} л</i>
      <i>начислено ≈ ${fmt(d.vol*rate)} ₽</i>`;
    tip.style.left = ((N===1?0.5:i/(N-1))*100)+'%';
    tip.style.top = '36px';
    tip.classList.add('is-on');
  };
  svg.onpointerleave = ()=>{
    tip.classList.remove('is-on');
    svg.querySelectorAll('.dot').forEach(d=>d.classList.remove('is-hot'));
  };
}

function renderDonut(rows){
  const svg = document.getElementById('donut');
  const list = document.getElementById('donutList');
  const tip = document.getElementById('donutTip');
  const wrap = document.getElementById('donutWrap');
  const cVal = document.getElementById('donut-c-val');
  const cLbl = document.getElementById('donut-c-lbl');
  const total = rows.reduce((s,c)=>s+c.vol,0) || 1;
  let off = 0;
  svg.innerHTML = rows.map((c,i)=>{
    const pct = Math.max(0.01, c.vol/total*100);
    const dash = pct.toFixed(3), gap=(100-pct).toFixed(3), o=(-off).toFixed(3);
    off += pct;
    return `<circle class="seg" data-i="${i}" data-name="${c.name.replace(/"/g,'&quot;')}" data-val="${c.vol}" data-pct="${(c.vol/total*100).toFixed(1)}" cx="18" cy="18" r="15.9155" stroke="${DONUT_COLORS[i%DONUT_COLORS.length]}" stroke-dasharray="${dash} ${gap}" stroke-dashoffset="${o}"></circle>`;
  }).join('');
  list.innerHTML = rows.map((c,i)=>`
    <div class="dl-row" data-i="${i}">
      <span class="dl-dot" style="background:${DONUT_COLORS[i%DONUT_COLORS.length]}"></span>
      <span class="dl-name">${c.name}</span>
      <span class="dl-val">${fmt(c.vol)}</span>
      <span class="dl-pct">${(c.vol/total*100).toFixed(1)}%</span>
    </div>`).join('');
  svg.dataset.total = String(total);
  cVal.textContent = fmt(total); cLbl.textContent = 'литров';
  tip.classList.remove('is-on'); wrap.classList.remove('is-hot'); svg.classList.remove('is-hovering');

  function activate(i){
    const seg = svg.querySelector('.seg[data-i="'+i+'"]');
    if(!seg) return;
    wrap.classList.add('is-hot'); svg.classList.add('is-hovering');
    svg.querySelectorAll('.seg').forEach(s=>s.classList.toggle('is-hot', +s.dataset.i===i));
    list.querySelectorAll('.dl-row').forEach(r=>r.classList.toggle('is-hot', +r.dataset.i===i));
    cVal.textContent = fmt(+seg.dataset.val);
    cLbl.textContent = seg.dataset.name;
    tip.innerHTML = `<b>${seg.dataset.name}</b><em>${fmt(+seg.dataset.val)} л · ${seg.dataset.pct}%</em>`;
    tip.classList.add('is-on');
  }
  function clear(){
    wrap.classList.remove('is-hot'); svg.classList.remove('is-hovering');
    svg.querySelectorAll('.seg').forEach(s=>s.classList.remove('is-hot'));
    list.querySelectorAll('.dl-row').forEach(r=>r.classList.remove('is-hot'));
    cVal.textContent = fmt(+svg.dataset.total); cLbl.textContent = 'литров';
    tip.classList.remove('is-on');
  }
  svg.onpointermove = e=>{ const t=e.target.closest('.seg'); if(t) activate(+t.dataset.i); else clear(); };
  svg.onpointerleave = clear;
  list.querySelectorAll('.dl-row').forEach(r=>{
    r.onpointerenter = ()=>activate(+r.dataset.i);
    r.onpointerleave = clear;
  });
}

function renderManager(){
  const {from,to} = STATE;
  const { days, rows, T, pf } = computeManager(from,to);
  document.getElementById('m-title').textContent = 'Хранение и услуги склада · '+fmtDate(from)+' — '+fmtDate(to);
  document.getElementById('m-updated').textContent = fmtDate(from)+' — '+fmtDate(to)+' · '+days.length+' дн.';
  document.getElementById('k-vol').innerHTML = fmt(T.vol)+' <small>л</small>';
  document.getElementById('k-vol-sub').textContent = 'по '+rows.length+' клиентам · '+(T.vol/1000).toLocaleString('ru-RU',{maximumFractionDigits:1})+' м³';
  document.getElementById('k-ld').textContent = fmt(T.literDays);
  document.getElementById('k-store').innerHTML = fmt(T.storage)+' <small>₽</small>';
  document.getElementById('k-rate').textContent = fmt2(T.avgRate);
  document.getElementById('k-ops').innerHTML = fmt(T.ops)+' <small>₽</small>';
  document.getElementById('k-total').innerHTML = fmt(T.total)+' <small>₽</small>';
  document.getElementById('k-total-sub').textContent = 'хранение + операции · '+rows.length+' счетов';
  document.getElementById('m-billed').textContent = fmt(T.total)+' ₽';
  const paid = rows.filter(c=>c.invState==='ok').reduce((s,c)=>s+c.total,0);
  document.getElementById('m-paid').textContent = fmt(paid)+' ₽';
  document.getElementById('m-paid-sub').textContent = (T.total?Math.round(paid/T.total*100):0)+'% от выставленного';
  document.getElementById('m-long').textContent = fmt(T.longVol)+' л';
  document.getElementById('ops-fbs').textContent = fmt(Math.round(1842*pf));
  document.getElementById('ops-diff').textContent = String(Math.max(1, Math.round(7*pf)));

  drawLineChart('mChart','mChartTip','mChartBox', days, T.avgRate);
  renderDonut(rows);

  document.querySelector('#clientTbl tbody').innerHTML = rows.map(c=>`
    <tr>
      <td class="name">${c.name}<span class="sub">${c.note}</span></td>
      <td class="num">${c.sku}</td>
      <td class="num">${fmt(c.units)}</td>
      <td class="num">${fmt(c.vol)}</td>
      <td class="num">${fmt(c.literDays)}</td>
      <td class="num">${fmt2(c.rate)}</td>
      <td class="num">${fmt(c.storage)}</td>
      <td class="num">${fmt(c.ops)}</td>
      <td class="num money gold">${fmt(c.total)}</td>
      <td><span class="badge ${c.invState}"><i></i>${c.invoice}</span></td>
    </tr>`).join('');
  document.querySelector('#clientTbl tfoot').innerHTML = `
    <tr>
      <td>Итого · ${rows.length} клиентов</td>
      <td class="num">${T.sku}</td>
      <td class="num">${fmt(T.units)}</td>
      <td class="num">${fmt(T.vol)}</td>
      <td class="num">${fmt(T.literDays)}</td>
      <td class="num">${fmt2(T.avgRate)}</td>
      <td class="num">${fmt(T.storage)}</td>
      <td class="num">${fmt(T.ops)}</td>
      <td class="num money gold">${fmt(T.total)}</td>
      <td></td>
    </tr>`;

  const sel = document.getElementById('clientSel');
  if(!sel.dataset.ready){
    sel.innerHTML = CLIENTS.map(c=>`<option value="${c.id}">${c.name}</option>`).join('');
    sel.dataset.ready = '1';
    sel.onchange = ()=>renderDaily(sel.value);
  }
  renderDaily(sel.value || CLIENTS[0].id);

  document.getElementById('capacity').innerHTML = CAPACITY.map(z=>{
    const pct = z.used/z.total*100;
    return `<div class="cap-row">
      <div class="cap-name">${z.name}</div>
      <div class="cap-bar"><div class="cap-fill" style="width:${pct.toFixed(1)}%">${pct.toFixed(0)}%</div></div>
      <div class="cap-val">${fmt(z.used)} / ${fmt(z.total)}</div>
    </div>`;
  }).join('');
  const tot = CAPACITY.reduce((s,z)=>s+z.total,0), used = CAPACITY.reduce((s,z)=>s+z.used,0);
  document.getElementById('cap-free').textContent = fmt(tot-used);
  document.getElementById('cap-free-pct').textContent = Math.round((tot-used)/tot*100);

  const map = { ok:['ok','Синхронизирован'], wait:['warn','Ожидает'], risk:['bad','Расхождение'] };
  document.querySelector('#stockTbl tbody').innerHTML = STOCK.map(s=>{
    const avail = s.wh - s.res, listed = s.ozon+s.wb+s.ym, bad = listed>avail;
    const [cls,txt] = map[s.sync];
    return `<tr>
      <td class="name">${s.art}<span class="sub">${s.name}</span></td>
      <td>${s.client}</td>
      <td class="num">${fmt(s.wh)}</td>
      <td class="num">${fmt(s.res)}</td>
      <td class="num" ${bad?'style="color:var(--red)"':''}>${fmt(avail)}</td>
      <td class="num">${s.ozon?fmt(s.ozon):'—'}</td>
      <td class="num">${s.wb?fmt(s.wb):'—'}</td>
      <td class="num">${s.ym?fmt(s.ym):'—'}</td>
      <td><span class="badge ${cls}"><i></i>${txt}${bad?' · −'+fmt(listed-avail):''}</span></td>
    </tr>`;
  }).join('');
}

function renderDaily(id){
  const c = CLIENTS.find(x=>x.id===id);
  const days = sliceSeries(STATE.from, STATE.to);
  const share = c.volNow / VOL_NOW;
  let sIn=0,sOut=0,sLd=0,sRub=0;
  document.querySelector('#dailyTbl tbody').innerHTML = days.map(d=>{
    const vol = Math.round(d.vol*share);
    const inc = Math.round(d.in*share);
    const out = Math.round(d.out*share);
    const rub = vol * c.rate;
    sIn+=inc; sOut+=out; sLd+=vol; sRub+=rub;
    return `<tr>
      <td>${fmtDate(d.iso)}</td>
      <td class="num">${inc?fmt(inc):'—'}</td>
      <td class="num">${out?fmt(out):'—'}</td>
      <td class="num">${fmt(vol)}</td>
      <td class="num">${fmt2(c.rate)}</td>
      <td class="num money">${fmt(rub)}</td>
    </tr>`;
  }).join('');
  document.querySelector('#dailyTbl tfoot').innerHTML = `<tr>
    <td>Итого</td>
    <td class="num">${fmt(sIn)}</td>
    <td class="num">${fmt(sOut)}</td>
    <td class="num">${fmt(sLd)} <span class="sub">литро-дни</span></td>
    <td class="num">${fmt2(c.rate)}</td>
    <td class="num money gold">${fmt(sRub)}</td>
  </tr>`;
}

function clientDays(c){
  const days = sliceSeries(STATE.from, STATE.to);
  const share = c.volNow / VOL_NOW;
  return days.map(d=>({
    iso:d.iso,
    vol: Math.round(d.vol*share),
    in: Math.round(d.in*share),
    out: Math.round(d.out*share),
  }));
}

function renderClient(){
  const c = CLIENTS.find(x=>x.id===STATE.clientId) || CLIENTS[0];
  const days = clientDays(c);
  const literDays = days.reduce((s,d)=>s+d.vol,0);
  const storage = Math.round(literDays * c.rate);
  const pf = periodFactor(STATE.from, STATE.to);
  const ops = c.ops.map(o=>({n:o.n, d:o.d, v:Math.round(o.v*pf)}));
  const opsSum = ops.reduce((s,o)=>s+o.v,0);
  const total = storage + opsSum;
  const endVol = days.length ? days[days.length-1].vol : c.volNow;

  document.getElementById('c-title').textContent = 'Мой товар — '+c.name;
  document.getElementById('h-contract').textContent = c.contract;
  document.getElementById('c-vol').innerHTML = fmt(endVol)+' <small>л</small>';
  document.getElementById('c-units').textContent = fmt(c.units);
  document.getElementById('c-sku').textContent = c.sku;
  document.getElementById('c-ld').textContent = fmt(literDays);
  document.getElementById('c-store').innerHTML = fmt(storage)+' <small>₽</small>';
  document.getElementById('c-rate').textContent = fmt2(c.rate);
  document.getElementById('c-orders').textContent = fmt(Math.round(c.orders.sent*pf));
  document.getElementById('c-total').innerHTML = fmt(total)+' <small>₽</small>';

  document.getElementById('inv-period').innerHTML = fmtDate(STATE.from)+' — '+fmtDate(STATE.to)+' · <span id="inv-state"><span class="badge '+c.invState+'"><i></i>'+c.invoice+'</span></span>';
  document.getElementById('inv-total-val').textContent = fmt(total);
  document.getElementById('inv-sum-val').textContent = fmt(total)+' ₽';
  document.getElementById('invLines').innerHTML = [
    { n:'Хранение', d: fmt(literDays)+' литро-дней × '+fmt2(c.rate)+' ₽', v:storage },
    ...ops
  ].map(l=>`<div class="inv-line"><div class="l-name">${l.n}<span>${l.d}</span></div><div class="l-val">${fmt(l.v)} ₽</div></div>`).join('');

  document.getElementById('o-sent').textContent = fmt(Math.round(c.orders.sent*pf));
  document.getElementById('o-pick').textContent = c.orders.pick || '0';
  document.getElementById('o-sla').textContent = c.orders.sla;
  document.getElementById('o-ret').textContent = Math.round(c.orders.ret*pf);

  drawLineChart('cChart','cChartTip','cChartBox', days, c.rate);
  renderSkuTable();
  const recs = (RECEIPTS_BY_CLIENT[c.id]||[]).filter(r=>r.d>=STATE.from && r.d<=STATE.to);
  document.querySelector('#recTbl tbody').innerHTML = (recs.length?recs:[{d:STATE.to,dec:0,acc:0,vol:0,empty:1}]).map(r=>{
    if(r.empty) return `<tr><td colspan="5" style="color:var(--mute-2)">Нет приёмок за период</td></tr>`;
    const diff = r.acc - r.dec;
    return `<tr>
      <td>${fmtDate(r.d)}</td>
      <td class="num">${fmt(r.dec)}</td>
      <td class="num">${fmt(r.acc)}</td>
      <td class="num">${fmt(r.vol)}</td>
      <td>${diff===0?'<span class="badge ok"><i></i>Без расхождений</span>':'<span class="badge bad"><i></i>Расхождение '+diff+' шт</span>'}</td>
    </tr>`;
  }).join('');

  let sIn=0,sOut=0,sVol=0,sRub=0;
  document.querySelector('#cDailyTbl tbody').innerHTML = days.map(d=>{
    const rub = d.vol * c.rate;
    sIn+=d.in; sOut+=d.out; sVol+=d.vol; sRub+=rub;
    return `<tr>
      <td>${fmtDate(d.iso)}</td>
      <td class="num">${d.in?fmt(d.in):'—'}</td>
      <td class="num">${d.out?fmt(d.out):'—'}</td>
      <td class="num">${fmt(d.vol)}</td>
      <td class="num">${fmt2(c.rate)}</td>
      <td class="num money">${fmt(rub)}</td>
    </tr>`;
  }).join('');
  document.querySelector('#cDailyTbl tfoot').innerHTML = `<tr>
    <td>Итого</td>
    <td class="num">${fmt(sIn)}</td>
    <td class="num">${fmt(sOut)}</td>
    <td class="num">${fmt(sVol)} <span class="sub">литро-дни</span></td>
    <td class="num">${fmt2(c.rate)}</td>
    <td class="num money gold">${fmt(sRub)}</td>
  </tr>`;
}

function renderSkuTable(){
  const c = CLIENTS.find(x=>x.id===STATE.clientId) || CLIENTS[0];
  const q = (document.getElementById('skuSearch').value||'').trim().toLowerCase();
  const ST = { ok:['ok','В продаже'], long:['warn','Долгое хранение'], risk:['bad','Расхождение'], new:['neutral','Не выставлен'] };
  let rows = SKU_BY_CLIENT[c.id] || [];
  if(q) rows = rows.filter(s => (s.art+' '+s.name).toLowerCase().includes(q));
  document.querySelector('#skuTbl tbody').innerHTML = rows.length ? rows.map(s=>{
    const [cls,txt] = ST[s.st];
    return `<tr>
      <td class="name">${s.art}<span class="sub">${s.name}</span></td>
      <td class="num">${fmt(s.wh)}</td>
      <td class="num">${s.res?fmt(s.res):'—'}</td>
      <td class="num">${fmt(s.wh-s.res)}</td>
      <td class="num">${fmt(s.vol)}</td>
      <td class="num" ${s.days>90?'style="color:var(--amber)"':''}>${s.days}</td>
      <td>${s.ch.length?s.ch.join(' · '):'<span style="color:var(--mute-2)">нет</span>'}</td>
      <td><span class="badge ${cls}"><i></i>${txt}</span></td>
    </tr>`;
  }).join('') : `<tr><td colspan="8" style="color:var(--mute-2)">Ничего не найдено</td></tr>`;
  const all = SKU_BY_CLIENT[c.id]||[];
  const sum = f => all.reduce((s,r)=>s+r[f],0);
  document.querySelector('#skuTbl tfoot').innerHTML = `<tr>
    <td>Итого · ${all.length} SKU</td>
    <td class="num">${fmt(sum('wh'))}</td>
    <td class="num">${fmt(sum('res'))}</td>
    <td class="num">${fmt(sum('wh')-sum('res'))}</td>
    <td class="num">${fmt(sum('vol'))}</td>
    <td></td><td></td><td></td>
  </tr>`;
}

function renderAll(){
  document.getElementById('dateFrom').value = STATE.from;
  document.getElementById('dateTo').value = STATE.to;
  if(STATE.view==='manager') renderManager();
  else renderClient();
}

function setView(view){
  STATE.view = view;
  document.querySelectorAll('.view').forEach(v=>v.classList.remove('active'));
  document.getElementById('view-'+view).classList.add('active');
  document.querySelectorAll('#viewNav button').forEach(b=>b.classList.toggle('active', b.dataset.view===view));
  document.getElementById('brandSub').textContent = view==='manager' ? 'Панель руководителя' : 'Кабинет клиента';
  document.getElementById('clientWhoWrap').style.display = view==='client' ? '' : 'none';
  renderAll();
}

function setPeriod(period){
  document.querySelectorAll('#periodSwitch button').forEach(b=>b.classList.toggle('active', b.dataset.period===period));
  let from = MIN;
  if(period==='7') from = addDays(MAX,-6);
  else if(period==='30') from = addDays(MAX,-29);
  else if(period==='90') from = addDays(MAX,-89);
  if(from < MIN) from = MIN;
  STATE.from = from; STATE.to = MAX;
  renderAll();
}

function applyTheme(theme){
  document.documentElement.setAttribute('data-theme', theme);
  localStorage.setItem('bg-theme', theme);
  document.getElementById('themeLabel').textContent = theme==='dark' ? 'Тёмная' : 'Светлая';
}

function enterApp(){
  STATE.clientId = STATE.clientId || 'sev';
  document.getElementById('login').classList.add('is-off');
  document.getElementById('app').classList.add('is-on');
  const who = document.getElementById('whoSel');
  who.innerHTML = CLIENTS.map(c=>`<option value="${c.id}">${c.name}</option>`).join('');
  who.value = STATE.clientId;
  document.getElementById('navMgr').style.display = '';
  setView(STATE.view || 'manager');
}

function tryLogin(){
  const u = (document.getElementById('loginUser').value||'').trim().toLowerCase();
  const p = document.getElementById('loginPass').value||'';
  const err = document.getElementById('loginErr');
  const acc = USERS[u];
  if(!acc || acc.pass !== p){ err.classList.add('is-on'); return; }
  err.classList.remove('is-on');
  sessionStorage.setItem('bg-auth', JSON.stringify({u}));
  STATE.view = 'manager';
  enterApp();
}

/* init */
document.getElementById('dateFrom').min = MIN;
document.getElementById('dateFrom').max = MAX;
document.getElementById('dateTo').min = MIN;
document.getElementById('dateTo').max = MAX;
applyTheme(localStorage.getItem('bg-theme') || 'light');

document.getElementById('loginBtn').onclick = tryLogin;
document.getElementById('loginPass').onkeydown = e=>{ if(e.key==='Enter') tryLogin(); };
document.getElementById('logoutBtn').onclick = ()=>{
  sessionStorage.removeItem('bg-auth');
  document.getElementById('app').classList.remove('is-on');
  document.getElementById('login').classList.remove('is-off');
};
document.getElementById('themeToggle').onclick = ()=>{
  applyTheme(document.documentElement.getAttribute('data-theme')==='dark'?'light':'dark');
};
document.getElementById('periodSwitch').onclick = e=>{
  const b = e.target.closest('button'); if(!b) return;
  setPeriod(b.dataset.period);
};
['dateFrom','dateTo'].forEach(id=>{
  document.getElementById(id).onchange = ()=>{
    document.querySelectorAll('#periodSwitch button').forEach(b=>b.classList.remove('active'));
    let from = document.getElementById('dateFrom').value || MIN;
    let to = document.getElementById('dateTo').value || MAX;
    if(from>to){ const t=from; from=to; to=t; }
    STATE.from=from; STATE.to=to;
    renderAll();
  };
});
document.getElementById('viewNav').onclick = e=>{
  const b = e.target.closest('button'); if(!b) return;
  setView(b.dataset.view);
};
document.getElementById('whoSel').onchange = ()=>{
  STATE.clientId = document.getElementById('whoSel').value;
  renderClient();
};
document.getElementById('skuSearch').oninput = renderSkuTable;

(function(){
  let active=null;
  const reset = el=>{ el.style.setProperty('--rx','0deg'); el.style.setProperty('--ry','0deg'); el.style.setProperty('--ty','0px'); el.classList.remove('tilt-active'); };
  document.addEventListener('pointermove', e=>{
    const card = e.target.closest('.tilt');
    if(!card){ if(active){ reset(active); active=null; } return; }
    if(card!==active){ if(active) reset(active); active=card; card.classList.add('tilt-active'); }
    const r=card.getBoundingClientRect();
    const px=(e.clientX-r.left)/r.width, py=(e.clientY-r.top)/r.height;
    card.style.setProperty('--rx', ((px-0.5)*6).toFixed(2)+'deg');
    card.style.setProperty('--ry', ((0.5-py)*6).toFixed(2)+'deg');
    card.style.setProperty('--ty','-3px');
    card.style.setProperty('--mx',(px*100).toFixed(1)+'%');
    card.style.setProperty('--my',(py*100).toFixed(1)+'%');
  });
  document.addEventListener('pointerleave', ()=>{ if(active){ reset(active); active=null; } });
})();

try{
  const saved = JSON.parse(sessionStorage.getItem('bg-auth')||'null');
  if(saved && saved.u){ enterApp(); }
}catch(e){}
