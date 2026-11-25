"use strict";
const meta = window.SNAPSHOT.meta;
const tz = meta.timeZone;
const deals = window.SNAPSHOT.deals;
const byId = new Map(deals.map((deal) => [deal.id, deal]));
const builtAt = Date.parse(meta.builtAt);
const badge = document.querySelector(".demo");
if (badge && meta.accepted) badge.remove();
else if (badge) badge.textContent = "Сверка не закрыта";
const fmt = (value) => new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 }).format(value);
const plural = (count, one, few, many) => { const n = Math.abs(count) % 100; const n1 = n % 10; if (n > 10 && n < 20) return many; if (n1 === 1) return one; if (n1 > 1 && n1 < 5) return few; return many; };
const percent = (part, whole) => whole ? fmt(100 * part / whole) + "%" : "—";
const pad = (value) => String(value).padStart(2, "0");
const dayKey = (ms) => { const zone = zonedParts(ms, tz); return `${zone.y}-${pad(zone.m)}-${pad(zone.d)}`; };
const dateFmt = (ms, month = "short") => new Intl.DateTimeFormat("ru-RU", { timeZone: tz, day: "numeric", month, year: "numeric" }).format(ms).replace(" г.", "");
const stamp = (ms) => `${dateFmt(ms)} · ${new Intl.DateTimeFormat("ru-RU", { timeZone: tz, hour: "2-digit", minute: "2-digit" }).format(ms)}`;
const CHANNEL = { off: "Офлайн", on: "Онлайн", unknown: "Канал не определён" };
const touchText = { yes: "С касанием", no: "Без касания", unknown: "Касание не определено" };
const titles = { home: "Статистика", crm: "Статистика CRM", quality: "Качество работы CRM", general: "Общая статистика", effect: "Эффективность CRM", log: "Логистика", queue: "Сейчас в работе", review: "Границы среза" };
const intro = {
  home: "Цифры этого среза посчитаны из amoCRM. Где источника нет, на экране так и написано.",
  crm: "Выбери вопрос, на который нужен ответ.",
  quality: "Сроки задач и этапов по закрытым сделкам. Время ответа считается по сообщениям Wazzup.",
  general: "Сколько обращений становятся покупками и из каких источников они приходят.",
  effect: "С каких этапов заявки доходят до встречи или передачи на сборку.",
  log: "Как товар доходит до магазина и до клиента по этапам CRM.",
  queue: "Открытые заявки трёх воронок продаж, включая неразобранное.",
  review: "Что в этом срезе сверено, а что ещё нельзя называть готовой цифрой.",
};
const icons = { crm: '<path d="M4 19V5m0 14h16M8 15v-4m5 4V7m5 8v-6"/>', log: '<path d="M3 6h11v10H3zM14 10h4l3 4v2h-7z"/><circle cx="7" cy="18" r="2"/><circle cx="18" cy="18" r="2"/>', queue: '<rect x="3" y="4" width="18" height="16" rx="3"/><path d="M8 4v16m4-11h5m-5 4h3"/>', right: '<path d="m9 5 7 7-7 7"/>', left: '<path d="m15 5-7 7 7 7"/>', goods: '<path d="m3 8 9-5 9 5v9l-9 5-9-5zM3 8l9 5 9-5m-9 5v9"/>', users: '<circle cx="10" cy="8" r="3"/><path d="M3 20v-2a7 7 0 0 1 14 0v2m-1-15a3 3 0 0 1 0 6m3 3c1 .8 2 2.5 2 5"/>', quality: '<path d="m3 12 4 0 3-7 4 14 3-7h4"/>', general: '<path d="M3 5h18l-7 8v5l-4 2v-7z"/>', effect: '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="4"/><path d="m12 12 8-8"/>' };
const ic = (name) => `<svg class="icon" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">${icons[name] ?? ""}</svg>`;
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
const app = document.querySelector("#app");
const drawer = document.querySelector("#drawer");
const embedded = window.self !== window.top;
if (embedded) document.body.classList.add("embedded");
let page = Object.hasOwn(titles, location.hash.slice(1)) ? location.hash.slice(1) : "home";
let actions = new Map();
let dialogActions = new Map();
let serial = 0;
const state = { kind: "week", date: meta.week.from + 3600000, mode: "results", source: "*", touch: "*", lane: "off", explain: false, outcomes: new Set(["success", "fail", "open"]), reasons: new Set(), reasonMenu: false };
function reg(fn, dialog = false) { const key = String(++serial); (dialog ? dialogActions : actions).set(key, fn); return `data-act="${key}"`; }
function nav(next) { if (!Object.hasOwn(titles, next)) return; if (drawer.open) drawer.close(); if (!embedded && location.hash !== "#" + next) { location.hash = next; return; } page = next; render(); window.scrollTo(0, 0); }
addEventListener("hashchange", () => { page = Object.hasOwn(titles, location.hash.slice(1)) ? location.hash.slice(1) : "home"; if (drawer.open) drawer.close(); render(); window.scrollTo(0, 0); });
document.addEventListener("click", (event) => { const el = event.target.closest("[data-page],[data-act]"); if (!el) return; if (el.dataset.page) nav(el.dataset.page); else (dialogActions.get(el.dataset.act) ?? actions.get(el.dataset.act))?.(); });
drawer.addEventListener("click", (event) => { if (event.target === drawer) drawer.close(); });
drawer.addEventListener("keydown", (event) => { if (event.key === "Escape") drawer.close(); });
document.addEventListener("keydown", (event) => { if (event.key === "Escape" && drawer.open) drawer.close(); }, true);
document.querySelector("#close").onclick = () => drawer.close();
drawer.addEventListener("close", () => { dialogActions.clear(); document.body.style.overflow = ""; });
function show(title, note, html) { document.querySelector("#dlgtitle").textContent = title; document.querySelector("#dlgmeta").textContent = note; document.querySelector("#dlgbody").innerHTML = html; if (!drawer.open) { drawer.showModal(); document.body.style.overflow = "hidden"; } drawer.querySelector(".sheet").scrollTop = 0; }
const empty = (text) => `<div class="empty"><strong>Нет записей</strong>${text}</div>`;
const bounds = () => periodBounds(state.kind, state.date, builtAt, tz, meta.weekStartsOn);
const modeOf = () => state.mode === "created" ? "sources" : "results";
const ready = () => periodCovered(modeOf(), bounds(), meta.coverage);
function rows() { return ready() ? cohort(deals, modeOf(), bounds(), { touch: state.touch, source: state.mode === "created" ? state.source : "*", lane: "*" }) : []; }
function replyListMeta(row, cutoff) {
  if (!row) return null;
  const { stat, max, pending } = row;
  const slow = pending || (max != null && max >= cutoff);
  if (pending && max == null) return { html: `<span class="replytag">без ответа</span>`, slow: true };
  const count = stat.series.length;
  const tail = count > 1 ? `<small class="replycount">${count} ${plural(count, "ответ", "ответа", "ответов")}</small>` : "";
  return { html: `<span class="replytag">макс. ${esc(fmt(max))} мин</span>${tail}`, slow };
}
function replyRows(deal, cutoff = 60) {
  const stat = wazzupReplies(deal.messages, tz);
  if (!stat.series.length && !stat.pending) return "";
  const max = stat.series.length ? Math.max(...stat.series.map((item) => item.minutes)) : null;
  const rows = stat.series.map((item) => {
    const mark = max != null && item.minutes === max && (stat.series.length > 1 || item.minutes >= cutoff);
    return `<div class="dlgrow${mark ? " highlight" : ""}"><span>Вопрос ${esc(stamp(item.from))}<br>ответ ${esc(stamp(item.to))}</span><strong>${esc(fmt(item.minutes))} мин${mark ? " · долгий" : ""}</strong></div>`;
  }).join("");
  const waiting = stat.pending ? `<div class="dlgrow highlight"><span>Серия без ответа с ${esc(stamp(stat.pendingFrom))}</span><strong>ещё нет</strong></div>` : "";
  return `<h3 class="sectiontitle">Время ответа</h3>${rows}${waiting}`;
}
function openReplyList(title, audit, explain, onlySlow = false) {
  const records = onlySlow ? audit.slow : audit.all;
  const byId = new Map(audit.rows.map((row) => [row.deal.id, row]));
  const cutLabel = fmt(Math.ceil(audit.cutoff));
  const note = explain + (onlySlow ? ` Порог проверки: от ${cutLabel} рабочих минут или серия без ответа.` : ` Сверху самые долгие. Для проверки откройте «Долгие ответы»: от ${cutLabel} рабочих минут или серия без ответа.`);
  list(title, records, note, (deal) => replyListMeta(byId.get(deal.id), audit.cutoff));
}
function list(title, records, explain, timeOf) {
  const openList = (limit = 40) => {
    dialogActions.clear();
    const items = records.slice(0, limit).map((deal) => {
      const time = timeOf ? timeOf(deal) : null;
      const timed = Boolean(time);
      const slow = timed && time.slow;
      const timeHtml = timed ? `<b class="replytime${slow ? " slow" : ""}">${time.html}</b>` : "";
      return `<button class="item${timed ? " timed" : ""}${slow ? " slowreply" : ""}" ${reg(() => detail(deal), true)}><span><strong>${deal.id}</strong> · ${esc(deal.stage || deal.result || "Без этапа")}<small>${esc(dateFmt(deal.created) + " · " + (CHANNEL[deal.lane] ?? "") + " · " + deal.source)}</small></span>${timeHtml}${ic("right")}</button>`;
    }).join("");
    show(title, `${fmt(records.length)} записей`, (explain ? `<p class="note">${esc(explain)}</p>` : "") + (items ? `<div class="stack">${items}</div>` : empty("По выбранным условиям ничего не найдено.")) + (limit < records.length ? `<p class="actions"><button class="pill" ${reg(() => openList(limit + 60), true)}>Показать ещё</button></p>` : ""));
  };
  openList();
}
function detail(deal) {
  dialogActions.clear();
  const cut = bounds().cut;
  const target = isTarget(deal, cut, modeOf());
  const qual = isQual(deal, cut, modeOf());
  const props = [["Создана", stamp(deal.created)], ["Канал", CHANNEL[deal.lane] ?? "Канал не определён"], ["Источник", deal.source], ["Касание", touchText[deal.touch] ?? touchText.unknown], ["Ответственный", deal.owner], ["Целевая · квал", `${target ? "Да" : "Нет"} · ${qual ? "Да" : "Нет"}`]];
  if (deal.closed) props.push(["Завершена", stamp(deal.closed)]);
  if (deal.result === "Провал") props.push(["Причина провала", deal.reason]);
  if (deal.kassaNumber) props.push(["Заявка кассы", String(deal.kassaNumber)]);
  const history = (deal.history ?? []).map((event) => `<li>${esc(event.stage)}<small>${stamp(event.at)}</small></li>`).join("");
  const tasks = (deal.tasks ?? []).map((task) => `<div class="dlgrow"><span class="taskname">${esc(task.name)}</span><strong>Срок ${stamp(task.due)}<br><small class="tiny">${task.done ? "Обновлена " + stamp(task.done) + ". Отдельного времени выполнения amo не отдаёт." : "Ещё открыта"}</small></strong></div>`).join("");
  const audit = replyAudit([deal], tz);
  show("Заявка " + deal.id, deal.result ?? "В работе", `<p><a class="link" href="https://mansband.amocrm.ru/leads/detail/${deal.id}" target="_blank" rel="noreferrer">Открыть в amoCRM</a>${deal.kassaNumber ? ` · <a class="link" href="https://mansband-kassa.ru/#board/all/all/${deal.kassaNumber}" target="_blank" rel="noreferrer">Заявка кассы ${deal.kassaNumber}</a>` : ""}</p>` + props.map(([key, value]) => `<div class="dlgrow"><span>${esc(key)}</span><strong>${esc(value)}</strong></div>`).join("") + replyRows(deal, audit.cutoff) + `<h3 class="sectiontitle">История этапов</h3>${history ? `<ol class="timeline">${history}</ol>` : empty(deal.historyComplete ? "Смен этапа нет." : "История этапа неполная, исход по ней не достраивается.")}` + (tasks ? `<h3>Задачи</h3>${tasks}` : ""));
}
function methodology() {
  show("Как читать статистику", "Правила расчёта", `<div class="stack"><section><h3>Результаты</h3><p class="note">Качество, общая статистика, эффективность и логистика берут сделки, закрытые в выбранный период. Успех и провал в сумме дают все закрытые. Возврат и обмен уже закрытый успех не уменьшают.</p></section><section><h3>Источники</h3><p class="note">Сделки группируются по дате создания. Успех и провал берутся из событий этапа на конец периода. Если история обрывается, заявка остаётся в общем числе и не попадает ни в успех, ни в провал.</p></section><section><h3>Целевые и квалы</h3><p class="note">Целевая: дата взятия в работу не позже границы или успех уже наступил. Квал: дата квалификации или успех. Провал «Спам» квалом не считается. Чекбокс «Целевой» число не меняет. Квал без даты взятия в работу в целевые не дописывается.</p></section><section><h3>Сроки</h3><p class="note">Просрочка закрытого периода считается только там, где срок сохранился на карточке или в задаче. «Сейчас просрочено» считается отдельно, по правилам ежедневной сводки, и с закрытым периодом не складывается.</p></section></div>`);
}
const openButton = (target, cls, body) => `<button class="${cls}" data-page="${target}">${body}</button>`;
const badgeIcon = (name) => `<span class="badgeicon">${ic(name)}</span>`;
const chev = `<span class="round">${ic("right")}</span>`;
function spark(values) {
  if (!values.length) return "";
  const max = Math.max(1, ...values);
  const width = 100 / values.length;
  return `<svg aria-hidden="true" class="spark" viewBox="0 0 100 32" preserveAspectRatio="none">${values.map((value, index) => `<rect x="${index * width}" y="${32 - 30 * value / max}" width="${Math.max(0.8, width - 1.7)}" height="${Math.max(0, 30 * value / max)}" rx="1" opacity="${0.5 + 0.5 * (index + 1) / values.length}" fill="currentColor"/>`).join("")}</svg>`;
}
function archivedQueue() {
  const from = Number(meta.queueArchiveFrom);
  if (!meta.queueComplete || !Number.isFinite(from)) return [];
  return queueHistory(deals, builtAt, tz).filter((point) => point.at >= from);
}
function home() {
  const period = bounds();
  const closed = periodCovered("results", period, meta.coverage) ? cohort(deals, "results", period, {}) : [];
  const logistics = chain("logistics", state.lane, new Set(meta.liveStages));
  const goal = logistics.at(-1) ?? "";
  const delivered = closed.filter((deal) => deal.lane === state.lane && deal.historyComplete && deal.history.some((event) => event.stage === goal));
  const queue = meta.queueComplete ? queueAt(deals, builtAt) : [];
  const trail = archivedQueue();
  return `<p class="main-period">Срез ${stamp(builtAt)} · ${meta.week.label}</p><div class="cards">${openButton("crm", "entry hero", `<span class="rowhead">${badgeIcon("crm")}${chev}</span><h2>Статистика CRM</h2><p class="aside">Обращения, качество работы и путь до цели.</p><div class="cardfoot"><div><div class="big">${closed.length ? fmt(closed.length) : "—"}</div><div class="caption">${closed.length ? "завершённых заявок в покрытом периоде" : "период вне среза"}</div></div></div>`)}${openButton("log", "entry log", `<span class="rowhead">${badgeIcon("log")}${chev}</span><h2>Логистика</h2><p class="aside">Путь товара до магазина и до клиента.</p><div class="cardfoot"><div><div class="big">${closed.length ? fmt(delivered.length) : "—"}</div><div class="caption">${esc(CHANNEL[state.lane] + " · " + (goal || "цель не найдена в живой воронке"))}</div></div></div>`)}</div>` +
    openButton("queue", "entry wide", `<div class="cardtitle">${badgeIcon("queue")}<div><h2>Сейчас в работе</h2><p class="aside">Открытые заявки, включая неразобранное.</p></div></div><div class="flex">${spark(trail.map((point) => point.ids.length))}<strong class="big">${meta.queueComplete ? fmt(queue.length) : "—"}</strong>${chev}</div>`) +
    `<h2 class="sectiontitle">Следующие разделы</h2><div class="futuregrid">${[["goods", "Товары"], ["users", "Консультанты"]].map(([name, label]) => `<div class="future">${ic(name)}<h3>${label}</h3><span class="chip">В планах</span></div>`).join("")}</div>`;
}
function crm() { return `<div class="three">${[["quality", "Качество работы", "Сроки задач, этапов и ответ в Wazzup."], ["general", "Общая статистика", "Целевые заявки, покупки и источники."], ["effect", "Эффективность", "На каких этапах заявки не доходят до цели."]].map(([target, title, text]) => openButton(target, "entry", `<span class="rowhead">${badgeIcon(target)}${chev}</span><h2>${title}</h2><p class="aside">${text}</p>`)).join("")}</div>`; }
function segment(key, options) { return `<div class="seg">${options.map(([value, label]) => `<button aria-pressed="${state[key] === value}" ${reg(() => { state[key] = value; render(); })}>${label}</button>`).join("")}</div>`; }
function shift(dir) { const period = bounds(); state.date = dir < 0 ? period.from - 3600000 : period.to + 3600000; render(); }
function toolbar() {
  const period = bounds();
  const cut = period.cut < period.to;
  return `<div class="toolbar">${segment("kind", [["day", "День"], ["week", "Неделя"], ["month", "Месяц"], ["year", "Год"]])}<div class="cal"><button class="step" aria-label="Предыдущий период" ${reg(() => shift(-1))}>${ic("left")}</button><input class="date" type="date" id="anchor" aria-label="Дата внутри периода" value="${dayKey(state.date)}"><button class="step" aria-label="Следующий период" ${reg(() => shift(1))}>${ic("right")}</button></div><span class="periodlabel">${dateFmt(period.from)} — ${dateFmt(period.cut - 1)}${cut ? " · период обрезан временем среза" : ""}${state.kind === "week" && !meta.weekStartConfirmed ? " · неделя с понедельника, день недели amo не прочитан" : ""}</span></div>`;
}
function select(key, label, options) { return `<label class="filter">${label}<select data-select="${key}">${options.map(([value, name]) => `<option value="${esc(value)}" ${state[key] === value ? "selected" : ""}>${esc(name)}</option>`).join("")}</select></label>`; }
document.addEventListener("toggle", (event) => { if (event.target.classList?.contains("reasonbox")) state.reasonMenu = event.target.open; }, true);
document.addEventListener("change", (event) => {
  if (event.target.id === "explain") { state.explain = event.target.checked; render(); }
  else if (event.target.id === "anchor") { const [y, m, d] = event.target.value.split("-").map(Number); if (y) state.date = zonedTimeToUtc(y, m, d, 12, 0, 0, tz); render(); }
  else if (event.target.dataset.outcome) {
    const key = event.target.dataset.outcome;
    if (event.target.checked) state.outcomes.add(key); else state.outcomes.delete(key);
    render();
  } else if (event.target.dataset.reason) {
    const name = event.target.dataset.reason;
    if (event.target.checked) state.reasons.add(name); else state.reasons.delete(name);
    state.reasonMenu = true;
    render();
  } else if (event.target.dataset.select) { state[event.target.dataset.select] = event.target.value; render(); }
});
function failReasonNames(rows, mode, period) {
  const names = new Set();
  for (const deal of rows) if (dealOutcome(deal, mode, period) === "fail") names.add(deal.reason || "Причина не указана");
  return [...names].sort((a, b) => a.localeCompare(b, "ru"));
}
function outcomeBar(rows, mode, period) {
  const names = failReasonNames(rows, mode, period);
  for (const name of [...state.reasons]) if (!names.includes(name)) state.reasons.delete(name);
  const checks = [["success", "Успех"], ["fail", "Провал"], ["open", "В работе"]].map(([key, label]) => `<label><input type="checkbox" data-outcome="${key}" ${state.outcomes.has(key) ? "checked" : ""}>${label}</label>`).join("");
  const summary = state.reasons.size === 0 ? "все" : [...state.reasons].join(", ");
  const menu = names.length ? names.map((name) => `<label><input type="checkbox" data-reason="${esc(name)}" ${state.reasons.has(name) ? "checked" : ""}>${esc(name)}</label>`).join("") + (state.reasons.size ? `<button type="button" class="link" ${reg(() => { state.reasons.clear(); state.reasonMenu = true; render(); })}>Сбросить</button>` : "") : `<p class="note">Провалов в этом периоде нет.</p>`;
  return `<div class="filterbar"><fieldset class="checks"><legend>Исход</legend>${checks}</fieldset><details class="reasonbox" ${state.reasonMenu ? "open" : ""}><summary>Причина провала · ${esc(summary)}</summary><div class="menu">${menu}</div></details></div>`;
}
function pickedDeals(rows, mode, period) {
  const openRows = mode === "results" ? queueAt(deals, Math.max(period.from, period.cut - 1)) : [];
  const picked = restrictDeals(rows, openRows, mode, period, { outcomes: state.outcomes, reasons: state.reasons });
  const addedOpen = picked.some((deal) => !rows.includes(deal));
  return { picked, addedOpen };
}
function metric(name, value, sub, fn, tone = "") { return `<button class="metric" ${reg(fn)}><span>${name}</span><strong class="${tone}">${value}</strong><small>${sub}</small></button>`; }
const panel = (title, text, body) => `<section class="panel"><h2>${title}</h2>${text ? `<p class="note aside">${text}</p>` : ""}${body}</section>`;
function barRow(label, count, total, fn, sub = "", rowClass = "") { return `<button class="stage${rowClass ? " " + rowClass : ""}" ${reg(fn)}><span>${esc(label)}${sub ? `<small>${esc(sub)}</small>` : ""}</span><span class="track"><i style="width:${total ? 100 * count / total : 0}%"></i></span><span class="alignr"><b>${count}</b><small>${percent(count, total)}</small></span></button>`; }
function missingSource(title, reason) { show(title, "Нет источника", `<p class="note">${esc(reason)}</p>`); }
function generalReport() {
  if (!ready()) return uncovered();
  const period = bounds();
  const mode = modeOf();
  const sourcePool = cohort(deals, mode, period, { touch: state.touch });
  const sources = [...new Set(sourcePool.map((deal) => deal.source))].sort((a, b) => a.localeCompare(b, "ru"));
  if (state.source !== "*" && !sources.includes(state.source)) state.source = "*";
  const narrowed = cohort(deals, mode, period, { touch: state.touch, source: state.mode === "created" ? state.source : "*" });
  const { picked, addedOpen } = pickedDeals(narrowed, mode, period);
  if (state.outcomes.size === 0) return `<div class="filterbar">${segment("mode", [["results", "Результаты"], ["created", "Источники"]])}${select("touch", "Касание", [["*", "Все заявки"], ["yes", "С касанием"], ["no", "Без касания"]])}</div>${outcomeBar(narrowed, mode, period)}${empty("Отметьте хотя бы один исход: успех, провал или в работе.")}`;
  const groups = general(picked, mode, period, { ready: true });
  const order = [["all", "Все заявки"], ["target", "Целевые"], ["qual", "Квалы"], ["success", "Успех"], ["fail", "Провал"]];
  const den = { target: groups.all.length, qual: groups.target.length, success: groups.all.length, fail: groups.all.length };
  const suffix = { target: "от всех", qual: "от целевых", success: "от всех", fail: "от всех" };
  let html = `<div class="filterbar">${segment("mode", [["results", "Результаты"], ["created", "Источники"]])}${select("touch", "Касание", [["*", "Все заявки"], ["yes", "С касанием"], ["no", "Без касания"]])}${state.mode === "created" ? select("source", "Источник", [["*", "Все источники"], ...sources.map((source) => [source, source])]) : ""}</div>${outcomeBar(narrowed, mode, period)}`;
  if (addedOpen) html += `<p class="note">В работе здесь — заявки, которые на конец периода ещё не закрыты.</p>`;
  html += `<p class="hintline aside">${state.mode === "created" ? "По дате создания. Исход на конец периода берётся из событий этапа." : "По дате завершения. Успех и провал в сумме равны всем закрытым сделкам."}</p>`;
  html += `<div class="metrics">${order.map(([key, label], index) => metric(label, fmt(groups[key].length), key === "all" ? (state.mode === "created" ? "создано за период" : "завершено за период") : percent(groups[key].length, den[key]) + " " + suffix[key], () => list(label, groups[key], "Состав показателя."), index === 3 ? "accent" : index === 4 ? "bad" : "")).join("")}</div>`;
  if (state.mode === "created") {
    const outside = qualOutsideTarget(groups, "sources", period);
    const ranked = (state.source === "*" ? sources : [state.source]).map((source) => [source, general(groups.all, "sources", period, { source })]).sort((a, b) => b[1].all.length - a[1].all.length);
    html += panel("Что дал каждый источник?", "Текущее значение поля «Источник». Технический источник число не меняет.", `<div class="tablewrap source-table"><table><thead><tr><th>Источник</th>${order.map(([, label]) => `<th>${label}</th>`).join("")}</tr></thead><tbody>${ranked.map(([source, group]) => `<tr><td><button class="sourcename" ${reg(() => list(source, group.all, "Все заявки этого источника."))}>${esc(source)}</button></td>${order.map(([key, label]) => `<td><button ${reg(() => list(source + " · " + label, group[key], "Заявки этого источника."))}><span class="source-label">${label}</span>${group[key].length}</button></td>`).join("")}</tr>`).join("")}</tbody></table></div>`);
    html += `<p class="note">На конец периода в работе: ${fmt(groups.all.length - groups.success.length - groups.fail.length - groups.unknown.length)}. Исход неизвестен: ${fmt(groups.unknown.length)}. Квалы без даты взятия в работу: ${fmt(outside.length)}.</p>`;
  } else {
    const reasons = [...new Set(groups.fail.map((deal) => deal.reason))].map((reason) => [reason, groups.fail.filter((deal) => deal.reason === reason)]).sort((a, b) => b[1].length - a[1].length);
    html += panel("Почему сделки закрыли в провал?", "Доля от всех провалов. Сдвоенные карточки не склеиваются.", reasons.length ? `<div class="stack">${reasons.map(([reason, group]) => barRow(reason, group.length, groups.fail.length, () => list(reason, group, "Причина закрытия."))).join("")}</div>` : empty("Провалов за период нет."));
    if (!addedOpen && state.outcomes.size === 3 && state.reasons.size === 0 && groups.success.length + groups.fail.length !== groups.all.length) html += `<p class="hintline">Ошибка расчёта: успех и провал не дают сумму закрытых сделок.</p>`;
  }
  return html + qualityLine(groups.all) + `<details class="aside"><summary>Как определяются целевые, квалы и касание?</summary><p>Успех автоматически считается и целевым, и квалом. Провал сам по себе целевым не становится. Без касания: заявки раньше не было, и консультант создал её сразу в успехе или провале. Если следа недостаточно, касание не определено и в оба фильтра не входит.</p></details>`;
}
function qualityLine(group) {
  const items = [
    ["Нет источника", group.filter((deal) => deal.source === "Не указан")],
    ["Конфликт двух источников", group.filter((deal) => deal.sourceConflict)],
    ["Конфликт чекбокса «Целевой»", checkboxDisagreements(group)],
    ["Нет типа клиента", group.filter((deal) => deal.pipelineId === 9601214 && deal.lane === "unknown" && !deal.laneConflict)],
    ["Нет причины провала", group.filter((deal) => deal.result === "Провал" && deal.reason === "Причина не указана")],
    ["Этап был, дата квалификации не записана", group.filter((deal) => deal.historyComplete && deal.qualAt == null && deal.result !== "Успех" && deal.history.some((event) => ["Встреча назначена", "Передан на сборку", "Сертификат оплачен"].includes(event.stage)))],
    ["Касание не определено", group.filter((deal) => deal.touch === "unknown")],
    ["Конфликт воронки и типа клиента", group.filter((deal) => deal.laneConflict)],
  ].filter(([, rows]) => rows.length);
  if (!items.length) return panel("Качество данных", "В этой выборке пустые и конфликтные поля не найдены.", "");
  return panel("Качество данных", "Эти строки видны отдельно и не меняют основные числа.", `<div class="stack">${items.map(([label, rows]) => barRow(label, rows.length, group.length, () => list(label, rows, "Заявки, которые не удалось классифицировать однозначно."))).join("")}</div>`);
}
function qualityReport() {
  if (!periodCovered("results", bounds(), meta.coverage)) return uncovered();
  const period = bounds();
  const closed = cohort(deals, "results", period, {});
  if (state.outcomes.size === 0) return outcomeBar(closed, "results", period) + empty("Отметьте хотя бы один исход: успех, провал или в работе.");
  const { picked, addedOpen } = pickedDeals(closed, "results", period);
  const group = picked;
  const tasks = overdueTasks(group);
  const stages = overdueStages(group, builtAt);
  const replies = responseSummary(group, tz);
  const audit = replyAudit(replies.deals, tz);
  const chatReady = meta.chat === "wazzup";
  const cards = [
    metric("Среднее время ответа", chatReady ? (replies.mean == null ? "—" : fmt(replies.mean) + " мин") : "нет источника", chatReady ? (replies.count ? fmt(replies.count) + " " + plural(replies.count, "ответ", "ответа", "ответов") + " · 10–23" : "нет ответов") : "Wazzup не прочитан", () => chatReady ? openReplyList("Ответы Wazzup", audit, "Серии сообщений Wazzup, в которых был ответ. С 23:00 до 10:00 МСК минуты не идут.") : missingSource("Среднее время ответа", QUALITY_SOURCES.response.reason)),
    metric("Долгие ответы", chatReady ? fmt(audit.slow.length) : "—", chatReady ? (audit.slow.length ? `от ${fmt(Math.ceil(audit.cutoff))} мин или без ответа` : "все ответы короче порога") : "Wazzup не прочитан", () => chatReady ? openReplyList("Долгие ответы", audit, "Сделки, где хотя бы один ответ длиннее порога или осталась серия без ответа.", true) : missingSource("Долгие ответы", QUALITY_SOURCES.response.reason), audit.slow.length ? "bad" : ""),
    metric("Просроченные задачи", fmt(tasks.length), "задач", () => list("Просроченные задачи", tasks.map((row) => row.deal), "Время выполнения взято из updated_at: отдельного поля amo не отдаёт.")),
    metric("Просроченные этапы", fmt(stages.overdue.length), "этапов", () => list("Просроченные этапы", stages.overdue.map((row) => row.deal), "Сегодняшний календарный день ещё не просрочка. Срок без сохранившейся даты сюда не входит.")),
  ];
  const byName = new Map();
  for (const row of tasks) { const list = byName.get(row.task.name) ?? []; list.push(row.deal); byName.set(row.task.name, list); }
  const byStage = new Map();
  for (const row of stages.overdue) { const list = byStage.get(row.stage) ?? []; list.push(row.deal); byStage.set(row.stage, list); }
  const digest = meta.digest?.complete
    ? `<p class="note">Сейчас просрочено: задачи ${fmt(meta.digest.tasks)}, этапы ${fmt(meta.digest.stages)}.</p><p class="note aside">Это другое число и с периодом не складывается. Без обязательной даты: ${fmt(meta.digest.missing)}. Задач с недоступной карточкой: ${fmt(meta.digest.unavailable)}.${chatReady ? ` Неотвеченных серий Wazzup: ${fmt(replies.pending)}.` : ""}</p>`
    : `<p class="note">Сейчас просрочено в этот срез не вошло.</p><p class="note aside">Список открытых задач собран не полностью.</p>`;
  return `${outcomeBar(closed, "results", period)}${addedOpen ? `<p class="note">В работе здесь — заявки, которые на конец периода ещё не закрыты.</p>` : ""}<p class="hintline aside">По дате завершения. Просрочка периода включает уже выполненные задачи. Срок, который не сохранился, в просрочку не ставится: таких пребываний ${fmt(stages.missing.length)}. Ответ считается от первого входящего Wazzup в серии до следующего исходящего, по сообщениям за 90 дней до начала недели. С 23:00 до 10:00 МСК счёт стоит и продолжается в 10:00. Звонки не входят.</p><div class="metrics">${cards.join("")}</div><div class="double">${groupPanel("Какие задачи просрочили?", byName, tasks.length, "taskname")}${groupPanel("На каких этапах задержались?", byStage, stages.overdue.length)}</div>${digest}`;
}
function groupPanel(title, map, total, rowClass = "") {
  const entries = [...map.entries()].sort((a, b) => b[1].length - a[1].length);
  return panel(title, "Открой строку, чтобы увидеть заявки.", entries.length ? `<div class="stack">${entries.map(([label, group]) => barRow(label, group.length, total, () => list(label, group, "Заявки этой строки."), "", rowClass)).join("")}</div>` : empty("Таких записей нет."));
}
function funnelReport(logistics) {
  if (!periodCovered("results", bounds(), meta.coverage)) return uncovered();
  const period = bounds();
  const closed = cohort(deals, "results", period, {});
  const filtered = logistics ? closed : (state.outcomes.size === 0 ? [] : pickedDeals(closed, "results", period).picked);
  const addedOpen = !logistics && filtered.some((deal) => !closed.includes(deal));
  if (!logistics && state.outcomes.size === 0) return outcomeBar(closed, "results", period) + empty("Отметьте хотя бы один исход: успех, провал или в работе.");
  const stages = chain(logistics ? "logistics" : "effect", state.lane, new Set(meta.liveStages));
  const group = filtered.filter((deal) => deal.lane === state.lane);
  const report = funnel(group, stages);
  const aside = filtered.filter((deal) => deal.lane !== state.lane);
  const rowsHtml = report.analysis.map((item) => `<button class="stage" ${reg(() => stageDetail(item, report.goal))}><span><strong>${esc(item.stage)}</strong><small>Побывали · ${item.visited.length}</small></span><span class="track"><i style="width:${item.visited.length ? 100 * item.hit.length / item.visited.length : 0}%"></i></span><span class="alignr"><b>${item.hit.length}</b> до цели<small>${percent(item.hit.length, item.visited.length)} от этапа</small></span></button>`).join("");
  const rest = `<div class="stack">${barRow("Не были ни на одном этапе цепочки", report.remainder.length, group.length, () => list("Вне цепочки", report.remainder, "Закрытые сделки этого канала без этапов выбранной цепочки."))}${barRow("История этапа неполная", report.incomplete.length, group.length, () => list("История неполная", report.incomplete, "Эти заявки не входят в числитель и знаменатель этапов."))}</div>`;
  const speed = logistics && stages.length ? medianHours(group, stages) : null;
  const speedPanel = speed ? panel("Сколько времени занимает путь?", `От этапа «${stages[0]}» до «${stages.at(-1)}». В медиану входят только сделки, где оба события есть и идут в этом порядке.`, `<button class="item" ${reg(() => list("Полный путь", speed.ids.map((id) => byId.get(id)).filter(Boolean), "Заявки, по которым посчитана медиана."))}><span>Медианное время<small>${fmt(speed.ids.length)} заявок с полным путём</small></span><strong>${speed.hours == null ? "—" : fmt(speed.hours) + " ч"}</strong></button>`) : "";
  return `${logistics ? "" : outcomeBar(closed, "results", period)}${addedOpen ? `<p class="note">В работе здесь — заявки, которые на конец периода ещё не закрыты.</p>` : ""}<div class="filterbar">${segment("lane", [["off", "Офлайн"], ["on", "Онлайн"]])}<span class="periodlabel">По дате завершения</span></div>` +
    panel("Цель · " + (report.goal || "в живой воронке не найдена"), "Повторный вход заявку не удваивает. Цель считается, только если она наступила в этот заход или позже. Имя этапа берётся из amo.", stages.length ? `<div class="stack">${rowsHtml}</div>` : empty("В живой воронке нет этапов этой цепочки.")) +
    speedPanel +
    panel("Чтобы сумма сходилась с закрытыми", "Сделки вне цепочки и сделки с неполной историей показаны отдельно.", rest) +
    (aside.length ? `<p class="note">Другой канал: ${fmt(aside.length)}</p>` : "") +
    `<details class="aside"><summary>Как считается достижение цели?</summary><p>Одна заявка на этап. Сертификат, аренда и «не готов» в эти цепочки не входят. Успех без посещения цели остаётся в «не дошли» как успех без этого этапа. Медиана логистики считается отдельно и не меняет число дошедших до цели. Строка «Другой канал» — это закрытые сделки, которые не попали в выбранный канал.</p></details>`;
}
function stageDetail(item, goal) {
  const missed = item.missed;
  const byReason = new Map();
  for (const deal of missed) {
    const reason = deal.result === "Провал" ? (deal.reason || "Причина не указана") : "Успех без этого этапа";
    byReason.set(reason, (byReason.get(reason) ?? 0) + 1);
  }
  const why = [...byReason.entries()].sort((a, b) => b[1] - a[1]).map(([name, count]) => `<li>${esc(name)} · ${count}</li>`).join("");
  show(item.stage, "Цель · " + goal, `<p class="note">Из ${item.visited.length} заявок, которые побывали на этапе, до цели дошли ${item.hit.length} (${percent(item.hit.length, item.visited.length)}).</p><div class="stack">${[["Побывали на этапе", item.visited], ["Дошли до цели", item.hit], ["Не дошли до цели", missed]].map(([label, group]) => `<button class="item" ${reg(() => list(label, group, "Этап «" + item.stage + "»."), true)}><span>${label}</span><strong>${group.length}</strong></button>`).join("")}</div>${why ? `<h3 class="sectiontitle">Исходы заявок, не дошедших до цели</h3><ul class="reasonlist">${why}</ul>` : ""}`);
}
function queuePoint(point, limit = 40) {
  const records = point.ids.map((id) => byId.get(id)).filter(Boolean).sort((a, b) => a.created - b.created);
  dialogActions.clear();
  const items = records.slice(0, limit).map((deal) => {
    const then = stageAt(deal, point.at);
    return `<button class="item" ${reg(() => detail(deal), true)}><span><strong>${deal.id}</strong> · ${esc(then || "этап на эту дату не восстановлен")}<small>${esc(dateFmt(deal.created) + " · открыта на конец дня")}</small></span>${ic("right")}</button>`;
  }).join("");
  show("В работе · " + dateFmt(point.at), `${fmt(records.length)} заявок`, (items || empty("На конец этого дня открытых заявок нет.")) + (limit < records.length ? `<p class="actions"><button class="pill" ${reg(() => queuePoint(point, limit + 60), true)}>Показать ещё</button></p>` : ""));
}
function queueReport() {
  if (!meta.queueComplete) return `<div class="empty"><strong>Очередь не опубликована</strong>Список открытых сделок прочитан не полностью. Частичное число не показывается.</div>`;
  const open = queueAt(deals, builtAt);
  const byStage = new Map();
  for (const deal of open) { const stage = deal.stage || "Этап не прочитан"; const list = byStage.get(stage) ?? []; list.push(deal); byStage.set(stage, list); }
  const entries = [...byStage.entries()].sort((a, b) => b[1].length - a[1].length);
  const trail = archivedQueue();
  const max = Math.max(...trail.map((point) => point.ids.length), 1);
  const chart = trail.length ? panel("Очередь на конец дня", "Последние полные дни. Столбец открывает состав именно на тот вечер. Этап внутри берётся из истории, а не из сегодняшней карточки.", `<div class="chart">${trail.map((point) => `<button title="${dateFmt(point.at)} · ${point.ids.length}" aria-label="${dateFmt(point.at)}: ${point.ids.length} в работе" ${reg(() => queuePoint(point))}><i style="height:${Math.max(2, 100 * point.ids.length / max)}%"></i></button>`).join("")}</div><div class="axis"><span>${dateFmt(trail[0].at)} · ${fmt(trail[0].ids.length)}</span><span>${dateFmt(trail.at(-1).at)} · ${fmt(trail.at(-1).ids.length)}</span></div>`) : `<p class="note">История по дням в этот срез не вошла: список сделок, закрытых за эти дни, прочитан не полностью. Текущая очередь ниже от этого не собирается по частям.</p>`;
  return `<p class="hintline">Срез на ${stamp(builtAt)}<span class="aside">. Период на эту очередь не влияет.</span></p><div class="metrics">${metric("Открытые заявки", fmt(open.length), "включая неразобранное", () => list("Все открытые заявки", open.slice().sort((a, b) => a.created - b.created), "Сначала самые ранние."))}</div>${chart}` +
    panel("На каких этапах накопились заявки?", "Текущий этап взят с карточки amo.", `<div class="stack">${entries.map(([stage, group]) => barRow(stage, group.length, open.length, () => list(stage, group, "Открытые заявки на этом этапе."))).join("")}</div>`);
}
function kassaLine() {
  const cases = meta.kassaCases ?? { onlyKassa: [], closedOpen: [], amoMissing: [] };
  if (meta.kassa === "checked") return "Продажи кассы за эту неделю сверены и в число CRM не добавлялись.";
  if (meta.kassa === "mismatch") return `Касса расходится с amo: только в кассе ${fmt(cases.onlyKassa.length)}, без сделки amo ${fmt(cases.amoMissing.length)}, касса закрыта при открытой сделке ${fmt(cases.closedOpen.length)}.`;
  return "Касса за эту неделю не прочитана. Продажа, которая есть только в кассе, в число CRM не добавляется и в этом срезе не выдумана.";
}
function review() {
  const blockers = meta.blockers?.length ? `<ul class="reasonlist">${meta.blockers.map((item) => `<li>${esc(item)}</li>`).join("")}</ul>` : `<p class="note">Список расхождений этой недели пуст.</p>`;
  const fields = meta.fieldProblems?.length ? `<p class="note">${meta.fieldProblems.map((item) => esc(item)).join("<br>")}</p>` : "";
  return panel("Почему бейдж сверки ещё на экране", meta.reopen?.note ?? "", blockers + fields) +
    panel("Время ответа", "", meta.chat === "wazzup" ? `<p class="note aside">Сообщения Wazzup. Звонки не входят.</p>` : `<p class="note">${QUALITY_SOURCES.response.reason}</p>`) +
    panel("Касса", "", `<p class="note">${kassaLine()}</p>`) +
    `<p class="note">Неделя среза: ${esc(meta.week.label)}. Закрыто ${fmt(meta.week.closed)}, создано ${fmt(meta.week.created)}. Расхождений с фильтром amo: ${fmt(meta.week.discrepancies)}. Расхождений чекбокса «Целевой»: ${fmt(meta.week.checkbox)}. Сдвоенных карточек: ${fmt(meta.week.duplicates)}.</p>`;
}
function uncovered() { return `<div class="empty"><strong>Этот период в срезе не собран</strong>Срез покрывает ${esc(meta.week.label)}. Непокрытый период не показывается нулями.</div>`; }
function render() {
  actions.clear();
  document.body.classList.toggle("hub", page === "home");
  document.body.classList.toggle("explain", state.explain);
  const inCrm = ["quality", "general", "effect"].includes(page);
  const report = inCrm || page === "log";
  const crumbs = `<nav class="crumbs" aria-label="Путь раздела"><span>Касса</span>${ic("right")}${page === "home" ? "<b>Статистика</b>" : `<button data-page="home">Статистика</button>${ic("right")}${inCrm ? `<button data-page="crm">CRM</button>${ic("right")}` : ""}<b>${titles[page]}</b>`}</nav>`;
  const content = { home, crm, quality: qualityReport, general: generalReport, effect: () => funnelReport(false), log: () => funnelReport(true), queue: queueReport, review }[page];
  app.innerHTML = crumbs + `<header class="intro"><h1>${titles[page]}</h1><p class="aside">${intro[page]}</p></header>` +
    (inCrm ? `<nav class="subnav" aria-label="Отчёты CRM">${[["quality", "Качество"], ["general", "Общая статистика"], ["effect", "Эффективность"]].map(([key, label]) => `<button data-page="${key}" ${page === key ? 'aria-current="page"' : ""}>${label}</button>`).join("")}</nav>` : "") +
    (report ? toolbar() : "") + content() + `<footer class="footer"><span>${meta.accepted ? "Боевой срез" : "Сверка не закрыта"} · ${stamp(builtAt)}</span><div class="flex"><label class="switch"><input type="checkbox" id="explain" ${state.explain ? "checked" : ""}><span>Пояснения</span></label><button class="link" data-page="review">Границы среза</button><button class="link" ${reg(methodology)}>Как читать статистику</button></div></footer>`;
  document.title = titles[page] + " · MANSBAND";
}
render();
