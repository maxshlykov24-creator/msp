const $ = (id) => document.getElementById(id);
const state = {
  clients: [], lots: [], ships: [], asm: [],
  picked: new Set(), pickedShip: new Set(), pickedAsm: new Set(), asmGroup: "new", asmGroups: [],
  // поставки WB в таблице «Заказов»: строка поставки вместо пачки заданий
  asmSupplies: [],
  // поставки WB: выбранная поставка, выбранное грузоместо и отмеченные задания внутри
  wbSupplies: [], wbSupply: 0, wbBox: 0, wbPicked: new Set(), wbDetail: null,
};

function openCombo(input, list, fill) {
  fill("");
  list.hidden = false;
  if (input.value) input.select();
}

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
    await loadAsm();
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
  if (btn.dataset.view === "asm") loadAsm();
};

function goView(name) {
  if (name === "ships") name = "asm";
  if (name === "asm" && $("dClient").value) {
    const c = state.clients.find((x) => String(x.id) === $("dClient").value);
    $("aClient").value = $("dClient").value;
    $("aClientQ").value = c ? c.name : $("dClientQ").value;
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

$("dClientQ").onfocus = () => openCombo($("dClientQ"), $("dClientList"), fillDashCombo);
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
    const lines = (res.notes || []).map((n) => Array.isArray(n) ? n.filter(Boolean).join(": ") : String(n || "")).filter(Boolean);
    const text = res.ok
      ? (lines.length ? "Обновил из МойСклад.\n" + lines.join("\n") : "Обновил из МойСклад. Новых контрагентов с группой или тегом «Фулфилмент» нет.")
      : (res.msg || "не обновилось");
    say($("syncNote"), text, res.ok ? (lines.length ? "ok" : "") : "bad");
    if ($("iMsg")) say($("iMsg"), text, res.ok ? "ok" : "bad");
    await loadClients();
    if ($("view-home") && $("view-home").classList.contains("active")) loadOverview();
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
  fillAsmCombo();
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

function intakeState(r) {
  if (r.state === "clash") {
    return `<button class="badge clash" data-pick="${r.id}" title="Выбрать нужный товар"><i></i>${esc(r.note)}</button>`;
  }
  return `<span class="badge ${r.state === "warn" ? "warn" : "ok"}"><i></i>${esc(r.state === "warn" ? r.note : "готово")}</span>`;
}

async function loadIntake() {
  const res = await api("/api/intake");
  state.kinds = res.kinds;
  const body = $("iTbl").querySelector("tbody");
  const miss = res.rows.filter((r) => (r.note || "").startsWith("нет в кабинетах")).length;
  const clash = res.rows.filter((r) => r.state === "clash").length;
  $("iCount").textContent = res.rows.length
    ? res.rows.length + " строк · в кабинетах " + (res.rows.length - miss)
      + (miss ? " · не нашлось " + miss : "")
      + (clash ? " · спорных " + clash : "")
    : "";
  if (!res.rows.length) {
    body.innerHTML = `<tr><td colspan="9" class="empty">Пусто. Загрузи реестр поставки или вставь штрихкоды сверху.</td></tr>`;
    $("iRun").disabled = true;
    return;
  }
  $("iRun").disabled = res.rows.length === clash;
  body.innerHTML = res.rows.map((r) => `<tr>
    <td class="client">${esc(r.client)}${r.supply ? `<span class="badge supply" title="Поставка по реестру">№${esc(r.supply)}</span>` : ""}</td>
    <td class="codes">
      <div class="codes-top"><b>${esc(r.article)}</b>${r.marketplace ? `<span class="badge mp">${esc(r.marketplace)}</span>` : ""}</div>
      <span class="codes-bc">${esc(r.barcode)}</span>
      <label class="codes-gtin"><em>GTIN</em><input class="cell slim" data-id="${r.id}" data-field="gtin" value="${esc(r.gtin)}" placeholder="—"></label>
    </td>
    <td class="name">${esc(r.name)}</td>
    <td><select class="cell slim" data-id="${r.id}" data-field="kind">${kindOptions(r.tracking)}</select></td>
    <td class="num"><input class="cell slim num" data-id="${r.id}" data-field="qty" value="${!r.qty ? "" : r.qty}" placeholder="шт"></td>
    <td class="num"><input class="cell slim num" data-id="${r.id}" data-field="liters" value="${r.liters == null ? "" : (r.dims || r.liters)}" placeholder="л или 8x8x120" title="${r.dims ? esc(r.dims) + " см = " + r.liters + " л" : ""}"></td>
    <td class="num"><input class="cell slim num" data-id="${r.id}" data-field="pick_rate" value="${r.pick_rate == null ? "" : r.pick_rate}" placeholder="₽/шт"></td>
    <td>${intakeState(r)}</td>
    <td><button class="link" data-drop="${r.id}" title="Удалить">✕</button></td>
  </tr>`).join("");
}

$("iClientQ").onfocus = () => openCombo($("iClientQ"), $("iClientList"), fillIntakeCombo);
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
  const pick = e.target.closest("button[data-pick]");
  if (pick) { openPick(pick.dataset.pick); return; }
  const btn = e.target.closest("button[data-drop]");
  if (!btn) return;
  await api("/api/intake/" + btn.dataset.drop, { method: "DELETE" });
  loadIntake();
};

// спорная строка: артикул клиента ведёт на несколько карточек, выбирает оператор

async function openPick(rowId) {
  state.pickRow = rowId;
  $("pickList").innerHTML = "";
  say($("pickMsg"), "");
  $("pickModal").classList.add("on");
  try {
    const res = await api("/api/intake/" + rowId + "/candidates");
    if (!res.rows.length) {
      $("pickList").innerHTML = `<div class="empty">Вариантов не нашлось. Обнови каталог кнопкой «Обновить клиентов».</div>`;
      return;
    }
    $("pickSub").textContent = "Один артикул на несколько карточек. Отметь тот товар, который реально приехал.";
    $("pickList").innerHTML = res.rows.map((c) => `<button type="button" class="pick-item" data-bc="${esc(c.barcode)}">
      <span class="pick-main">
        <b>${esc(c.name || c.barcode)}</b>
        <small>${esc(c.barcode)}${c.size ? " · " + esc(c.size) : ""}${c.gtin ? " · GTIN " + esc(c.gtin) : ""}</small>
      </span>
      <span class="badge mp">${esc(c.marketplace || "—")}</span>
    </button>`).join("");
  } catch (err) {
    say($("pickMsg"), err.message, "bad");
  }
}

$("pickClose").onclick = () => $("pickModal").classList.remove("on");
$("pickModal").onclick = (e) => { if (e.target === $("pickModal")) $("pickModal").classList.remove("on"); };

$("pickList").onclick = async (e) => {
  const btn = e.target.closest(".pick-item[data-bc]");
  if (!btn) return;
  say($("pickMsg"), "Сохраняю…");
  try {
    await api("/api/intake/" + state.pickRow + "/resolve", {
      method: "POST",
      body: JSON.stringify({ barcode: btn.dataset.bc }),
    });
    $("pickModal").classList.remove("on");
    loadIntake();
  } catch (err) {
    say($("pickMsg"), err.message, "bad");
  }
};

// поставки по реестрам

$("iSupplies").onclick = async () => {
  $("supModal").classList.add("on");
  const body = $("supTbl").querySelector("tbody");
  body.innerHTML = `<tr><td colspan="11" class="empty">Загружаю…</td></tr>`;
  try {
    const res = await api("/api/supplies");
    body.innerHTML = res.rows.length
      ? res.rows.map((s) => `<tr>
          <td><b>${esc(s.number || "—")}</b></td>
          <td>${esc(s.client)}</td>
          <td>${esc(s.contract || "—")}</td>
          <td>${esc(s.planned_at || "—")}</td>
          <td>${esc(s.carrier || "—")}</td>
          <td>${esc(s.car_plate || "—")}</td>
          <td>${esc(s.places || "—")}</td>
          <td class="num">${s.rows_total}</td>
          <td class="num">${num(s.qty_total, 0)}</td>
          <td class="num">${s.open_rows || "—"}</td>
          <td>${esc(s.author || "—")}</td>
        </tr>`).join("")
      : `<tr><td colspan="11" class="empty">Реестров пока нет.</td></tr>`;
  } catch (err) {
    body.innerHTML = `<tr><td colspan="11" class="empty">${esc(err.message)}</td></tr>`;
  }
};

$("supClose").onclick = () => $("supModal").classList.remove("on");
$("supModal").onclick = (e) => { if (e.target === $("supModal")) $("supModal").classList.remove("on"); };

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

function fileBase64(file) {
  return file.arrayBuffer().then((buf) => {
    const bytes = new Uint8Array(buf);
    let bin = "";
    for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
    return btoa(bin);
  });
}

function registrySummary(res) {
  const s = res.supply || {};
  const head = [
    s.number ? "Поставка №" + s.number : "",
    s.planned_at ? "на " + s.planned_at : "",
    s.carrier ? s.carrier : "",
    s.car_plate ? s.car_plate : "",
  ].filter(Boolean).join(" · ");
  const bits = [
    `Прочитал ${res.added.length} позиций.`,
    res.matched ? `Сошлось с кабинетами: ${res.matched}.` : "",
    res.clashes ? `Спорных, нужно выбрать товар: ${res.clashes}.` : "",
    res.missing ? `Нет в кабинетах: ${res.missing}.` : "",
    res.skipped.length ? `Уже были в очереди: ${res.skipped.length}.` : "",
    res.merged ? `Одинаковых строк реестра слито: ${res.merged}.` : "",
    (res.problems || []).join(" "),
  ];
  return [head, bits.filter(Boolean).join(" ")].filter(Boolean).join("\n");
}

$("iRegistry").onchange = async () => {
  const file = $("iRegistry").files[0];
  $("iRegistry").value = "";
  if (!file) return;
  const clientId = resolveIntakeClient();
  if (!clientId) { say($("iRegMsg"), "Выбери контрагента.", "bad"); return; }
  say($("iRegMsg"), "Читаю реестр и сверяю с кабинетами…");
  try {
    const res = await api("/api/intake/registry", {
      method: "POST",
      body: JSON.stringify({
        client_id: clientId,
        filename: file.name,
        content: await fileBase64(file),
      }),
    });
    say($("iRegMsg"), registrySummary(res), res.clashes || res.missing ? "" : "ok");
    loadIntake();
  } catch (e) {
    say($("iRegMsg"), e.message, "bad");
  }
};

$("iFile").onchange = async () => {
  const file = $("iFile").files[0];
  $("iFile").value = "";
  if (!file) return;
  const clientId = resolveIntakeClient();
  if (!clientId) { say($("iMsg"), "Выбери контрагента.", "bad"); return; }
  say($("iMsg"), "Читаю файл и сверяю с кабинетами…");
  try {
    const res = await api("/api/intake/file", {
      method: "POST",
      body: JSON.stringify({
        client_id: clientId,
        filename: file.name,
        content: await fileBase64(file),
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
  if ($("sTo").value) params.set("date_to", $("sTo").value);
  return params.toString();
}

function lastFriday() {
  const d = new Date();
  d.setDate(d.getDate() - ((d.getDay() + 2) % 7));
  return d;
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
  $("kLiterDays").textContent = num(res.totals.liter_days, 0);
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
      <td class="name">${esc(r.name)}</td>
      <td class="num" title="${r.dims ? esc(r.dims) + " см" : ""}">${num(r.liters, 3)}</td><td class="num">${num(r.pick_rate)}</td>
      <td class="num">${num(r.qty_in, 0)}</td><td class="num">${num(r.shipped, 0)}</td>
      <td class="num">${num(r.qty, 0)}</td>
      <td>${r.accepted ? esc(r.accepted) : `<span class="badge warn" title="Счётчик пойдёт после приёмки на склад «Фулфилмент»"><i></i>ждёт приёмки</span>`}</td><td class="num ${r.days > 90 ? "aged" : ""}">${r.days}</td>
      <td class="num" title="${esc(r.bill_from)} — ${esc(r.bill_to)}">${r.bill_days}</td>
      <td class="num">${num(r.liter_days, 1)}</td>
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

$("sAccept").onclick = async () => {
  $("sAccept").disabled = true;
  say($("sMsg"), "Смотрю приёмки в МойСклад…");
  try {
    const res = await api("/api/lots/accept", { method: "POST" });
    if (!res.ok) { say($("sMsg"), res.msg, "bad"); return; }
    const parts = [`Проверил партий: ${res.checked}.`];
    parts.push(res.accepted.length ? `Встали на счётчик: ${res.accepted.length}.` : "Новых приёмок нет.");
    if (res.errors.length) parts.push(res.errors.join(" "));
    say($("sMsg"), parts.join(" "), res.accepted.length ? "ok" : "");
    if (res.accepted.length) loadLots();
  } catch (e) {
    say($("sMsg"), e.message, "bad");
  } finally {
    $("sAccept").disabled = false;
  }
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

$("sClientQ").onfocus = () => openCombo($("sClientQ"), $("sClientList"), fillClientCombo);
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
$("sTo").onchange = () => loadLots();
$("sWeek").onclick = () => { $("sTo").value = isoDay(lastFriday()); loadLots(); };
$("sExport").onclick = () => { window.location = "/api/export.xlsx?" + stockQuery(); };
$("sCal").onclick = () => openCalendar();
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

function ruDay(iso) {
  const t = String(iso || "").slice(0, 10);
  return t.length === 10 ? t.slice(8, 10) + "." + t.slice(5, 7) + "." + t.slice(0, 4) : "";
}

$("sBill").onclick = async () => {
  try {
    const res = await api("/api/invoice/preview", {
      method: "POST",
      body: JSON.stringify({ lot_ids: [...state.picked], date_to: $("sTo").value }),
    });
    $("mTitle").textContent = "Счёт: " + res.client;
    $("mPeriod").textContent = "Хранение " + ruDay(res.period_from) + " — " + ruDay(res.period_to) + " · литро-суток " + num(res.liter_days, 1);
    $("mLines").innerHTML = res.lines.map((l) => `<div class="inv-line"><span>${esc(l.name)}</span><b>${num(l.sum)} ₽</b></div>`).join("");
    $("mTotal").textContent = num(res.total) + " ₽";
    say($("mMsg"), res.total > 0 ? "" : "По выбранным позициям всё уже выставлено.", res.total > 0 ? "" : "bad");
    $("mOk").disabled = res.total <= 0;
    $("modal").classList.add("on");
  } catch (e) {
    say($("mMsg"), e.message, "bad");
    $("mPeriod").textContent = "";
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
    const res = await api("/api/invoice", {
      method: "POST",
      body: JSON.stringify({ lot_ids: [...state.picked], date_to: $("sTo").value }),
    });
    $("modal").classList.remove("on");
    say($("iRunMsg"), "");
    await loadLots();
    say($("sMsg"), "Счёт " + (res.number || "") + " за " + ruDay(res.period_from) + " — " + ruDay(res.period_to) + " на " + num(res.total) + " ₽ создан в МойСклад.", "ok");
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
  const withMs = state.ships.filter((r) => state.pickedShip.has(r.id) && r.ms_url).length;
  $("shMs").disabled = withMs === 0;
}

async function loadShips() {
  tintMp("view-ships", "shMp");
  const res = await api("/api/shipments?" + shipQuery());
  state.ships = res.rows;
  state.pickedShip.clear();
  $("kShip").textContent = num(res.totals.positions, 0);
  $("kShipQty").textContent = num(res.totals.qty, 0);
  $("kShipMarks").innerHTML = num(res.totals.marks, 0);
  $("kShipEmpty").textContent = num(res.totals.without, 0);
  const body = $("shTbl").querySelector("tbody");
  if (!res.rows.length) {
    body.innerHTML = `<tr><td colspan="12" class="empty">Нет отправлений за период. Нажми «Обновить отгрузки».</td></tr>`;
  } else {
    body.innerHTML = res.rows.map((r) => `<tr>
      <td class="pick"><input type="checkbox" data-ship="${r.id}"></td>
      <td class="client">${esc(r.client)}</td>
      <td><span class="badge mp">${esc(r.marketplace)}</span></td>
      <td>${esc((r.kind || "").toUpperCase())}</td>
      <td class="code">${esc(r.ext_id)}</td>
      <td>${r.ms_url ? `<a class="ms-link" href="${esc(r.ms_url)}" target="_blank" rel="noopener">печать</a>` : `<span class="mute">—</span>`}</td>
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

$("shClientQ").onfocus = () => openCombo($("shClientQ"), $("shClientList"), fillShipCombo);
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
// сборка

function fillAsmCombo(query) {
  const list = $("aClientList");
  if (!list) return;
  list.innerHTML = clientMatches(query).map((c) => {
    const on = String(c.id) === String($("aClient").value);
    return `<button type="button" class="combo-item${c.id === "" ? " muted" : ""}${on ? " is-on" : ""}" data-id="${c.id}">${esc(c.name)}</button>`;
  }).join("") || `<div class="combo-item muted">Никого не нашлось</div>`;
}

function pickAsmClient(id, name) {
  $("aClient").value = id || "";
  $("aClientQ").value = id ? name : "";
  $("aClientList").hidden = true;
  loadAsm();
}

function localStamp(d) {
  const p = (n) => String(n).padStart(2, "0");
  return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()) + "T" + p(d.getHours()) + ":" + p(d.getMinutes());
}

// Смены склада: утро до 14:00, вечер после. Границу подсказал Сергей — отгрузка
// уходит дважды в день, и заказы делят по времени поступления.
function setShift(kind) {
  const now = new Date();
  const day = (h, m) => {
    const d = new Date(now);
    d.setHours(h, m, 0, 0);
    return localStamp(d);
  };
  if (kind === "today") { $("aSince").value = day(0, 0); $("aUntil").value = day(23, 59); return; }
  if (kind === "morning") { $("aSince").value = day(0, 0); $("aUntil").value = day(14, 0); return; }
  if (kind === "evening") { $("aSince").value = day(14, 0); $("aUntil").value = day(23, 59); }
}

function tintMp(viewId, navId) {
  const mp = ((document.querySelector("#" + navId + " button.active") || {}).dataset || {}).mp || "";
  $(viewId).classList.toggle("theme-all", mp === "");
  $(viewId).classList.toggle("theme-ozon", mp === "ozon");
  $(viewId).classList.toggle("theme-wb", mp === "wb");
}

// колонок в таблице «Заказов»: считаем один раз, чтобы пустая строка и строка
// поставки не разъезжались с шапкой при добавлении колонки
const ASM_COLS = 11;

function asmQuery(group) {
  const params = new URLSearchParams();
  if ($("aClient").value) params.set("client_id", $("aClient").value);
  const mp = document.querySelector("#aMp button.active");
  if (mp && mp.dataset.mp) params.set("mp", mp.dataset.mp);
  const kind = document.querySelector("#aKind button.active");
  if (kind && kind.dataset.kind) params.set("kind", kind.dataset.kind);
  if ($("aQuery").value.trim()) params.set("q", $("aQuery").value.trim());
  // datetime-local отдаёт 'ГГГГ-ММ-ДДTЧЧ:ММ', в базе храним через пробел
  if ($("aSince").value) params.set("since", $("aSince").value.replace("T", " "));
  if ($("aUntil").value) params.set("until", $("aUntil").value.replace("T", " "));
  if (group) params.set("group", group);
  return params.toString();
}

function asmTabs(groups) {
  state.asmGroups = groups;
  $("aTabs").innerHTML = groups.map((g) => `<button type="button" data-group="${g.code}"${g.code === state.asmGroup ? ' class="active"' : ""}>
    ${esc(g.label)}<span class="cnt">${g.count}</span>
  </button>`).join("");
}

function asmGroupLabel() {
  const hit = (state.asmGroups || []).find((g) => g.code === state.asmGroup);
  return hit ? hit.label : state.asmGroup;
}

let asmLoad = 0;

function asmLoading() {
  const body = $("aTbl") && $("aTbl").querySelector("tbody");
  if (body) body.innerHTML = `<tr><td colspan="${ASM_COLS}" class="asm-wait"><span class="spin"></span>Загружаю вкладку…</td></tr>`;
  if ($("aTabs")) $("aTabs").classList.add("is-wait");
}

async function loadAsm() {
  const mine = ++asmLoad;
  if (!$("aSince").value) {
    const shift = ((document.querySelector("#aShift button.active") || {}).dataset || {}).shift || "today";
    setShift(shift);
  }
  tintMp("view-asm", "aMp");
  asmLoading();
  let res;
  try {
    res = await api("/api/assembly?" + asmQuery(state.asmGroup));
  } catch (e) {
    if (mine !== asmLoad) return;
    $("aTabs").classList.remove("is-wait");
    $("aTbl").querySelector("tbody").innerHTML = `<tr><td colspan="${ASM_COLS}" class="empty">${esc(e.message)}</td></tr>`;
    return;
  }
  if (mine !== asmLoad) return;
  state.asm = res.rows;
  state.asmSupplies = res.supplies || [];
  state.pickedAsm.clear();
  // галка «выбрать все» живёт вне таблицы и при перерисовке не сбрасывается сама:
  // иначе она остаётся отмеченной при пустом выборе, и клик по ней снимает выбор
  $("aAll").checked = false;
  $("aTabs").classList.remove("is-wait");
  asmTabs(res.groups);
  refreshAsmPick();
  const body = $("aTbl").querySelector("tbody");
  if (!res.rows.length) {
    body.innerHTML = `<tr><td colspan="${ASM_COLS}" class="empty">Здесь пусто. Проверь период и нажми «Обновить отправления».</td></tr>`;
    return;
  }
  body.innerHTML = asmBodyHtml(res.rows);
}

function asmRowHtml(r) {
  return `<tr>
    <td class="pick"><input type="checkbox" data-asm="${r.id}"></td>
    <td class="client">${esc(r.client)}</td>
    <td class="ext">${esc(r.ext_id)}<span class="badge mp">${esc(r.marketplace)}</span><span class="badge mp">${esc((r.kind || "").toUpperCase())}</span></td>
    <td class="when">${esc(r.accepted || "—")}</td>
    <td class="ph">${r.image ? `<img src="${esc(r.image)}" alt="" loading="lazy" title="увеличить">` : `<span class="noph"></span>`}</td>
    <td class="artq"><b>${num(r.qty, 0)} шт</b> · ${r.article ? `<button type="button" class="artlink" data-art="${esc(r.article)}" title="Найти этот артикул">${esc(r.article)}</button>` : "—"}</td>
    <td class="nm" title="${esc(r.name)}">${esc(r.name || "—")}</td>
    <td class="trk">${esc(r.track || "—")}</td>
    <td class="dest">${r.office ? esc(r.office) : "—"}${r.cargo ? `<span class="cargo">${esc(r.cargo)}</span>` : ""}</td>
    <td class="sup">${r.supply ? `<span class="badge supply">${esc(r.supply)}</span>` : "—"}${r.box ? `<span class="badge box">${esc(r.box)}</span>` : ""}</td>
    <td class="num">${r.marks || "—"}</td>
  </tr>`;
}

// Поставка — та же строка таблицы, что и заказ, но с бейджем и полосой WB.
// Задания внутри не раскрываем: состав, короба и сдача живут в окне поставки.
function asmSupplyHtml(sup, rows) {
  const bits = [sup.orders + " заданий", sup.boxes + " коробов"];
  if (sup.loose) bits.push("без короба " + sup.loose);
  if (!sup.pickup && sup.cargo) bits.push("короба не нужны");
  const qty = rows.reduce((a, r) => a + Number(r.qty || 0), 0);
  const marks = rows.reduce((a, r) => a + Number(r.marks || 0), 0);
  const office = sup.office || (rows.length && rows[0].office ? rows[0].office : "—");
  return `<tr class="sup-head" data-supply="${esc(sup.ext_id)}" data-sid="${sup.id}" title="Открыть поставку">
    <td class="pick"><input type="checkbox" data-supbox="${esc(sup.ext_id)}" title="Выбрать все задания поставки"></td>
    <td class="client">${esc(sup.client)}</td>
    <td class="ext">${esc(sup.ext_id)}<span class="badge supply-kind">Поставка</span><span class="badge mp">WB</span><span class="sup-open">открыть</span></td>
    <td class="when">${esc(sup.created || "—")}</td>
    <td class="ph"><span class="sup-ico" aria-hidden="true"></span></td>
    <td class="artq"><b>${qty} шт</b></td>
    <td class="nm" title="${esc(bits.join(" · "))}">${esc(bits.join(" · "))}</td>
    <td class="trk">${sup.state === "delivered" ? "передана" : "на сборке"}</td>
    <td class="dest">${esc(office)}${sup.cargo ? `<span class="cargo">${esc(sup.cargo)}</span>` : ""}</td>
    <td class="sup"><span class="badge supply">${esc(sup.name || "поставка")}</span></td>
    <td class="num">${marks || "—"}</td>
  </tr>`;
}

function asmBodyHtml(rows) {
  // в «Новых» поставки ещё нет: каждое задание само по себе
  if (state.asmGroup === "new") {
    return rows.map((r) => asmRowHtml(r)).join("");
  }
  const bySupply = new Map();
  const loose = [];
  rows.forEach((r) => {
    if (r.supply) {
      if (!bySupply.has(r.supply)) bySupply.set(r.supply, []);
      bySupply.get(r.supply).push(r);
    } else {
      loose.push(r);
    }
  });
  let html = "";
  (state.asmSupplies || []).forEach((sup) => {
    const kids = bySupply.get(sup.ext_id);
    if (!kids) return;
    bySupply.delete(sup.ext_id);
    html += asmSupplyHtml(sup, kids);
  });
  // поставка есть у задания, но самой поставки в базе нет: заводили не мы
  bySupply.forEach((kids) => { html += kids.map((r) => asmRowHtml(r)).join(""); });
  return html + loose.map((r) => asmRowHtml(r)).join("");
}

function hidePrintMenu() {
  $("aPrintMenu").hidden = true;
}

// Поставка живёт сразу в нескольких вкладках: часть заданий ещё на сборке,
// часть уже «ожидают отгрузки». В таблице текущей вкладки видны не все.
// Печать и галка на строке поставки должны брать весь состав, не только вкладку.
function supplyMembers(ext) {
  const sup = (state.asmSupplies || []).find((s) => s.ext_id === ext);
  if (sup && sup.ship_ids && sup.ship_ids.length) return sup.ship_ids.map(Number);
  return state.asm.filter((r) => r.supply === ext).map((r) => r.id);
}

function pickedAsmExpanded() {
  const ids = new Set(state.pickedAsm);
  (state.asmSupplies || []).forEach((s) => {
    const members = (s.ship_ids || []).map(Number);
    if (members.some((id) => ids.has(id))) members.forEach((id) => ids.add(id));
  });
  return [...ids];
}

function refreshAsmDock() {
  const group = state.asmGroup;
  $("aActsNew").hidden = group !== "new";
  $("aActsAssembling").hidden = group !== "assembling";
  $("aActsReady").hidden = group !== "ready";
  if (group !== "assembling") hidePrintMenu();
}

// после перерисовки тела таблицы галочки надо расставить заново: разметка новая,
// а выбор оператора живёт в state
function restoreAsmPick() {
  $("aTbl").querySelectorAll("input[data-asm]").forEach((b) => {
    b.checked = state.pickedAsm.has(Number(b.dataset.asm));
  });
  $("aTbl").querySelectorAll("input[data-supbox]").forEach((b) => {
    const kids = supplyMembers(b.dataset.supbox);
    b.checked = kids.length > 0 && kids.every((id) => state.pickedAsm.has(id));
  });
  refreshAsmPick();
}

function refreshAsmPick() {
  const n = state.pickedAsm.size;
  const marks = state.asm.filter((r) => state.pickedAsm.has(r.id)).reduce((a, r) => a + (r.marks || 0), 0);
  $("aSel").textContent = n ? "Выбрано " + n : "Ничего не выбрано";
  ["aWork", "aDone", "aPrint", "aShipped", "aBoxQr", "aBackAsm"].forEach((id) => { $(id).disabled = !n; });
  // коды маркировки вносим по одному отправлению: у каждого свой набор
  $("aKiz").disabled = n !== 1;
  if (!n) hidePrintMenu();
  $("shReport").disabled = !n;
  $("shExport").disabled = marks === 0;
  $("aTbl").querySelectorAll("tr").forEach((tr) => {
    const box = tr.querySelector("input[data-asm]");
    const sup = tr.querySelector("input[data-supbox]");
    tr.classList.toggle("picked", !!(box && box.checked) || !!(sup && sup.checked));
  });
  refreshAsmDock();
}

$("aTabs").onclick = (e) => {
  const btn = e.target.closest("button[data-group]");
  if (!btn || $("aTabs").classList.contains("is-wait")) return;
  state.asmGroup = btn.dataset.group;
  $("aTabs").querySelectorAll("button").forEach((b) => b.classList.toggle("active", b === btn));
  loadAsm();
};

$("aClientQ").onfocus = () => openCombo($("aClientQ"), $("aClientList"), fillAsmCombo);
$("aClientQ").oninput = () => {
  fillAsmCombo($("aClientQ").value);
  $("aClientList").hidden = false;
  if (!$("aClientQ").value.trim() && $("aClient").value) {
    $("aClient").value = "";
    loadAsm();
  }
};
$("aClientQ").onkeydown = (e) => {
  if (e.key === "Escape") $("aClientList").hidden = true;
  if (e.key === "Enter") {
    e.preventDefault();
    const first = $("aClientList").querySelector(".combo-item[data-id]");
    if (first) pickAsmClient(first.dataset.id, first.textContent);
  }
};
$("aClientList").onclick = (e) => {
  const btn = e.target.closest(".combo-item[data-id]");
  if (btn) pickAsmClient(btn.dataset.id, btn.textContent);
};

$("aMp").onclick = (e) => {
  const btn = e.target.closest("button[data-mp]");
  if (!btn) return;
  $("aMp").querySelectorAll("button").forEach((b) => b.classList.toggle("active", b === btn));
  loadAsm();
};

$("aKind").onclick = (e) => {
  const btn = e.target.closest("button[data-kind]");
  if (!btn) return;
  $("aKind").querySelectorAll("button").forEach((b) => b.classList.toggle("active", b === btn));
  loadAsm();
};

$("aShift").onclick = (e) => {
  const btn = e.target.closest("button[data-shift]");
  if (!btn) return;
  $("aShift").querySelectorAll("button").forEach((b) => b.classList.toggle("active", b === btn));
  setShift(btn.dataset.shift);
  loadAsm();
};

$("aNow").onclick = () => {
  $("aUntil").value = localStamp(new Date());
  $("aShift").querySelectorAll("button").forEach((b) => b.classList.remove("active"));
  loadAsm();
};

$("aSince").onchange = $("aUntil").onchange = () => {
  $("aShift").querySelectorAll("button").forEach((b) => b.classList.remove("active"));
  loadAsm();
};

let asmTimer = null;
$("aQuery").oninput = () => {
  clearTimeout(asmTimer);
  asmTimer = setTimeout(loadAsm, 250);
};

function photoZoomSrc(url) {
  return (url || "")
    .replace("/images/tm/", "/images/c516x688/")
    .replace("/wc50/", "/wc400/");
}

function openPhoto(url) {
  $("photoZoomImg").src = photoZoomSrc(url);
  $("photoZoom").hidden = false;
  $("photoZoom").classList.add("on");
}

function closePhoto() {
  $("photoZoom").classList.remove("on");
  $("photoZoom").hidden = true;
  $("photoZoomImg").src = "";
}

$("photoZoom").onclick = closePhoto;
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && $("photoZoom").classList.contains("on")) closePhoto();
});

$("aTbl").onclick = (e) => {
  const pic = e.target.closest("td.ph img");
  if (pic) {
    openPhoto(pic.src);
    return;
  }
  const art = e.target.closest("button[data-art]");
  if (art) {
    // клик по артикулу отбирает ровно этот артикул: так оператор набирает
    // партию под печать этикеток одним движением
    $("aQuery").value = $("aQuery").value === art.dataset.art ? "" : art.dataset.art;
    loadAsm();
    return;
  }
  const supbox = e.target.closest("input[data-supbox]");
  if (supbox) {
    // галка на поставке отмечает все её задания: кнопки дока работают по id
    const ext = supbox.dataset.supbox;
    const kids = supplyMembers(ext);
    kids.forEach((id) => { if (supbox.checked) state.pickedAsm.add(id); else state.pickedAsm.delete(id); });
    restoreAsmPick();
    return;
  }
  const head = e.target.closest("tr.sup-head");
  if (head) {
    openWb(head.dataset.sid);
    return;
  }
  const tr = e.target.closest("tbody tr");
  const box = tr && tr.querySelector("input[data-asm]");
  if (!box) return;
  if (e.target !== box) box.checked = !box.checked;
  const id = Number(box.dataset.asm);
  if (box.checked) state.pickedAsm.add(id); else state.pickedAsm.delete(id);
  refreshAsmPick();
};

$("aAll").onclick = () => {
  const on = $("aAll").checked;
  state.pickedAsm.clear();
  if (on) state.asm.forEach((r) => state.pickedAsm.add(r.id));
  restoreAsmPick();
};

async function asmWork(stateCode, nextGroup, label) {
  const ids = [...state.pickedAsm];
  if (!ids.length) return;
  say($("aMsg"), "Сохраняю…");
  try {
    await api("/api/assembly/work", { method: "POST", body: JSON.stringify({ ids, state: stateCode }) });
    say($("aMsg"), label + ": " + ids.length, "ok");
    state.asmGroup = nextGroup;
    await loadAsm();
  } catch (e) {
    say($("aMsg"), e.message, "bad");
  }
}

// поставки WB в выборке, по которым можно заводить короба: только те, что едут
// на ПВЗ — в сортировочный центр товар сдают без коробов
function pickedPickupSupplies() {
  const exts = new Set(state.asm.filter((r) => state.pickedAsm.has(r.id) && r.supply).map((r) => r.supply));
  return (state.asmSupplies || []).filter((s) => exts.has(s.ext_id) && s.pickup && s.state === "open");
}

async function asmDone() {
  const ids = [...state.pickedAsm];
  if (!ids.length) return;
  let boxes = 0;
  const supplies = pickedPickupSupplies();
  if (supplies.length === 1) {
    const sup = supplies[0];
    const answer = await askNumber(
      "Сколько коробов в поставке " + sup.ext_id + "?",
      "WB печатает QR на короб, а не на поставку. Заведи столько коробов, сколько реально собрал:"
        + " максимум " + Math.max(1, Math.floor(sup.orders / 2)) + " при " + sup.orders + " заданиях (WB даёт половину, округление вниз), уже создано " + sup.boxes + "."
        + " Состав короба площадке не передаётся, его заводит ПВЗ при приёмке. Ноль — пропустить.",
      "Собрано",
      Math.max(1, sup.boxes ? 1 : 1)
    );
    if (answer === false) return;
    boxes = Number(answer) || 0;
  } else if (supplies.length > 1) {
    const okay = await ask(
      "В выборке " + supplies.length + " поставки",
      "Короба заводятся на одну поставку за раз: число у них своё. Отметить задания собранными без коробов?",
      "Собрано без коробов"
    );
    if (!okay) return;
  }
  $("aDone").disabled = true;
  const label = $("aDone").textContent;
  $("aDone").textContent = "Собираю…";
  say($("aMsg"), "Собираю на площадке…");
  try {
    const res = await api("/api/assembly/ship", { method: "POST", body: JSON.stringify({ ids, split: true, boxes }) });
    const bits = [];
    if (res.shipped) bits.push("Ozon собрано на площадке: " + res.shipped);
    if ((res.boxes || []).length) bits.push("коробов заведено: " + res.boxes.length + " (" + res.boxes.join(", ") + ")");
    if (res.marked) bits.push("отмечено складом: " + res.marked);
    const notes = (res.notes || []).join("\n");
    say($("aMsg"), (bits.join(" · ") || "Готово") + (notes ? "\n" + notes : ""), notes ? "" : "ok");
    state.asmGroup = "ready";
    await loadAsm();
  } catch (e) {
    say($("aMsg"), e.message, "bad");
  }
  $("aDone").textContent = label;
  refreshAsmPick();
}

// «Взять в сборку»: у WB это добавление в поставку, и шаг необратим — метода
// вынуть задание из поставки в API нет. Предупреждаем до звонка наружу.
async function asmTake() {
  const ids = [...state.pickedAsm];
  if (!ids.length) return;
  const wb = state.asm.filter((r) => state.pickedAsm.has(r.id) && r.marketplace === "wb" && r.kind === "fbs");
  const okay = await ask(
    wb.length
      ? "Открыть поставку WB на " + wb.length + " заданий?"
      : "Взять " + ids.length + " заданий в сборку?",
    wb.length
      ? "Своего «взять в сборку» у WB нет: задание уходит в сборку вместе с поставкой, поэтому поставка откроется сама."
        + " Шаг необратимый — вынуть задание из поставки площадка не даёт."
        + " Задания с разной габаритностью уйдут в разные поставки: WB держит в одной поставке только один тип."
      : "Задания перейдут на вкладку «На сборке».",
    "Взять в сборку"
  );
  if (!okay) return;
  $("aWork").disabled = true;
  const label = $("aWork").textContent;
  $("aWork").textContent = "Беру…";
  say($("aMsg"), "Открываю поставку и беру в сборку…");
  try {
    const res = await api("/api/assembly/take", { method: "POST", body: JSON.stringify({ ids }) });
    const bits = (res.supplies || []).map((s) => s.ext_id + ": " + s.orders);
    if (res.marked) bits.push("отмечено складом: " + res.marked);
    const notes = (res.notes || []).join("\n");
    say($("aMsg"), (bits.join(" · ") || "Готово") + (notes ? "\n" + notes : ""), notes ? "" : "ok");
    state.asmGroup = "assembling";
    await loadAsm();
  } catch (e) {
    say($("aMsg"), e.message, "bad");
  }
  $("aWork").textContent = label;
  refreshAsmPick();
}

$("aWork").onclick = asmTake;
$("aDone").onclick = asmDone;

// коды маркировки: склад пикает сканером, отправляем на площадку набором

let kizState = { id: 0, need: 0, codes: [] };

function kizRender() {
  const have = kizState.codes.length;
  const need = kizState.need;
  $("kizCount").textContent = need
    ? "Нужно кодов: " + need + " · внесено: " + have
    : "Внесено кодов: " + have;
  $("kizCount").classList.toggle("is-ok", need > 0 && have === need);
  $("kizList").innerHTML = have
    ? kizState.codes.map((code, i) => `<div class="kiz-item"><span>${i + 1}</span><code>${esc(code)}</code><button type="button" data-kiz-del="${i}">Убрать</button></div>`).join("")
    : `<div class="empty">Пока ничего не просканировано.</div>`;
  $("kizSend").disabled = !have;
}

function kizAdd(raw) {
  let added = 0;
  let dupe = 0;
  String(raw || "").split(/[\r\n]+/).forEach((part) => {
    const code = part.trim();
    if (!code) return;
    if (kizState.codes.includes(code)) { dupe += 1; return; }
    kizState.codes.push(code);
    added += 1;
  });
  if (dupe) say($("kizMsg"), "Этот код уже в списке — второй раз не беру.", "bad");
  else if (added) say($("kizMsg"), "");
  kizRender();
}

async function openKiz() {
  const ids = [...state.pickedAsm];
  if (ids.length !== 1) {
    say($("aMsg"), "Выбери одно отправление: коды вносим по одному.", "bad");
    return;
  }
  kizState = { id: ids[0], need: 0, codes: [] };
  $("kizSub").textContent = "Спрашиваю площадку, сколько кодов нужно…";
  $("kizInput").value = "";
  say($("kizMsg"), "");
  kizRender();
  $("kizModal").classList.add("on");
  try {
    const res = await api("/api/assembly/" + kizState.id + "/kiz");
    kizState.need = res.need || 0;
    const mp = res.marketplace === "wb" ? "WB" : "Ozon";
    $("kizSub").textContent = mp + " " + res.ext_id + " · " + (res.name || res.article || "") + " · " + res.qty + " шт";
    if (res.marks) kizState.codes = [];
    say($("kizMsg"), res.note || "", res.note ? "" : "");
    kizRender();
    $("kizInput").focus();
  } catch (e) {
    $("kizSub").textContent = "";
    say($("kizMsg"), e.message, "bad");
  }
}

$("aKiz").onclick = openKiz;
$("kizClose").onclick = () => $("kizModal").classList.remove("on");
$("kizModal").onclick = (e) => { if (e.target === $("kizModal")) $("kizModal").classList.remove("on"); };
$("kizClear").onclick = () => { kizState.codes = []; say($("kizMsg"), ""); kizRender(); $("kizInput").focus(); };

$("kizInput").onkeydown = (e) => {
  if (e.key !== "Enter") return;
  e.preventDefault();
  kizAdd($("kizInput").value);
  $("kizInput").value = "";
};
// вставка пачкой из буфера: сканер в режиме «много кодов» отдаёт их строками
$("kizInput").onpaste = (e) => {
  const text = (e.clipboardData || window.clipboardData).getData("text");
  if (!text || !/[\r\n]/.test(text)) return;
  e.preventDefault();
  kizAdd(text);
  $("kizInput").value = "";
};

$("kizList").onclick = (e) => {
  const btn = e.target.closest("button[data-kiz-del]");
  if (!btn) return;
  kizState.codes.splice(Number(btn.dataset.kizDel), 1);
  kizRender();
  $("kizInput").focus();
};

$("kizSend").onclick = async () => {
  if (!kizState.codes.length) return;
  if (kizState.need && kizState.codes.length !== kizState.need) {
    const okay = await ask(
      "Кодов не столько, сколько ждёт площадка",
      "Площадка ждёт " + kizState.need + ", у тебя " + kizState.codes.length + ". Отправить всё равно?",
      "Отправить"
    );
    if (!okay) return;
  }
  $("kizSend").disabled = true;
  say($("kizMsg"), "Отправляю на площадку…");
  try {
    const res = await api("/api/assembly/" + kizState.id + "/kiz", {
      method: "POST",
      body: JSON.stringify({ codes: kizState.codes }),
    });
    const notes = (res.notes || []).join("\n");
    say($("aMsg"), "Коды отправлены: " + res.sent + (notes ? "\n" + notes : ""), notes ? "" : "ok");
    $("kizModal").classList.remove("on");
    await loadAsm();
  } catch (e) {
    say($("kizMsg"), e.message, "bad");
  }
  $("kizSend").disabled = false;
};
$("aBackAsm").onclick = () => asmWork("assembling", "assembling", "Возвращено в сборку");

// поставки WB из выборки, ещё не переданные в доставку
function pickedOpenSupplies() {
  const exts = new Set(state.asm.filter((r) => state.pickedAsm.has(r.id) && r.supply).map((r) => r.supply));
  return (state.asmSupplies || []).filter((s) => exts.has(s.ext_id) && s.state === "open");
}

// «Отгружено» у WB — это передача поставки в доставку, шаг необратимый. У Ozon
// поставок нет, там остаётся складская отметка.
async function asmShipped() {
  const ids = [...state.pickedAsm];
  if (!ids.length) return;
  const supplies = pickedOpenSupplies();
  for (const sup of supplies) {
    const dest = sup.office || (sup.pickup ? "ПВЗ Домодедовская, 28" : "СЦ Кавказский бульвар, 57 стр. 1, Москва");
    const bad = sup.loose && sup.pickup ? " Заданий без короба: " + sup.loose + "." : "";
    const okay = await ask(
      "Передать поставку " + sup.ext_id + " в доставку?",
      dest + ". " + sup.client + " · " + sup.orders + " заданий, " + (sup.pickup ? sup.boxes + " мест." : "короба не нужны.") + bad
        + " Шаг необратимый: WB закроет поставку, задания уйдут в «В доставке», добавить в неё больше ничего нельзя."
        + " QR поставки появится только после этого.",
      "Передать"
    );
    if (!okay) {
      say($("aMsg"), "Отменил: поставка " + sup.ext_id + " осталась на сборке.");
      return;
    }
    try {
      await api("/api/wb/supplies/" + sup.id + "/deliver", { method: "POST", body: JSON.stringify({ confirm: true, force: true }) });
      say($("aMsg"), "Поставка " + sup.ext_id + " передана в доставку. Качаю QR…", "ok");
      await downloadXlsx("/api/wb/supplies/" + sup.id + "/qr.pdf", [], "QR_поставки.pdf", "", null, {});
    } catch (e) {
      say($("aMsg"), e.message, "bad");
      await loadAsm();
      return;
    }
  }
  await asmWork("shipped", "shipped", "Отгружено");
}

$("aShipped").onclick = asmShipped;

$("aBoxQr").onclick = async () => {
  const supplies = pickedOpenSupplies();
  if (supplies.length !== 1) {
    say($("aMsg"), supplies.length ? "Выбери задания одной поставки: QR коробов печатается по поставке." : "В выборке нет открытой поставки WB.", "bad");
    return;
  }
  $("aBoxQr").disabled = true;
  say($("aMsg"), "Запрашиваю QR коробов у WB…");
  try {
    const res = await downloadXlsx("/api/wb/supplies/" + supplies[0].id + "/boxes.pdf", [], "QR_коробов.pdf", "", null, {});
    const notes = decodeURIComponent(res.headers.get("X-Label-Notes") || "");
    say($("aMsg"), "QR коробов в файле: " + (res.headers.get("X-Label-Pages") || "?") + (notes ? "\n" + notes : ""), notes ? "" : "ok");
  } catch (e) {
    say($("aMsg"), e.message, "bad");
  }
  refreshAsmPick();
};

async function printAsm(mode) {
  const ids = pickedAsmExpanded();
  if (!ids.length) return;
  hidePrintMenu();
  $("aPrint").disabled = true;
  const label = $("aPrint").textContent;
  $("aPrint").textContent = "Собираю файл…";
  say($("aMsg"), "Запрашиваю этикетки у площадок…");
  try {
    const res = await downloadXlsx(
      "/api/assembly/labels", ids, "этикетки.pdf", "", null, { mode }
    );
    const pages = res.headers.get("X-Label-Pages") || "?";
    const raw = res.headers.get("X-Label-Notes") || "";
    const notes = raw ? decodeURIComponent(raw) : "";
    say($("aMsg"), "Готово: этикеток в файле " + pages + "." + (notes ? "\n" + notes : ""), notes ? "" : "ok");
  } catch (e) {
    say($("aMsg"), e.message, "bad");
  }
  $("aPrint").textContent = label;
  refreshAsmPick();
}

$("aPrint").onclick = (e) => {
  e.stopPropagation();
  if ($("aPrint").disabled) return;
  $("aPrintMenu").hidden = !$("aPrintMenu").hidden;
};

$("aPrintMenu").onclick = (e) => {
  const btn = e.target.closest("button[data-mode]");
  if (!btn) return;
  printAsm(btn.dataset.mode);
};

document.addEventListener("click", (e) => {
  if (!e.target.closest(".print-wrap")) hidePrintMenu();
});

// лист подбора идёт по фильтру, а не по галочкам: смена отбирается контрагентом
// и вкладкой. PDF A4 открывается сразу — Серёжа шлёт на принтер из окна
$("aPicking").onclick = () => {
  window.open("/api/assembly/picking.pdf?" + asmQuery(state.asmGroup), "_blank");
  say($("aMsg"), "Лист подбора открыт на печать A4: вкладка «" + asmGroupLabel() + "».", "ok");
};

// недельный отчёт клиенту: артикулы по строкам, дни по столбцам. Период берём
// из фильтра «Принят», контрагент — обязателен, отчёт уходит наружу
$("aWeekly").onclick = () => {
  if (!$("aClient").value) {
    say($("aMsg"), "Выбери контрагента: недельный отчёт собирается по одному.", "bad");
    return;
  }
  const params = new URLSearchParams({
    client_id: $("aClient").value,
    date_from: ($("aSince").value || "").slice(0, 10),
    date_to: ($("aUntil").value || "").slice(0, 10),
  });
  window.location = "/api/shipments/weekly.xlsx?" + params.toString();
  say($("aMsg"), "Отчёт собран за период из фильтра «Принят». Макет таблицы сверь с Димой до отправки клиенту.", "ok");
};

$("aSync").onclick = async () => {
  $("aSync").disabled = true;
  $("aSync").textContent = "Обновляю…";
  try {
    const res = await api("/api/shipments/sync", { method: "POST", body: JSON.stringify({ days: 14 }) });
    say($("aMsg"), res.ok ? (res.notes || []).join("\n") || ("Обновлено: " + res.count) : res.msg, res.ok ? "ok" : "bad");
    await loadAsm();
  } catch (e) {
    say($("aMsg"), e.message, "bad");
  }
  $("aSync").disabled = false;
  $("aSync").textContent = "Обновить отправления";
};

// Окно подтверждения для необратимых шагов: сборка на площадке и передача
// поставки в доставку. Обещание разрешается кнопкой, а не ответом сервера.
let askResolve = null;
let askIsNum = false;
function ask(title, text, okLabel) {
  askIsNum = false;
  $("askNum").hidden = true;
  $("askTitle").textContent = title;
  $("askText").textContent = text;
  $("askYes").textContent = okLabel || "Подтверждаю";
  $("askModal").classList.add("on");
  return new Promise((resolve) => { askResolve = resolve; });
}

// то же окно, но с числом: «сколько коробов» спрашивается ровно здесь, на
// «Собрано», как договорились с Сергеем
function askNumber(title, text, okLabel, value) {
  const promise = ask(title, text, okLabel);
  askIsNum = true;
  $("askNum").value = value || 1;
  $("askNum").hidden = false;
  $("askNum").focus();
  $("askNum").select();
  return promise;
}

function askDone(answer) {
  $("askModal").classList.remove("on");
  $("askNum").hidden = true;
  const value = answer && askIsNum ? Math.max(0, Number($("askNum").value) || 0) : answer;
  if (askResolve) askResolve(value);
  askResolve = null;
  askIsNum = false;
}
$("askYes").onclick = () => askDone(true);
$("askNo").onclick = () => askDone(false);
$("askNum").onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); askDone(true); } };
$("askModal").onclick = (e) => { if (e.target === $("askModal")) askDone(false); };


// поставки WB: грузоместа, QR коробов, передача в доставку

$("aSupplies").onclick = () => openWb();
$("wbClose").onclick = () => $("wbModal").classList.remove("on");
$("wbModal").onclick = (e) => { if (e.target === $("wbModal")) $("wbModal").classList.remove("on"); };

function openWb(id) {
  if (id) state.wbSupply = Number(id);
  $("wbModal").classList.add("on");
  state.wbPicked = new Set();
  loadWbSupplies().catch((e) => say($("wbMsg"), e.message, "bad"));
}

async function loadWbSupplies() {
  const params = new URLSearchParams();
  if ($("aClient").value) params.set("client_id", $("aClient").value);
  const res = await api("/api/wb/supplies?" + params.toString());
  state.wbSupplies = res.rows;
  const empty = !res.rows.length;
  const show = !!state.wbSupply;
  $("wbModal").classList.toggle("is-empty", empty && !show);
  $("wbCols").classList.toggle("is-empty", empty && !show);
  $("wbSupplies").innerHTML = empty
    ? `<div class="wb-empty">Поставок нет. Они появляются из «Новых» кнопкой «Взять в сборку».</div>`
    : res.rows.map((r) => `<button type="button" class="wb-item${r.id === state.wbSupply ? " is-on" : ""}" data-supply="${r.id}">
        <b>${esc(r.ext_id)}</b>
        <span>${r.orders} зак. · ${r.boxes} кор.</span>
        <i class="wb-state ${r.state}">${r.state === "delivered" ? "сдана" : "сборка"}</i>
      </button>`).join("");
  if (state.wbSupply) await loadWbDetail(state.wbSupply);
  else $("wbDetail").innerHTML = empty ? "" : `<div class="wb-idle">Выбери поставку слева</div>`;
}

$("wbSupplies").onclick = (e) => {
  const btn = e.target.closest("button[data-supply]");
  if (!btn) return;
  state.wbSupply = Number(btn.dataset.supply);
  state.wbPicked = new Set();
  loadWbSupplies().catch((err) => say($("wbMsg"), err.message, "bad"));
};

async function loadWbDetail(id) {
  const res = await api("/api/wb/supplies/" + id);
  state.wbDetail = res;
  const open = res.supply.state === "open";
  const pickup = res.supply.pickup !== false;
  const dest = res.supply.office || (pickup ? "ПВЗ Домодедовская, 28" : "СЦ Кавказский бульвар, 57 стр. 1, Москва");
  const loose = pickup ? res.rows.filter((r) => !r.box).length : 0;
  const boxes = res.boxes.map((b) => `<button type="button" class="wb-box${b.id === state.wbBox ? " is-on" : ""}" data-box="${b.id}">
      <b>${esc(b.ext_id)}</b>
      <span>${b.orders ? b.orders + " зак." : "пустой"}</span>
      ${open ? `<i data-boxdrop="${b.id}" title="Удалить короб">×</i>` : ""}
    </button>`).join("");
  $("wbDetail").innerHTML = `
    <div class="wb-hero">
      <div>
        <div class="wb-hero-id">${esc(res.supply.ext_id)}</div>
        <div class="wb-hero-meta">${esc(res.supply.client)} · ${esc(dest)} · ${res.rows.length} зак.${pickup ? " · " + res.boxes.length + " кор." : " · короба не нужны"}${loose ? " · без короба " + loose : ""}</div>
      </div>
      <span class="wb-pill ${res.supply.state}">${open ? "На сборке" : "Сдана " + esc(res.supply.delivered)}</span>
    </div>
    <section class="wb-sec">
      <div class="wb-sec-h">
        <h4>Короба</h4>
        <div class="wb-sec-acts">
          ${open && pickup ? `<button class="btn-ghost" id="wbNewBox" type="button">Добавить короб</button>` : ""}
          ${pickup ? `<button class="btn-ghost" id="wbBoxQr" type="button">Печать QR</button>` : ""}
        </div>
      </div>
      <div class="wb-boxes">${pickup ? (boxes || `<div class="wb-empty">Коробов нет. Добавь один — на него печатается QR.</div>`) : `<div class="wb-empty">Эта поставка едет на СЦ. Грузоместа не заводятся.</div>`}</div>
    </section>
    <section class="wb-sec">
      <div class="wb-sec-h">
        <h4>Заказы</h4>
        <div class="wb-sec-acts">
          ${open && loose ? `<button class="btn" id="wbPack" type="button" disabled>В этот короб</button>` : ""}
        </div>
      </div>
      <div class="tbl-wrap wb-rows">
        <table class="tbl" id="wbTbl">
          <thead><tr><th class="pick"></th><th>Задание</th><th>Артикул</th><th>Наименование</th><th>Короб</th></tr></thead>
          <tbody>${res.rows.length
            ? res.rows.map((r) => `<tr${r.box ? ' class="is-boxed"' : ""}>
                <td class="pick">${open && !r.box ? `<input type="checkbox" data-wbrow="${r.id}"${state.wbPicked.has(r.id) ? " checked" : ""}>` : ""}</td>
                <td class="ext">${esc(r.ext_id)}</td>
                <td class="artq"><b>${num(r.qty, 0)}</b> · ${esc(r.article || "—")}</td>
                <td class="nm" title="${esc(r.name)}">${esc(r.name || "—")}</td>
                <td>${r.box ? `<span class="badge box">${esc(r.box)}</span>` : "—"}</td>
              </tr>`).join("")
            : `<tr><td colspan="5" class="empty">Пусто. Задания попадают сюда кнопкой «Взять в сборку».</td></tr>`}</tbody>
        </table>
      </div>
    </section>
    <div class="wb-foot">
      ${open
        ? `<span>${esc(dest)}</span><button class="btn wb-deliver" id="wbDeliver" type="button">${pickup ? "Сдать на ПВЗ" : "Сдать в СЦ"}</button>`
        : `<span>Поставка закрыта</span><button class="btn" id="wbQr" type="button">QR поставки</button>`}
    </div>`;
  bindWbDetail();
}

function refreshWbPack() {
  const btn = $("wbPack");
  const boxed = new Set((state.wbDetail && state.wbDetail.rows || []).filter((r) => r.box).map((r) => r.id));
  [...state.wbPicked].forEach((id) => { if (boxed.has(id)) state.wbPicked.delete(id); });
  if (btn) btn.disabled = !(state.wbBox && state.wbPicked.size);
}

function bindWbDetail() {
  const id = state.wbSupply;
  const guard = async (fn, busy) => {
    say($("wbMsg"), busy);
    try { await fn(); } catch (e) { say($("wbMsg"), e.message, "bad"); }
  };
  const nb = $("wbNewBox");
  if (nb) nb.onclick = () => guard(async () => {
    const res = await api("/api/wb/supplies/" + id + "/boxes", { method: "POST", body: JSON.stringify({ amount: 1 }) });
    say($("wbMsg"), "Короб " + (res.boxes || []).join(", ") + " заведён.", "ok");
    await loadWbSupplies();
  }, "Добавляю короб…");
  const pack = $("wbPack");
  if (pack) pack.onclick = () => guard(async () => {
    const res = await api("/api/wb/boxes/" + state.wbBox + "/orders", {
      method: "POST",
      body: JSON.stringify({ ids: [...state.wbPicked] }),
    });
    const notes = (res.notes || []).join("\n");
    say($("wbMsg"), "Отмечено в " + res.box + ": " + res.packed + " — учёт наш, площадке состав коробки не передаётся." + (notes ? "\n" + notes : ""), notes ? "" : "ok");
    state.wbPicked = new Set();
    await loadWbSupplies();
    await loadAsm();
  }, "Укладываю задания в грузоместо…");
  const qrBox = $("wbBoxQr");
  if (qrBox) qrBox.onclick = () => guard(async () => {
    const res = await downloadXlsx("/api/wb/supplies/" + id + "/boxes.pdf", [], "QR_грузомест.pdf", "", null, {});
    const notes = decodeURIComponent(res.headers.get("X-Label-Notes") || "");
    say($("wbMsg"), "QR грузомест в файле: " + (res.headers.get("X-Label-Pages") || "?") + (notes ? "\n" + notes : ""), notes ? "" : "ok");
  }, "Запрашиваю QR грузомест у WB…");
  const qr = $("wbQr");
  if (qr) qr.onclick = () => guard(async () => {
    await downloadXlsx("/api/wb/supplies/" + id + "/qr.pdf", [], "QR_поставки.pdf", "", null, {});
    say($("wbMsg"), "QR поставки скачан.", "ok");
  }, "Запрашиваю QR поставки…");
  const dv = $("wbDeliver");
  if (dv) dv.onclick = async () => {
    let pre;
    try {
      pre = await api("/api/wb/supplies/" + id + "/preflight");
    } catch (e) {
      say($("wbMsg"), e.message, "bad");
      return;
    }
    const dest = pre.office || "";
    const bad = [];
    if (pre.pickup !== false && pre.loose) bad.push("заданий без грузоместа: " + pre.loose);
    if (pre.pickup !== false && pre.empty_boxes.length) bad.push("пустых грузомест: " + pre.empty_boxes.length);
    const okay = await ask(
      "Передать поставку в доставку?",
      (dest ? dest + ". " : "") + pre.ext_id + " · " + pre.client + ". В поставке " + pre.orders + " заданий"
        + (pre.pickup === false ? ", короба не нужны." : " и " + pre.boxes + " грузомест.")
        + (bad.length ? " Внимание: " + bad.join(", ") + "." : "")
        + " Шаг необратимый: WB закроет поставку, все задания уйдут в «В доставке», добавить в неё больше ничего нельзя. QR поставки появится только после этого.",
      "Передать"
    );
    if (!okay) return;
    await guard(async () => {
      try {
        await api("/api/wb/supplies/" + id + "/deliver", { method: "POST", body: JSON.stringify({ confirm: true }) });
      } catch (e) {
        if (!/без грузоместа/.test(e.message)) throw e;
        const force = await ask("Сдать врассыпную?", e.message + " Передать поставку как есть?", "Передать как есть");
        if (!force) { say($("wbMsg"), "Отменил. Разложи задания по грузоместам."); return; }
        await api("/api/wb/supplies/" + id + "/deliver", { method: "POST", body: JSON.stringify({ confirm: true, force: true }) });
      }
      say($("wbMsg"), "Поставка передана в доставку. Теперь доступен QR поставки.", "ok");
      await loadWbSupplies();
      await loadAsm();
    }, "Передаю поставку в доставку…");
  };
  const wrap = $("wbDetail");
  wrap.onclick = async (e) => {
    const drop = e.target.closest("[data-boxdrop]");
    if (drop) {
      e.stopPropagation();
      const card = drop.closest(".wb-box");
      const label = card && card.querySelector("b") ? card.querySelector("b").textContent : "этот короб";
      const inside = card && card.querySelector("span") ? card.querySelector("span").textContent : "";
      const okay = await ask(
        "Удалить короб " + label + "?",
        "WB снимет грузоместо с поставки" + (inside && inside !== "пустой" ? " (" + inside + ")" : "") + ". Заказы останутся в поставке, но без короба. Если QR уже напечатан и наклеен — он больше не действует, нужен новый короб."
        + " Пока поставка на сборке, это обратимо: короб можно завести снова.",
        "Удалить"
      );
      if (!okay) return;
      guard(async () => {
        await api("/api/wb/boxes/" + drop.dataset.boxdrop, { method: "DELETE" });
        if (state.wbBox === Number(drop.dataset.boxdrop)) state.wbBox = 0;
        say($("wbMsg"), "Короб " + label + " удалён у WB.", "ok");
        await loadWbSupplies();
      }, "Удаляю короб у WB…");
      return;
    }
    const box = e.target.closest(".wb-box[data-box]");
    if (box) {
      state.wbBox = state.wbBox === Number(box.dataset.box) ? 0 : Number(box.dataset.box);
      wrap.querySelectorAll(".wb-box").forEach((el) => el.classList.toggle("is-on", el === box && !!state.wbBox));
      refreshWbPack();
      return;
    }
    const cb = e.target.closest("input[data-wbrow]");
    if (cb) {
      const rid = Number(cb.dataset.wbrow);
      if (cb.checked) state.wbPicked.add(rid); else state.wbPicked.delete(rid);
      refreshWbPack();
    }
  };
  refreshWbPack();
}

async function downloadXlsx(url, ids, fallback, okText, msgEl, extra) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(Object.assign({ ids }, extra || {})),
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
  return res;
}

$("shExport").onclick = async () => {
  $("shExport").disabled = true;
  try {
    await downloadXlsx("/api/shipments/marks.xlsx", [...state.pickedAsm], "коды_маркировки.xlsx", "Файл с кодами скачан.", $("aMsg"));
  } catch (e) {
    say($("aMsg"), e.message, "bad");
  }
  refreshAsmPick();
};
$("shReport").onclick = async () => {
  $("shReport").disabled = true;
  try {
    await downloadXlsx("/api/shipments/report.xlsx", [...state.pickedAsm], "отгрузки.xlsx", "Отчёт по отгрузкам скачан.", $("aMsg"));
  } catch (e) {
    say($("aMsg"), e.message, "bad");
  }
  refreshAsmPick();
};
$("shMs").onclick = () => {
  const urls = [...new Set(state.ships.filter((r) => state.pickedShip.has(r.id) && r.ms_url).map((r) => r.ms_url))];
  if (!urls.length) {
    say($("shMsg"), "У выбранных нет заказа в МойСклад. Сначала подтяни заказы.", "bad");
    return;
  }
  urls.forEach((url) => window.open(url, "_blank", "noopener"));
  say($("shMsg"), "Открыл " + urls.length + " заказ" + (urls.length === 1 ? "" : urls.length < 5 ? "а" : "ов") + " в МойСклад — печать оттуда.", "ok");
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
    body.innerHTML = `<tr><td colspan="8" class="empty">Счетов пока нет.</td></tr>`;
    return;
  }
  body.innerHTML = res.rows.map((r) => `<tr class="clickable" data-bill="${r.id}">
    <td>${esc(r.number)}</td><td>${esc(r.client)}</td><td>${esc(r.period)}</td><td>${esc(r.created)}</td><td class="num">${r.positions}</td>
    <td class="num">${num(r.storage)}</td>
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
  $("bdSub").textContent = [inv.client, "хранение " + inv.period, inv.created, inv.author ? "выставил " + inv.author : ""].filter(Boolean).join(" · ");
  $("bdLiterDays").textContent = num(inv.liter_days, 1);
  $("bdStorage").innerHTML = num(inv.storage) + " <small>₽</small>";
  $("bdPick").innerHTML = num(inv.pick) + " <small>₽</small>";
  $("bdTotal").innerHTML = num(inv.total) + " <small>₽</small>";
  const body = $("bdTbl").querySelector("tbody");
  if (!res.positions.length) {
    body.innerHTML = `<tr><td colspan="7" class="empty">Позиции счёта не сохранились.</td></tr>`;
  } else {
    body.innerHTML = res.positions.map((r) => `<tr>
      <td class="code">${esc(r.article)}</td>
      <td class="name">${esc(r.name)}</td>
      <td>${esc(r.period)}</td>
      <td class="num">${num(r.days, 0)}</td>
      <td class="num">${num(r.liter_days, 1)}</td>
      <td class="num">${num(r.pick, 2)}</td>
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

// календарь хранения

function calQuery() {
  const params = new URLSearchParams();
  if ($("sClient").value) params.set("client_id", $("sClient").value);
  if ($("sQuery").value.trim()) params.set("q", $("sQuery").value.trim());
  if ($("calFrom").value) params.set("date_from", $("calFrom").value);
  if ($("calTo").value) params.set("date_to", $("calTo").value);
  return params.toString();
}

function openCalendar() {
  if (!$("calTo").value) {
    const to = $("sTo").value ? new Date($("sTo").value) : new Date();
    const from = new Date(to);
    from.setDate(from.getDate() - 13);
    $("calFrom").value = isoDay(from);
    $("calTo").value = isoDay(to);
  }
  $("calModal").classList.add("on");
  loadCalendar();
}

async function loadCalendar() {
  say($("calMsg"), "Считаю дни…");
  try {
    const res = await api("/api/calendar?" + calQuery());
    const head = $("calTbl").querySelector("thead tr");
    const body = $("calTbl").querySelector("tbody");
    const foot = $("calTbl").querySelector("tfoot tr");
    head.innerHTML = `<th class="cal-side">Позиция</th><th class="num">Ставка хранения, ₽/л/сутки</th><th class="num">Итого, ₽</th>`
      + res.days.map((d) => `<th class="num cal-day">${d.slice(8, 10)}.${d.slice(5, 7)}</th>`).join("");
    if (!res.rows.length) {
      body.innerHTML = `<tr><td colspan="${res.days.length + 3}" class="empty">За период нет позиций на складе.</td></tr>`;
      foot.innerHTML = "";
      say($("calMsg"), "");
      return;
    }
    body.innerHTML = res.rows.map((r) => `<tr>
      <td class="cal-side"><b>${esc(r.article)}</b><span>${esc(r.name)}</span><span>${esc(r.client)} · ${num(r.liters, 3)} л</span></td>
      <td class="num">${num(r.rate)}</td>
      <td class="num money">${num(r.sum)}</td>
      ${r.cells.map((c) => c.qty === null
        ? `<td class="num cal-cell muted">—</td>`
        : `<td class="num cal-cell${c.ship ? " cal-ship" : ""}" title="${num(c.qty, 0)} шт · ${num(c.sum)} ₽${c.ship ? " · уехало " + num(c.ship, 0) : ""}">${num(c.qty, 0)}</td>`).join("")}
    </tr>`).join("");
    foot.innerHTML = `<td class="cal-side"><b>Итого за день, ₽</b></td><td></td><td class="num money gold">${num(res.total)}</td>`
      + res.per_day.map((d) => `<td class="num cal-cell" title="уехало ${num(d.ship, 0)} шт">${num(d.sum, 0)}</td>`).join("");
    say($("calMsg"), "");
  } catch (e) {
    say($("calMsg"), e.message, "bad");
  }
}

$("calClose").onclick = () => $("calModal").classList.remove("on");
$("calModal").onclick = (e) => { if (e.target === $("calModal")) $("calModal").classList.remove("on"); };
$("calFrom").onchange = $("calTo").onchange = () => loadCalendar();
$("calExport").onclick = () => { window.location = "/api/calendar.xlsx?" + calQuery(); };

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
