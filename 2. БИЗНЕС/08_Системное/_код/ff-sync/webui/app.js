const $ = (id) => document.getElementById(id);
const state = { clients: [], lots: [], picked: new Set() };

async function api(path, opts) {
  const res = await fetch(path, Object.assign({ headers: { "Content-Type": "application/json" } }, opts || {}));
  let data = null;
  try { data = await res.json(); } catch (e) { data = {}; }
  if (!res.ok) throw new Error(data.detail || ("ошибка " + res.status));
  return data;
}

const num = (v, d) => (v === null || v === undefined || v === "" ? "—" : Number(v).toLocaleString("ru-RU", { maximumFractionDigits: d === undefined ? 2 : d }));
const esc = (s) => String(s === null || s === undefined ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function say(el, text, kind) {
  el.textContent = text || "";
  el.className = "msg" + (kind ? " " + kind : "");
}

// вход

async function boot() {
  try {
    const me = await api("/api/me");
    $("who").textContent = me.login;
    $("login").classList.add("off");
    $("app").classList.add("on");
    await loadClients();
    await loadIntake();
  } catch (e) {
    $("login").classList.remove("off");
    $("app").classList.remove("on");
  }
}

$("loginBtn").onclick = async () => {
  const err = $("loginErr");
  err.classList.remove("on");
  try {
    await api("/api/login", { method: "POST", body: JSON.stringify({ login: $("loginUser").value, password: $("loginPass").value }) });
    await boot();
  } catch (e) {
    err.textContent = e.message;
    err.classList.add("on");
  }
};
$("loginPass").addEventListener("keydown", (e) => { if (e.key === "Enter") $("loginBtn").click(); });
$("logoutBtn").onclick = async () => { await api("/api/logout", { method: "POST" }); location.reload(); };

// разделы

$("viewNav").onclick = (e) => {
  const btn = e.target.closest("button[data-view]");
  if (!btn) return;
  document.querySelectorAll("#viewNav button").forEach((b) => b.classList.toggle("active", b === btn));
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.id === "view-" + btn.dataset.view));
  if (btn.dataset.view === "stock") loadLots();
  if (btn.dataset.view === "bills") loadBills();
};

$("syncBtn").onclick = async () => {
  $("syncBtn").disabled = true;
  $("syncBtn").textContent = "Обновляю…";
  try {
    const res = await api("/api/agents/sync", { method: "POST" });
    say($("iMsg"), res.ok ? "Клиенты обновлены из МойСклад." : res.msg, res.ok ? "ok" : "bad");
    await loadClients();
  } catch (e) {
    say($("iMsg"), e.message, "bad");
  }
  $("syncBtn").disabled = false;
  $("syncBtn").textContent = "Обновить клиентов";
};

// приёмка

async function loadClients() {
  const res = await api("/api/clients");
  state.clients = res.clients;
  const opts = state.clients.map((c) => `<option value="${c.id}">${esc(c.name)}</option>`).join("");
  $("iClient").innerHTML = `<option value="">— выбери —</option>` + opts;
  $("sClient").innerHTML = `<option value="">Все контрагенты</option>` + opts;
}

async function loadIntake() {
  const res = await api("/api/intake");
  if (!$("iKind").options.length) {
    $("iKind").innerHTML = res.kinds.map((k) => `<option>${esc(k)}</option>`).join("");
  }
  const body = $("iTbl").querySelector("tbody");
  $("iCount").textContent = res.rows.length ? res.rows.length + " строк ждут отправки" : "";
  if (!res.rows.length) {
    body.innerHTML = `<tr><td colspan="11" class="empty">Пусто. Добавь строку сверху.</td></tr>`;
    $("iRun").disabled = true;
    return;
  }
  $("iRun").disabled = false;
  body.innerHTML = res.rows.map((r) => `<tr>
    <td>${esc(r.client)}</td><td>${esc(r.barcode)}</td><td>${esc(r.article)}</td>
    <td class="name">${esc(r.name)}</td><td>${esc(r.marketplace)}</td><td>${esc(r.gtin)}</td>
    <td>${esc(r.tracking)}</td><td class="num">${num(r.liters, 3)}</td><td class="num">${num(r.qty, 0)}</td>
    <td><span class="badge ${r.state === "warn" ? "warn" : "ok"}">${esc(r.state === "warn" ? r.note : "готово к отправке")}</span></td>
    <td><button class="link" data-drop="${r.id}" title="Удалить">✕</button></td>
  </tr>`).join("");
}

$("iTbl").onclick = async (e) => {
  const btn = e.target.closest("button[data-drop]");
  if (!btn) return;
  await api("/api/intake/" + btn.dataset.drop, { method: "DELETE" });
  loadIntake();
};

$("iAdd").onclick = async () => {
  const payload = {
    client_id: $("iClient").value,
    barcode: $("iBarcode").value,
    qty: $("iQty").value,
    liters: $("iLiters").value,
    kind: $("iKind").value,
    gtin: $("iGtin").value,
  };
  if (!payload.client_id) { say($("iMsg"), "Выбери контрагента.", "bad"); return; }
  try {
    await api("/api/intake", { method: "POST", body: JSON.stringify(payload) });
    say($("iMsg"), "");
    $("iBarcode").value = "";
    $("iGtin").value = "";
    $("iQty").value = "1";
    $("iBarcode").focus();
    loadIntake();
  } catch (e) {
    say($("iMsg"), e.message, "bad");
  }
};
$("iBarcode").addEventListener("keydown", (e) => { if (e.key === "Enter") $("iAdd").click(); });

$("iRun").onclick = async () => {
  $("iRun").disabled = true;
  say($("iRunMsg"), "Отправляю в МойСклад…");
  try {
    const res = await api("/api/intake/run", { method: "POST" });
    if (!res.ok) { say($("iRunMsg"), res.msg, "bad"); }
    else {
      const orders = (res.orders || []).map((o) => `${o.client}: заказ поставщика ${o.number}`).join("\n");
      const bad = (res.skipped || []).map((s) => `${s.barcode}: ${s.note}`).join("\n");
      say($("iRunMsg"), [`Создано позиций: ${res.done.length}.`, orders, bad && "Не прошли:\n" + bad].filter(Boolean).join("\n"), bad ? "" : "ok");
    }
    loadIntake();
  } catch (e) {
    say($("iRunMsg"), e.message, "bad");
  }
  $("iRun").disabled = false;
};

// учёт

function stockQuery() {
  const params = new URLSearchParams();
  if ($("sClient").value) params.set("client_id", $("sClient").value);
  if ($("sQuery").value.trim()) params.set("q", $("sQuery").value.trim());
  return params.toString();
}

async function loadLots() {
  const res = await api("/api/lots?" + stockQuery());
  state.lots = res.rows;
  state.picked.clear();
  $("kPos").textContent = num(res.totals.positions, 0);
  $("kQty").textContent = num(res.totals.qty, 0);
  $("kVol").innerHTML = num(res.totals.volume, 1) + ' <small>л</small>';
  $("kMoney").innerHTML = num(res.totals.money, 2) + ' <small>₽</small>';
  $("kAged").textContent = res.totals.aged ? res.totals.aged + " позиций лежат больше 90 дней" : "";
  const body = $("sTbl").querySelector("tbody");
  if (!res.rows.length) {
    body.innerHTML = `<tr><td colspan="16" class="empty">Ничего не нашлось.</td></tr>`;
  } else {
    body.innerHTML = res.rows.map((r) => `<tr>
      <td class="pick"><input type="checkbox" data-lot="${r.id}"></td>
      <td>${esc(r.client)}</td><td>${esc(r.article)}</td><td>${esc(r.barcode)}</td><td>${esc(r.gtin)}</td>
      <td class="name">${esc(r.name)}</td><td>${esc(r.tracking)}</td>
      <td class="num">${num(r.liters, 3)}</td><td class="num">${num(r.qty, 0)}</td><td class="num">${num(r.volume, 2)}</td>
      <td>${esc(r.received)}</td><td class="num ${r.days > 90 ? "aged" : ""}">${r.days}</td>
      <td class="num">${num(r.storage)}</td><td class="num">${num(r.intake)}</td><td class="num">${num(r.ship)}</td>
      <td class="num money">${num(r.total)}</td>
    </tr>`).join("");
  }
  $("sAll").checked = false;
  refreshPick();
}

function refreshPick() {
  const n = state.picked.size;
  const sum = state.lots.filter((r) => state.picked.has(r.id)).reduce((a, r) => a + r.total, 0);
  $("sSel").textContent = n ? `Выбрано ${n} позиций на ${num(sum)} ₽` : "Ничего не выбрано";
  $("sBill").disabled = n === 0;
}

$("sTbl").onclick = (e) => {
  const box = e.target.closest("input[data-lot]");
  if (!box) return;
  const id = Number(box.dataset.lot);
  if (box.checked) state.picked.add(id); else state.picked.delete(id);
  refreshPick();
};

$("sAll").onclick = () => {
  const on = $("sAll").checked;
  state.picked.clear();
  $("sTbl").querySelectorAll("input[data-lot]").forEach((b) => {
    b.checked = on;
    if (on) state.picked.add(Number(b.dataset.lot));
  });
  refreshPick();
};

$("sClient").onchange = loadLots;
let timer = null;
$("sQuery").oninput = () => { clearTimeout(timer); timer = setTimeout(loadLots, 250); };
$("sExport").onclick = () => { window.location = "/api/export.xlsx?" + stockQuery(); };

// счёт

$("sBill").onclick = async () => {
  try {
    const res = await api("/api/invoice/preview", { method: "POST", body: JSON.stringify({ lot_ids: [...state.picked] }) });
    $("mTitle").textContent = "Счёт: " + res.client;
    $("mLines").innerHTML = res.lines.map((l) => `<div class="inv-line"><span>${esc(l.name)}</span><b>${num(l.sum)} ₽</b></div>`).join("");
    $("mTotal").textContent = num(res.total) + " ₽";
    say($("mMsg"), res.total > 0 ? "" : "По выбранным позициям всё уже выставлено.", res.total > 0 ? "" : "bad");
    $("mOk").disabled = res.total <= 0;
    $("modal").classList.add("on");
  } catch (e) {
    say($("mMsg"), e.message, "bad");
    $("mLines").innerHTML = "";
    $("mTotal").textContent = "—";
    $("mOk").disabled = true;
    $("modal").classList.add("on");
  }
};

$("mCancel").onclick = () => $("modal").classList.remove("on");
$("mOk").onclick = async () => {
  $("mOk").disabled = true;
  try {
    const res = await api("/api/invoice", { method: "POST", body: JSON.stringify({ lot_ids: [...state.picked] }) });
    $("modal").classList.remove("on");
    say($("iRunMsg"), "");
    await loadLots();
    await loadBills();
    document.querySelector('#viewNav button[data-view="bills"]').click();
    alert("Счёт " + (res.number || "") + " на " + num(res.total) + " ₽ создан в МойСклад");
  } catch (e) {
    say($("mMsg"), e.message, "bad");
  }
  $("mOk").disabled = false;
};

async function loadBills() {
  const res = await api("/api/invoices");
  const body = $("bTbl").querySelector("tbody");
  if (!res.rows.length) {
    body.innerHTML = `<tr><td colspan="9" class="empty">Счетов пока нет.</td></tr>`;
    return;
  }
  body.innerHTML = res.rows.map((r) => `<tr>
    <td>${esc(r.number)}</td><td>${esc(r.client)}</td><td>${esc(r.created)}</td><td class="num">${r.positions}</td>
    <td class="num">${num(r.storage)}</td><td class="num">${num(r.intake)}</td><td class="num">${num(r.ship)}</td>
    <td class="num money">${num(r.total)}</td><td>${esc(r.author)}</td>
  </tr>`).join("");
}

boot();
