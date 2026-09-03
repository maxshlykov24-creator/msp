const $ = (id) => document.getElementById(id);
const state = { clients: [], lots: [], ships: [], picked: new Set(), pickedShip: new Set() };

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
    await loadOverview();
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
  if (btn.dataset.view === "home") loadOverview();
  if (btn.dataset.view === "stock") loadLots();
  if (btn.dataset.view === "ships") loadShips();
};

function goView(name) {
  if (name === "ships") {
    if ($("dFrom").value) $("shFrom").value = $("dFrom").value;
    if ($("dTo").value) $("shTo").value = $("dTo").value;
    $("shPeriod").querySelectorAll("button").forEach((b) => b.classList.remove("active"));
    if ($("dClient").value) {
      const c = state.clients.find((x) => String(x.id) === $("dClient").value);
      $("shClient").value = $("dClient").value;
      $("shClientQ").value = c ? c.name : $("dClientQ").value;
    }
  }
  if (name === "stock" && $("dClient").value) {
    const c = state.clients.find((x) => String(x.id) === $("dClient").value);
    $("sClient").value = $("dClient").value;
    $("sClientQ").value = c ? c.name : $("dClientQ").value;
  }
  if (name === "bills") {
    const stock = document.querySelector('#viewNav button[data-view="stock"]');
    if (stock) stock.click();
    openBills();
    return;
  }
  const btn = document.querySelector('#viewNav button[data-view="' + name + '"]');
  if (btn) btn.click();
}

function setDashDays(days) {
  const to = new Date();
  const from = new Date();
  if (Number(days) > 0) from.setDate(from.getDate() - Number(days) + 1);
  $("dFrom").value = isoDay(from);
  $("dTo").value = isoDay(to);
}

function fillDashCombo(query) {
  const list = $("dClientList");
  if (!list) return;
  const items = clientMatches(query);
  list.innerHTML = items.map((c) => {
    const on = String(c.id) === String($("dClient").value);
    return `<button type="button" class="combo-item${c.id === "" ? " muted" : ""}${on ? " is-on" : ""}" data-id="${c.id}">${esc(c.name)}</button>`;
  }).join("") || `<div class="combo-item muted">Никого не нашлось</div>`;
}

function pickDashClient(id, name) {
  $("dClient").value = id || "";
  $("dClientQ").value = id ? name : "";
  $("dClientList").hidden = true;
  loadOverview();
}

function dashShare(el, rows, key) {
  const total = (rows || []).reduce((a, r) => a + (r.n || 0), 0) || 1;
  const labels = { wb: "WB", ozon: "Ozon", fbs: "FBS", fbo: "FBO" };
  const order = key === "marketplace" ? ["wb", "ozon"] : ["fbs", "fbo"];
  const by = Object.fromEntries((rows || []).map((r) => [r[key], r]));
  el.innerHTML = order.map((k) => {
    const n = (by[k] && by[k].n) || 0;
    return `<div class="share-row"><span>${labels[k]}</span><div class="share-bar"><i style="width:${Math.round(n * 100 / total)}%"></i></div><b>${num(n, 0)}</b></div>`;
  }).join("");
}

function fillDayRange(from, to, rows) {
  const map = {};
  (rows || []).forEach((r) => { map[r.day] = r; });
  const out = [];
  const cur = new Date(from + "T00:00:00");
  const end = new Date(to + "T00:00:00");
  while (cur <= end) {
    const k = isoDay(cur);
    out.push({ day: k, n: (map[k] && map[k].n) || 0, marks: (map[k] && map[k].marks) || 0 });
    cur.setDate(cur.getDate() + 1);
  }
  return out;
}

function renderDashChart(rows) {
  const svg = $("dChart");
  const tip = $("dChartTip");
  const box = $("dChartBox");
  if (!svg) return;
  const W = 1000, H = 220, L = 8, R = 8, T = 14, B = 26;
  const innerW = W - L - R, innerH = H - T - B;
  const max = Math.max(1, ...rows.map((r) => Math.max(r.n || 0, r.marks || 0)));
  const bw = innerW / Math.max(rows.length, 1);
  const yOf = (v) => T + innerH - (v / max) * innerH;
  const grid = [0.25, 0.5, 0.75, 1].map((p) => {
    const yy = T + innerH * (1 - p);
    return `<line class="grid-line" x1="${L}" y1="${yy}" x2="${W - R}" y2="${yy}"/>`;
  }).join("");
  const bars = rows.map((r, i) => {
    const x = L + i * bw + bw * 0.18;
    const h = Math.max((r.n / max) * innerH, 0);
    return `<rect class="bar" data-i="${i}" x="${x}" y="${yOf(r.n)}" width="${bw * 0.64}" height="${h}" rx="3"/>`;
  }).join("");
  const pts = rows.map((r, i) => (L + i * bw + bw / 2) + "," + yOf(r.marks)).join(" ");
  const dots = rows.map((r, i) => `<circle class="dot" cx="${L + i * bw + bw / 2}" cy="${yOf(r.marks)}" r="2.6"/>`).join("");
  const labels = rows.map((r, i) => {
    if (rows.length > 14 && i % 2) return "";
    return `<text class="axis-lbl" x="${L + i * bw + bw / 2}" y="${H - 6}" text-anchor="middle">${r.day.slice(8, 10)}.${r.day.slice(5, 7)}</text>`;
  }).join("");
  svg.innerHTML = grid + bars + `<polyline class="line" points="${pts}"/>` + dots + labels;
  svg.onmousemove = (e) => {
    const hit = e.target.closest("[data-i]");
    svg.querySelectorAll(".bar").forEach((b) => b.classList.toggle("is-hot", hit && b === hit));
    if (!hit) { tip.classList.remove("is-on"); return; }
    const r = rows[Number(hit.dataset.i)];
    const br = box.getBoundingClientRect();
    tip.innerHTML = `<b>${r.day.slice(8, 10)}.${r.day.slice(5, 7)}</b><i>отправлений ${num(r.n, 0)}</i><i>кодов ${num(r.marks, 0)}</i>`;
    tip.style.left = (e.clientX - br.left) + "px";
    tip.style.top = (e.clientY - br.top) + "px";
    tip.classList.add("is-on");
  };
  svg.onmouseleave = () => {
    tip.classList.remove("is-on");
    svg.querySelectorAll(".bar").forEach((b) => b.classList.remove("is-hot"));
  };
}

function fmtSync(raw) {
  if (!raw) return "кабинеты ещё не синхронизированы";
  const d = new Date(raw);
  if (isNaN(d.getTime())) return String(raw);
  const pad = (n) => String(n).padStart(2, "0");
  return "кабинеты: " + pad(d.getDate()) + "." + pad(d.getMonth() + 1) + " " + pad(d.getHours()) + ":" + pad(d.getMinutes());
}

async function loadOverview() {
  if (!$("dFrom") || !$("dFrom").value) setDashDays(7);
  const params = new URLSearchParams();
  if ($("dClient").value) params.set("client_id", $("dClient").value);
  if ($("dFrom").value) params.set("date_from", $("dFrom").value);
  if ($("dTo").value) params.set("date_to", $("dTo").value);
  const res = await api("/api/overview?" + params.toString());
  $("dQty").textContent = num(res.stock.qty, 0);
  $("dQtySub").textContent = num(res.stock.positions, 0) + " позиций · сейчас";
  $("dVol").innerHTML = num(res.stock.volume, 1) + " <small>л</small>";
  $("dVolSub").textContent = res.stock.aged ? res.stock.aged + " позиций старше 90 дней" : "занято на складе";
  $("dMoney").innerHTML = num(res.stock.money, 0) + " <small>₽</small>";
  $("dShip").textContent = num(res.ships.n, 0);
  $("dShipSub").textContent = num(res.ships.qty, 0) + " шт · FBS и FBO";
  $("dMarks").textContent = num(res.ships.marks, 0);
  const cover = res.ships.n ? Math.round(100 * (res.ships.n - res.ships.without) / res.ships.n) : 0;
  $("dMarksSub").textContent = cover + "% отправлений с КИЗ";
  $("dIntake").textContent = num(res.intake, 0);
  $("dEmpty").textContent = num(res.ships.without, 0);
  $("dBills").textContent = num(res.invoices.n, 0);
  $("dBillsSub").textContent = res.invoices.n ? num(res.invoices.total, 0) + " ₽ в МойСклад" : "за период нет";
  $("dClients").textContent = num(res.clients, 0);
  $("dUpdated").textContent = fmtSync(res.last_ok);
  dashShare($("dShareMp"), res.by_mp, "marketplace");
  dashShare($("dShareKind"), res.by_kind, "kind");
  renderDashChart(fillDayRange($("dFrom").value, $("dTo").value, res.days));
}

$("dClientQ").onfocus = () => { fillDashCombo($("dClientQ").value); $("dClientList").hidden = false; };
$("dClientQ").oninput = () => {
  fillDashCombo($("dClientQ").value);
  $("dClientList").hidden = false;
  if (!$("dClientQ").value.trim() && $("dClient").value) {
    $("dClient").value = "";
    loadOverview();
  }
};
$("dClientQ").onkeydown = (e) => {
  if (e.key === "Escape") $("dClientList").hidden = true;
  if (e.key === "Enter") {
    e.preventDefault();
    const first = $("dClientList").querySelector(".combo-item[data-id]");
    if (first) pickDashClient(first.dataset.id, first.textContent);
  }
};
$("dClientList").onclick = (e) => {
  const btn = e.target.closest(".combo-item[data-id]");
  if (btn) pickDashClient(btn.dataset.id, btn.textContent);
};
$("dPeriod").onclick = (e) => {
  const btn = e.target.closest("button[data-days]");
  if (!btn) return;
  $("dPeriod").querySelectorAll("button").forEach((b) => b.classList.toggle("active", b === btn));
  setDashDays(btn.dataset.days);
  loadOverview();
};
$("dFrom").onchange = $("dTo").onchange = () => {
  $("dPeriod").querySelectorAll("button").forEach((b) => b.classList.remove("active"));
  loadOverview();
};
$("view-home").onclick = (e) => {
  const card = e.target.closest("[data-go]");
  if (card) goView(card.dataset.go);
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
  fillIntakeCombo();
  fillClientCombo();
  fillShipCombo();
  fillDashCombo();
  if (state.clients.length === 1 && !$("iClient").value) {
    setIntakeClient(state.clients[0].id, state.clients[0].name);
  }
}

function intakeMatches(query) {
  const q = (query || "").trim().toLowerCase();
  if (!q) return state.clients;
  return state.clients.filter((c) => c.name.toLowerCase().includes(q) || String(c.id) === q);
}

function fillIntakeCombo(query) {
  const list = $("iClientList");
  if (!list) return;
  const items = intakeMatches(query);
  list.innerHTML = items.map((c) => {
    const on = String(c.id) === String($("iClient").value);
    return `<button type="button" class="combo-item${on ? " is-on" : ""}" data-id="${c.id}">${esc(c.name)}</button>`;
  }).join("") || `<div class="combo-item muted">Никого не нашлось</div>`;
}

function setIntakeClient(id, name) {
  $("iClient").value = id || "";
  $("iClientQ").value = id ? name : "";
  $("iClientList").hidden = true;
}

function resolveIntakeClient() {
  if ($("iClient").value) return $("iClient").value;
  const hits = intakeMatches($("iClientQ").value);
  if (hits.length === 1) {
    setIntakeClient(hits[0].id, hits[0].name);
    return String(hits[0].id);
  }
  return "";
}

function kindOptions(selected) {
  const kinds = state.kinds || [];
  return [`<option value="">—</option>`].concat(
    kinds.map((k) => `<option${k === selected ? " selected" : ""}>${esc(k)}</option>`)
  ).join("");
}

async function loadIntake() {
  const res = await api("/api/intake");
  state.kinds = res.kinds;
  const body = $("iTbl").querySelector("tbody");
  const miss = res.rows.filter((r) => (r.note || "").startsWith("нет в кабинетах")).length;
  $("iCount").textContent = res.rows.length
    ? res.rows.length + " строк · в кабинетах " + (res.rows.length - miss) + (miss ? " · не нашлось " + miss : "")
    : "";
  if (!res.rows.length) {
    body.innerHTML = `<tr><td colspan="10" class="empty">Пусто. Вставь штрихкоды сверху.</td></tr>`;
    $("iRun").disabled = true;
    return;
  }
  $("iRun").disabled = false;
  body.innerHTML = res.rows.map((r) => `<tr>
    <td>${esc(r.client)}</td><td>${esc(r.barcode)}</td><td>${esc(r.article)}</td>
    <td class="name">${esc(r.name)}</td><td>${r.marketplace ? `<span class="badge mp">${esc(r.marketplace)}</span>` : ""}</td>
    <td><input class="cell" data-id="${r.id}" data-field="gtin" value="${esc(r.gtin)}"></td>
    <td><select class="cell" data-id="${r.id}" data-field="kind">${kindOptions(r.tracking)}</select></td>
    <td><input class="cell" data-id="${r.id}" data-field="liters" value="${r.liters == null ? "" : r.liters}"></td>
    <td><span class="badge ${r.state === "warn" ? "warn" : "ok"}"><i></i>${esc(r.state === "warn" ? r.note : "готово")}</span></td>
    <td><button class="link" data-drop="${r.id}" title="Удалить">✕</button></td>
  </tr>`).join("");
}

$("iClientQ").onfocus = () => { fillIntakeCombo($("iClientQ").value); $("iClientList").hidden = false; };
$("iClientQ").oninput = () => {
  fillIntakeCombo($("iClientQ").value);
  $("iClientList").hidden = false;
  if (!$("iClientQ").value.trim()) $("iClient").value = "";
};
$("iClientQ").onkeydown = (e) => {
  if (e.key === "Escape") $("iClientList").hidden = true;
  if (e.key === "Enter") {
    e.preventDefault();
    const first = $("iClientList").querySelector(".combo-item[data-id]");
    if (first) setIntakeClient(first.dataset.id, first.textContent);
  }
};
$("iClientList").onclick = (e) => {
  const btn = e.target.closest(".combo-item[data-id]");
  if (btn) setIntakeClient(btn.dataset.id, btn.textContent);
};

$("iTbl").onclick = async (e) => {
  const btn = e.target.closest("button[data-drop]");
  if (!btn) return;
  await api("/api/intake/" + btn.dataset.drop, { method: "DELETE" });
  loadIntake();
};

$("iTbl").onchange = async (e) => {
  const el = e.target.closest("[data-id][data-field]");
  if (!el) return;
  const body = {};
  body[el.dataset.field === "kind" ? "kind" : el.dataset.field] = el.value;
  try {
    await api("/api/intake/" + el.dataset.id, { method: "PATCH", body: JSON.stringify(body) });
    loadIntake();
  } catch (err) {
    say($("iMsg"), err.message, "bad");
  }
};

function intakeSummary(res) {
  const bits = [
    `Сверил ${res.added.length} штрихкодов.`,
    res.found ? `Нашлось в кабинетах: ${res.found}.` : "",
    res.missing ? `Нет в кабинетах: ${res.missing}.` : "",
    res.skipped.length ? `Уже были в очереди: ${res.skipped.length}.` : "",
  ];
  return bits.filter(Boolean).join(" ");
}

$("iAdd").onclick = async () => {
  const clientId = resolveIntakeClient();
  if (!clientId) { say($("iMsg"), "Выбери контрагента.", "bad"); return; }
  $("iAdd").disabled = true;
  say($("iMsg"), "Сверяю с кабинетами WB и Ozon…");
  try {
    const res = await api("/api/intake", {
      method: "POST",
      body: JSON.stringify({
        client_id: clientId,
        text: $("iBarcodes").value,
      }),
    });
    say($("iMsg"), intakeSummary(res), res.missing ? "" : "ok");
    $("iBarcodes").value = "";
    loadIntake();
  } catch (e) {
    say($("iMsg"), e.message, "bad");
  }
  $("iAdd").disabled = false;
};

$("iFile").onchange = async () => {
  const file = $("iFile").files[0];
  $("iFile").value = "";
  if (!file) return;
  const clientId = resolveIntakeClient();
  if (!clientId) { say($("iMsg"), "Выбери контрагента.", "bad"); return; }
  say($("iMsg"), "Читаю файл и сверяю с кабинетами…");
  try {
    const buf = await file.arrayBuffer();
    const bytes = new Uint8Array(buf);
    let bin = "";
    for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
    const res = await api("/api/intake/file", {
      method: "POST",
      body: JSON.stringify({
        client_id: clientId,
        filename: file.name,
        content: btoa(bin),
      }),
    });
    say($("iMsg"), intakeSummary(res), res.missing ? "" : "ok");
    loadIntake();
  } catch (e) {
    say($("iMsg"), e.message, "bad");
  }
};

$("iRun").onclick = async () => {
  $("iRun").disabled = true;
  say($("iRunMsg"), "Создаю товары в МойСклад…");
  try {
    const res = await api("/api/intake/run", { method: "POST" });
    if (!res.ok) { say($("iRunMsg"), res.msg, "bad"); }
    else {
      const orders = (res.orders || []).map((o) => `${o.client}: заказ поставщика ${o.number}`).join("\n");
      const bad = (res.skipped || []).map((s) => `${s.barcode}: ${s.note}`).join("\n");
      say($("iRunMsg"), [`Создано товаров: ${res.done.length}.`, orders, bad && "Не прошли:\n" + bad].filter(Boolean).join("\n"), bad ? "" : "ok");
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
  const mark = document.querySelector("#sMark button.active");
  if (mark && mark.dataset.mark) params.set("marked", mark.dataset.mark);
  return params.toString();
}

function clientMatches(query) {
  const q = (query || "").trim().toLowerCase();
  const rows = [{ id: "", name: "Все контрагенты" }].concat(state.clients);
  if (!q) return rows;
  return rows.filter((c) => c.name.toLowerCase().includes(q) || String(c.id) === q);
}

function fillClientCombo(query) {
  const list = $("sClientList");
  if (!list) return;
  const items = clientMatches(query);
  list.innerHTML = items.map((c) => {
    const on = String(c.id) === String($("sClient").value);
    return `<button type="button" class="combo-item${c.id === "" ? " muted" : ""}${on ? " is-on" : ""}" data-id="${c.id}">${esc(c.name)}</button>`;
  }).join("") || `<div class="combo-item muted">Никого не нашлось</div>`;
}

function pickClient(id, name) {
  $("sClient").value = id || "";
  $("sClientQ").value = id ? name : "";
  $("sClientList").hidden = true;
  loadLots();
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
    body.innerHTML = `<tr><td colspan="14" class="empty">Ничего не нашлось.</td></tr>`;
  } else {
    body.innerHTML = res.rows.map((r) => `<tr>
      <td class="pick"><input type="checkbox" data-lot="${r.id}"></td>
      <td class="client">${esc(r.client)}</td>
      <td class="codes"><b>${esc(r.article)}</b><span>ШК ${esc(r.barcode)}</span><span>GTIN ${esc(r.gtin)}</span></td>
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
  $("sReport").disabled = n === 0;
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

$("sClientQ").onfocus = () => { fillClientCombo($("sClientQ").value); $("sClientList").hidden = false; };
$("sClientQ").oninput = () => {
  fillClientCombo($("sClientQ").value);
  $("sClientList").hidden = false;
  if (!$("sClientQ").value.trim() && $("sClient").value) {
    $("sClient").value = "";
    loadLots();
  }
};
$("sClientQ").onkeydown = (e) => {
  if (e.key === "Escape") $("sClientList").hidden = true;
  if (e.key === "Enter") {
    e.preventDefault();
    const first = $("sClientList").querySelector(".combo-item[data-id]");
    if (first) pickClient(first.dataset.id, first.textContent);
  }
};
$("sClientList").onclick = (e) => {
  const btn = e.target.closest(".combo-item[data-id]");
  if (!btn) return;
  pickClient(btn.dataset.id, btn.textContent);
};
document.addEventListener("click", (e) => {
  if ($("iClientBox") && !$("iClientBox").contains(e.target)) $("iClientList").hidden = true;
  if (!$("sClientBox").contains(e.target)) $("sClientList").hidden = true;
  if ($("shClientBox") && !$("shClientBox").contains(e.target)) $("shClientList").hidden = true;
  if ($("dClientBox") && !$("dClientBox").contains(e.target)) $("dClientList").hidden = true;
});
$("sMark").onclick = (e) => {
  const btn = e.target.closest("button[data-mark]");
  if (!btn) return;
  $("sMark").querySelectorAll("button").forEach((b) => b.classList.toggle("active", b === btn));
  loadLots();
};
let timer = null;
$("sQuery").oninput = () => { clearTimeout(timer); timer = setTimeout(loadLots, 250); };
$("sExport").onclick = () => { window.location = "/api/export.xlsx?" + stockQuery(); };
$("sBills").onclick = () => openBills();
$("sReport").onclick = async () => {
  $("sReport").disabled = true;
  try {
    await downloadXlsx("/api/lots/report.xlsx", [...state.picked], "остаток.xlsx", "Отчёт для клиента скачан.", $("sMsg"));
  } catch (e) {
    say($("sMsg"), e.message, "bad");
  }
  refreshPick();
};

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
    say($("sMsg"), "Счёт " + (res.number || "") + " на " + num(res.total) + " ₽ создан в МойСклад.", "ok");
    openBills();
  } catch (e) {
    say($("mMsg"), e.message, "bad");
  }
  $("mOk").disabled = false;
};

function isoDay(d) {
  const x = d || new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return x.getFullYear() + "-" + pad(x.getMonth() + 1) + "-" + pad(x.getDate());
}

function setShipDays(days) {
  const to = new Date();
  const from = new Date();
  if (Number(days) > 0) from.setDate(from.getDate() - Number(days) + 1);
  $("shFrom").value = isoDay(from);
  $("shTo").value = isoDay(to);
}

function fillShipCombo(query) {
  const list = $("shClientList");
  if (!list) return;
  const items = clientMatches(query);
  list.innerHTML = items.map((c) => {
    const on = String(c.id) === String($("shClient").value);
    return `<button type="button" class="combo-item${c.id === "" ? " muted" : ""}${on ? " is-on" : ""}" data-id="${c.id}">${esc(c.name)}</button>`;
  }).join("") || `<div class="combo-item muted">Никого не нашлось</div>`;
}

function pickShipClient(id, name) {
  $("shClient").value = id || "";
  $("shClientQ").value = id ? name : "";
  $("shClientList").hidden = true;
  loadShips();
}

function shipQuery() {
  if (!$("shFrom").value) setShipDays(0);
  const params = new URLSearchParams();
  if ($("shClient").value) params.set("client_id", $("shClient").value);
  if ($("shQuery").value.trim()) params.set("q", $("shQuery").value.trim());
  const mp = document.querySelector("#shMp button.active");
  const kind = document.querySelector("#shKind button.active");
  const mark = document.querySelector("#shMark button.active");
  if (mp && mp.dataset.mp) params.set("mp", mp.dataset.mp);
  if (kind && kind.dataset.kind) params.set("kind", kind.dataset.kind);
  if (mark && mark.dataset.mark) params.set("marked", mark.dataset.mark);
  if ($("shFrom").value) params.set("date_from", $("shFrom").value);
  if ($("shTo").value) params.set("date_to", $("shTo").value);
  return params.toString();
}

function refreshShipPick() {
  const n = state.pickedShip.size;
  const marks = state.ships.filter((r) => state.pickedShip.has(r.id)).reduce((a, r) => a + (r.marks || 0), 0);
  $("shSel").textContent = n ? `Выбрано ${n} отправлений, кодов ${num(marks, 0)}` : "Ничего не выбрано";
  $("shReport").disabled = n === 0;
  $("shExport").disabled = marks === 0;
}

async function loadShips() {
  const res = await api("/api/shipments?" + shipQuery());
  state.ships = res.rows;
  state.pickedShip.clear();
  $("kShip").textContent = num(res.totals.positions, 0);
  $("kShipQty").textContent = num(res.totals.qty, 0);
  $("kShipMarks").innerHTML = num(res.totals.marks, 0);
  $("kShipEmpty").textContent = num(res.totals.without, 0);
  const body = $("shTbl").querySelector("tbody");
  if (!res.rows.length) {
    body.innerHTML = `<tr><td colspan="11" class="empty">Нет отправлений за период. Нажми «Обновить отгрузки».</td></tr>`;
  } else {
    body.innerHTML = res.rows.map((r) => `<tr>
      <td class="pick"><input type="checkbox" data-ship="${r.id}"></td>
      <td class="client">${esc(r.client)}</td>
      <td><span class="badge mp">${esc(r.marketplace)}</span></td>
      <td>${esc((r.kind || "").toUpperCase())}</td>
      <td class="code">${esc(r.ext_id)}</td>
      <td>${esc(r.shipped)}</td>
      <td>${esc(r.status)}</td>
      <td class="code">${esc(r.article)}</td>
      <td class="name">${esc(r.name)}</td>
      <td class="num">${num(r.qty, 0)}</td>
      <td class="num">${r.marks ? `<span class="badge ok"><i></i>${r.marks}</span>` : `<span class="badge warn"><i></i>0</span>`}</td>
    </tr>`).join("");
  }
  $("shAll").checked = false;
  refreshShipPick();
}

$("shClientQ").onfocus = () => { fillShipCombo($("shClientQ").value); $("shClientList").hidden = false; };
$("shClientQ").oninput = () => {
  fillShipCombo($("shClientQ").value);
  $("shClientList").hidden = false;
  if (!$("shClientQ").value.trim() && $("shClient").value) {
    $("shClient").value = "";
    loadShips();
  }
};
$("shClientQ").onkeydown = (e) => {
  if (e.key === "Escape") $("shClientList").hidden = true;
  if (e.key === "Enter") {
    e.preventDefault();
    const first = $("shClientList").querySelector(".combo-item[data-id]");
    if (first) pickShipClient(first.dataset.id, first.textContent);
  }
};
$("shClientList").onclick = (e) => {
  const btn = e.target.closest(".combo-item[data-id]");
  if (!btn) return;
  pickShipClient(btn.dataset.id, btn.textContent);
};
["shMp", "shKind", "shMark"].forEach((id) => {
  $(id).onclick = (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    $(id).querySelectorAll("button").forEach((b) => b.classList.toggle("active", b === btn));
    loadShips();
  };
});
$("shPeriod").onclick = (e) => {
  const btn = e.target.closest("button[data-days]");
  if (!btn) return;
  $("shPeriod").querySelectorAll("button").forEach((b) => b.classList.toggle("active", b === btn));
  setShipDays(btn.dataset.days);
  loadShips();
};
$("shFrom").onchange = $("shTo").onchange = () => {
  $("shPeriod").querySelectorAll("button").forEach((b) => b.classList.remove("active"));
  loadShips();
};
let shipTimer = null;
$("shQuery").oninput = () => { clearTimeout(shipTimer); shipTimer = setTimeout(loadShips, 250); };
$("shTbl").onclick = (e) => {
  const box = e.target.closest("input[data-ship]");
  if (!box) return;
  const id = Number(box.dataset.ship);
  if (box.checked) state.pickedShip.add(id); else state.pickedShip.delete(id);
  refreshShipPick();
};
$("shAll").onclick = () => {
  const on = $("shAll").checked;
  state.pickedShip.clear();
  $("shTbl").querySelectorAll("input[data-ship]").forEach((b) => {
    b.checked = on;
    if (on) state.pickedShip.add(Number(b.dataset.ship));
  });
  refreshShipPick();
};
$("shSync").onclick = async () => {
  $("shSync").disabled = true;
  $("shSync").textContent = "Забираю из кабинетов…";
  say($("shMsg"), "Тяну FBS и FBO за выбранный период.");
  try {
    const from = $("shFrom").value ? new Date($("shFrom").value) : new Date();
    const days = Math.max(1, Math.round((Date.now() - from.getTime()) / 86400000) + 1);
    const res = await api("/api/shipments/sync", { method: "POST", body: JSON.stringify({ days }) });
    say($("shMsg"), res.ok ? (res.notes || []).join("\n") || ("Обновлено отправлений: " + res.count) : res.msg, res.ok ? "ok" : "bad");
    await loadShips();
  } catch (e) {
    say($("shMsg"), e.message, "bad");
  }
  $("shSync").disabled = false;
  $("shSync").textContent = "Обновить отгрузки";
};
async function downloadXlsx(url, ids, fallback, okText, msgEl) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ids }),
  });
  if (!res.ok) {
    let data = {};
    try { data = await res.json(); } catch (e) {}
    throw new Error(data.detail || ("ошибка " + res.status));
  }
  const blob = await res.blob();
  const a = document.createElement("a");
  const cd = res.headers.get("Content-Disposition") || "";
  const m = /filename\*=UTF-8''([^;]+)/.exec(cd);
  a.download = m ? decodeURIComponent(m[1]) : fallback;
  a.href = URL.createObjectURL(blob);
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(a.href);
  if (msgEl) say(msgEl, okText, "ok");
}

$("shExport").onclick = async () => {
  $("shExport").disabled = true;
  try {
    await downloadXlsx("/api/shipments/marks.xlsx", [...state.pickedShip], "коды_маркировки.xlsx", "Файл с кодами скачан.", $("shMsg"));
  } catch (e) {
    say($("shMsg"), e.message, "bad");
  }
  refreshShipPick();
};
$("shReport").onclick = async () => {
  $("shReport").disabled = true;
  try {
    await downloadXlsx("/api/shipments/report.xlsx", [...state.pickedShip], "отгрузки.xlsx", "Отчёт по отгрузкам скачан.", $("shMsg"));
  } catch (e) {
    say($("shMsg"), e.message, "bad");
  }
  refreshShipPick();
};

function openBills() {
  $("billsModal").classList.add("on");
  loadBills();
}
function closeBills() {
  $("billsModal").classList.remove("on");
}
$("billsClose").onclick = closeBills;
$("billsModal").onclick = (e) => { if (e.target === $("billsModal")) closeBills(); };

async function loadBills() {
  const res = await api("/api/invoices");
  const body = $("bTbl").querySelector("tbody");
  if (!res.rows.length) {
    body.innerHTML = `<tr><td colspan="9" class="empty">Счетов пока нет.</td></tr>`;
    return;
  }
  body.innerHTML = res.rows.map((r) => `<tr class="clickable" data-bill="${r.id}">
    <td>${esc(r.number)}</td><td>${esc(r.client)}</td><td>${esc(r.created)}</td><td class="num">${r.positions}</td>
    <td class="num">${num(r.storage)}</td><td class="num">${num(r.intake)}</td><td class="num">${num(r.ship)}</td>
    <td class="num money gold">${num(r.total)}</td><td>${esc(r.author)}</td>
  </tr>`).join("");
}

function closeBillDetail() {
  $("billDetail").classList.remove("on");
}

async function openBillDetail(id) {
  const res = await api("/api/invoices/" + id);
  const inv = res.invoice;
  $("bdTitle").textContent = "Счёт " + (inv.number || "#" + inv.id);
  $("bdSub").textContent = [inv.client, inv.created, inv.author ? "выставил " + inv.author : ""].filter(Boolean).join(" · ");
  $("bdStorage").innerHTML = num(inv.storage) + " <small>₽</small>";
  $("bdIntake").innerHTML = num(inv.intake) + " <small>₽</small>";
  $("bdShip").innerHTML = num(inv.ship) + " <small>₽</small>";
  $("bdTotal").innerHTML = num(inv.total) + " <small>₽</small>";
  const body = $("bdTbl").querySelector("tbody");
  if (!res.positions.length) {
    body.innerHTML = `<tr><td colspan="8" class="empty">Позиции счёта не сохранились.</td></tr>`;
  } else {
    body.innerHTML = res.positions.map((r) => `<tr>
      <td class="code">${esc(r.article)}</td>
      <td class="code">${esc(r.barcode)}</td>
      <td class="name">${esc(r.name)}</td>
      <td>${esc(r.received)}</td>
      <td class="num">${num(r.storage)}</td>
      <td class="num">${num(r.intake)}</td>
      <td class="num">${num(r.ship)}</td>
      <td class="num money">${num(r.total)}</td>
    </tr>`).join("");
  }
  $("billDetail").classList.add("on");
}

$("bTbl").onclick = (e) => {
  const row = e.target.closest("tr[data-bill]");
  if (!row) return;
  openBillDetail(row.dataset.bill).catch((err) => say($("sMsg"), err.message, "bad"));
};
$("bdClose").onclick = closeBillDetail;
$("billDetail").onclick = (e) => { if (e.target === $("billDetail")) closeBillDetail(); };

function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  localStorage.setItem("bg-theme", theme);
  const label = $("themeLabel");
  if (label) label.textContent = theme === "dark" ? "Тёмная" : "Светлая";
}
applyTheme(localStorage.getItem("bg-theme") || "light");
$("themeToggle").onclick = () => {
  applyTheme(document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark");
};

(function () {
  let active = null;
  const reset = (el) => {
    el.style.setProperty("--rx", "0deg");
    el.style.setProperty("--ry", "0deg");
    el.style.setProperty("--ty", "0px");
    el.classList.remove("tilt-active");
  };
  document.addEventListener("pointermove", (e) => {
    const card = e.target.closest(".tilt");
    if (!card) { if (active) { reset(active); active = null; } return; }
    if (card !== active) { if (active) reset(active); active = card; card.classList.add("tilt-active"); }
    const r = card.getBoundingClientRect();
    const px = (e.clientX - r.left) / r.width;
    const py = (e.clientY - r.top) / r.height;
    card.style.setProperty("--rx", ((px - 0.5) * 6).toFixed(2) + "deg");
    card.style.setProperty("--ry", ((0.5 - py) * 6).toFixed(2) + "deg");
    card.style.setProperty("--ty", "-3px");
    card.style.setProperty("--mx", (px * 100).toFixed(1) + "%");
    card.style.setProperty("--my", (py * 100).toFixed(1) + "%");
  });
  document.addEventListener("pointerleave", () => { if (active) { reset(active); active = null; } });
})();

boot();
