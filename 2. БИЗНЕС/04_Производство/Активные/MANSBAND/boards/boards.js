/**
 * Раскладка досок: этапы в одну линию слева направо, переходы вперёд —
 * по каналам над линией, возвраты — под линией, провалы — по нижней шине.
 * Геометрия считается, поэтому связи не проходят сквозь карточки.
 */

const NODE_W = 158;
const NODE_H = 54;
const COL_GAP = 60;
const COL_W = NODE_W + COL_GAP;
const LANE = 21;
const PAD_X = 30;
const PAD_TOP = 40;
const RAIL_GAP = 34;

const COLORS = { main: "#3f6fd8", move: "#d9822b", back: "#d9822b", ok: "#2f9e5a", fail: "#8b9099" };
const Z_ORDER = { fail: 0, back: 1, move: 2, main: 3, ok: 4 };

function assignLanes(items) {
  const lanes = [];
  for (const it of items) {
    const lo = Math.min(it.x1, it.x2) - 12;
    const hi = Math.max(it.x1, it.x2) + 12;
    let i = 0;
    for (; i < lanes.length; i += 1) {
      if (lanes[i].every((s) => hi < s.lo || lo > s.hi)) break;
    }
    if (!lanes[i]) lanes[i] = [];
    lanes[i].push({ lo, hi });
    it.lane = i;
  }
  return lanes.length;
}

function spread(count, index) {
  if (count <= 1) return 0;
  const step = Math.min(26, (NODE_W - 44) / (count - 1));
  return -((count - 1) * step) / 2 + index * step;
}

function roundedPath(points, r = 9) {
  const pts = points.filter(
    (p, i) => i === 0 || Math.abs(p.x - points[i - 1].x) > 0.5 || Math.abs(p.y - points[i - 1].y) > 0.5
  );
  if (pts.length < 3) {
    const a = pts[0];
    const b = pts[pts.length - 1];
    return `M ${a.x} ${a.y} L ${b.x} ${b.y}`;
  }
  let d = `M ${pts[0].x} ${pts[0].y}`;
  for (let i = 1; i < pts.length - 1; i += 1) {
    const p = pts[i];
    const a = pts[i - 1];
    const b = pts[i + 1];
    const la = Math.hypot(p.x - a.x, p.y - a.y) || 1;
    const lb = Math.hypot(b.x - p.x, b.y - p.y) || 1;
    const ra = Math.min(r, la / 2);
    const rb = Math.min(r, lb / 2);
    d += ` L ${p.x + ((a.x - p.x) / la) * ra} ${p.y + ((a.y - p.y) / la) * ra}`;
    d += ` Q ${p.x} ${p.y} ${p.x + ((b.x - p.x) / lb) * rb} ${p.y + ((b.y - p.y) / lb) * rb}`;
  }
  const last = pts[pts.length - 1];
  return `${d} L ${last.x} ${last.y}`;
}

function buildModel(key) {
  const def = BOARDS[key];
  const nodes = new Map();
  def.nodes.forEach(([id, label, kind], i) => {
    nodes.set(id, { id, label, kind, col: i, x: PAD_X + i * COL_W });
  });

  const cols = def.nodes.length;
  const fail = { id: "fail", label: "Провал", kind: "fail", col: cols - 1, x: PAD_X + (cols - 1) * COL_W };
  nodes.set("fail", fail);

  const straight = [];
  const forward = [];
  const backward = [];
  const fails = [];

  for (const [from, to, type] of def.edges) {
    const A = nodes.get(from);
    const B = nodes.get(to);
    if (!A || !B) continue;
    const edge = { from, to, type, A, B, x1: A.x + NODE_W / 2, x2: B.x + NODE_W / 2 };
    if (type === "fail") fails.push(edge);
    else if (B.col === A.col + 1) straight.push(edge);
    else if (B.col > A.col) forward.push(edge);
    else backward.push(edge);
  }

  const bySpan = (a, b) => Math.abs(a.x2 - a.x1) - Math.abs(b.x2 - b.x1);
  forward.sort(bySpan);
  backward.sort(bySpan);
  const topLanes = assignLanes(forward);
  const backLanes = assignLanes(backward);

  const topBand = topLanes ? topLanes * LANE + 16 : 0;
  const backBand = backLanes ? backLanes * LANE + 16 : 0;
  const spineY = PAD_TOP + topBand;
  const railY = spineY + NODE_H + backBand + RAIL_GAP;

  nodes.forEach((n) => {
    n.y = n.id === "fail" ? railY : spineY;
    n.cx = n.x + NODE_W / 2;
    n.cy = n.y + NODE_H / 2;
  });

  const outTotal = new Map();
  const inTotal = new Map();
  for (const e of [...forward, ...backward]) {
    outTotal.set(e.from, (outTotal.get(e.from) || 0) + 1);
    inTotal.set(e.to, (inTotal.get(e.to) || 0) + 1);
  }
  const outSeen = new Map();
  const inSeen = new Map();
  const anchors = (e) => {
    const oi = outSeen.get(e.from) || 0;
    const ii = inSeen.get(e.to) || 0;
    outSeen.set(e.from, oi + 1);
    inSeen.set(e.to, ii + 1);
    return {
      sx: e.A.cx + spread(outTotal.get(e.from), oi),
      tx: e.B.cx + spread(inTotal.get(e.to), ii),
    };
  };

  const wires = [];

  for (const e of straight) {
    wires.push({ ...e, d: roundedPath([{ x: e.A.x + NODE_W, y: e.A.cy }, { x: e.B.x, y: e.B.cy }]) });
  }

  for (const e of forward) {
    const { sx, tx } = anchors(e);
    const y = spineY - 14 - e.lane * LANE;
    wires.push({
      ...e,
      d: roundedPath([
        { x: sx, y: e.A.y },
        { x: sx, y },
        { x: tx, y },
        { x: tx, y: e.B.y },
      ]),
    });
  }

  for (const e of backward) {
    const { sx, tx } = anchors(e);
    const y = spineY + NODE_H + 14 + e.lane * LANE;
    wires.push({
      ...e,
      d: roundedPath([
        { x: sx, y: e.A.y + NODE_H },
        { x: sx, y },
        { x: tx, y },
        { x: tx, y: e.B.y + NODE_H },
      ]),
    });
  }

  const railLineY = fail.y + NODE_H / 2;
  fails.forEach((e, i) => {
    const sx = e.A.cx - 12 + (i % 3) * 12;
    wires.push({
      ...e,
      d: roundedPath([
        { x: sx, y: e.A.y + NODE_H },
        { x: sx, y: railLineY },
        { x: fail.x - 2, y: railLineY },
      ]),
    });
  });

  const zones = def.zones.map((z) => {
    const a = nodes.get(z.from);
    const b = nodes.get(z.to);
    const bottom = z.tall ? railY + NODE_H + 16 : spineY + NODE_H + 22;
    return { title: z.title, x: a.x - 16, y: 8, w: b.x + NODE_W + 16 - (a.x - 16), h: bottom - 8 };
  });

  return {
    nodes,
    wires,
    zones,
    width: PAD_X * 2 + cols * COL_W - COL_GAP,
    height: railY + NODE_H + 44,
  };
}

/* ---------- рендер ---------- */

const SVG_NS = "http://www.w3.org/2000/svg";

function svgEl(name, attrs) {
  const el = document.createElementNS(SVG_NS, name);
  for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, String(v));
  return el;
}

function appendMarkers(svg) {
  const defs = svgEl("defs");
  for (const [type, fill] of Object.entries(COLORS)) {
    const size = type === "fail" ? 5 : 6.5;
    const marker = svgEl("marker", {
      id: `mk-${type}`,
      viewBox: "0 0 10 10",
      refX: 8.5,
      refY: 5,
      markerWidth: size,
      markerHeight: size,
      orient: "auto-start-reverse",
    });
    marker.appendChild(svgEl("path", { d: "M 0 0 L 10 5 L 0 10 z", fill }));
    defs.appendChild(marker);
  }
  svg.appendChild(defs);
}

function renderBoard(key) {
  const model = buildModel(key);
  const board = document.createElement("section");
  board.className = "board";
  board.dataset.board = key;

  const scroll = document.createElement("div");
  scroll.className = "scroll";
  const sizer = document.createElement("div");
  sizer.className = "sizer";
  const canvas = document.createElement("div");
  canvas.className = "canvas";
  canvas.style.width = `${model.width}px`;
  canvas.style.height = `${model.height}px`;

  for (const z of model.zones) {
    const box = document.createElement("div");
    box.className = "zone";
    box.style.cssText = `left:${z.x}px;top:${z.y}px;width:${z.w}px;height:${z.h}px`;
    canvas.appendChild(box);
    const title = document.createElement("div");
    title.className = "zone-title";
    title.textContent = z.title;
    title.style.cssText = `left:${z.x + 14}px;top:${z.y + 10}px;max-width:${z.w - 24}px`;
    canvas.appendChild(title);
  }

  const svg = svgEl("svg", { class: "wires", width: model.width, height: model.height });
  appendMarkers(svg);
  [...model.wires]
    .sort((a, b) => Z_ORDER[a.type] - Z_ORDER[b.type])
    .forEach((w) => {
      const path = svgEl("path", { d: w.d, class: `wire ${w.type}`, "marker-end": `url(#mk-${w.type})` });
      path.dataset.from = w.from;
      path.dataset.to = w.to;
      svg.appendChild(path);
    });
  canvas.appendChild(svg);

  model.nodes.forEach((n) => {
    const el = document.createElement("div");
    el.className = `node ${n.kind}`;
    el.textContent = n.label;
    el.dataset.id = n.id;
    el.style.cssText = `left:${n.x}px;top:${n.y}px;width:${NODE_W}px;height:${NODE_H}px`;
    canvas.appendChild(el);
  });

  sizer.appendChild(canvas);
  scroll.appendChild(sizer);
  board.appendChild(scroll);
  return { board, model, canvas, scroll, sizer };
}

/* ---------- поведение ---------- */

const SCALE_MIN = 0.35;
const SCALE_MAX = 1.6;
const SCALE_STEP = 0.08;

const state = { current: "offline", fit: false, scale: 1, boards: {} };

function clampScale(v) {
  return Math.min(SCALE_MAX, Math.max(SCALE_MIN, Math.round(v * 100) / 100));
}

function updateZoomLabel() {
  const el = document.getElementById("btn-zoom-pct");
  if (el) el.textContent = `${Math.round(state.scale * 100)}%`;
}

function applyScale(key) {
  const b = state.boards[key];
  if (!b) return;
  if (state.fit) {
    const availW = b.scroll.clientWidth || window.innerWidth - 48;
    const availH = b.scroll.clientHeight || window.innerHeight - 160;
    const sx = availW / b.model.width;
    const sy = availH / b.model.height;
    state.scale = clampScale(Math.min(1, sx, sy));
  }
  const scale = state.scale;
  b.canvas.style.transform = `scale(${scale})`;
  b.sizer.style.width = `${b.model.width * scale}px`;
  b.sizer.style.height = `${b.model.height * scale}px`;
  updateZoomLabel();
}

function setScale(next, { fit = false } = {}) {
  state.fit = fit;
  state.scale = clampScale(next);
  applyScale(state.current);
}

function zoomBy(delta) {
  setScale(state.scale + delta);
}

function fitBoard(key) {
  state.fit = true;
  applyScale(key || state.current);
}

function clearFocus() {
  document.querySelectorAll(".node, path.wire").forEach((el) => el.classList.remove("dimmed", "sel", "rel"));
  document.getElementById("inspector").hidden = true;
}

function fillList(ul, items) {
  ul.innerHTML = "";
  if (!items.length) {
    const li = document.createElement("li");
    li.className = "empty";
    li.textContent = "—";
    ul.appendChild(li);
    return;
  }
  items.forEach((text) => {
    const li = document.createElement("li");
    li.textContent = text;
    ul.appendChild(li);
  });
}

function focusNode(key, id) {
  const b = state.boards[key];
  const related = new Set([id]);
  const incoming = [];
  const outgoing = [];
  b.model.wires.forEach((w) => {
    if (w.from === id) {
      related.add(w.to);
      outgoing.push(b.model.nodes.get(w.to).label);
    }
    if (w.to === id) {
      related.add(w.from);
      incoming.push(b.model.nodes.get(w.from).label);
    }
  });

  b.board.querySelectorAll("path.wire").forEach((p) => {
    p.classList.toggle("dimmed", p.dataset.from !== id && p.dataset.to !== id);
  });
  b.board.querySelectorAll(".node").forEach((n) => {
    const nid = n.dataset.id;
    n.classList.toggle("dimmed", !related.has(nid));
    n.classList.toggle("sel", nid === id);
    n.classList.toggle("rel", nid !== id && related.has(nid));
  });

  document.getElementById("insp-title").textContent = b.model.nodes.get(id).label;
  fillList(document.getElementById("insp-in"), incoming);
  fillList(document.getElementById("insp-out"), outgoing);
  document.getElementById("inspector").hidden = false;
}

function activate(key) {
  state.current = key;
  clearFocus();
  Object.entries(state.boards).forEach(([k, b]) => b.board.classList.toggle("active", k === key));
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.board === key));
  const url = new URL(location.href);
  url.hash = key;
  history.replaceState(null, "", url);
  requestAnimationFrame(() => applyScale(key));
}

function bindZoom(scroll) {
  // На Mac pinch двумя пальцами приходит как wheel + ctrlKey.
  scroll.addEventListener(
    "wheel",
    (e) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      e.preventDefault();
      const factor = Math.exp(-e.deltaY * 0.01);
      setScale(state.scale * factor);
    },
    { passive: false }
  );

  // Safari gesture events (дополнительно к wheel).
  let gestureBase = 1;
  scroll.addEventListener("gesturestart", (e) => {
    e.preventDefault();
    gestureBase = state.scale;
  });
  scroll.addEventListener("gesturechange", (e) => {
    e.preventDefault();
    setScale(gestureBase * e.scale);
  });
  scroll.addEventListener("gestureend", (e) => e.preventDefault());
}

function init() {
  const stage = document.getElementById("stage");
  const tabs = document.getElementById("tabs");

  Object.keys(BOARDS).forEach((key) => {
    const tab = document.createElement("button");
    tab.className = "tab";
    tab.dataset.board = key;
    tab.textContent = BOARDS[key].tab;
    tab.addEventListener("click", () => activate(key));
    tabs.appendChild(tab);

    const rendered = renderBoard(key);
    state.boards[key] = rendered;
    stage.appendChild(rendered.board);
    bindZoom(rendered.scroll);

    rendered.canvas.addEventListener("click", (event) => {
      const node = event.target.closest(".node");
      if (node) focusNode(key, node.dataset.id);
      else clearFocus();
    });
  });

  document.getElementById("insp-close").addEventListener("click", clearFocus);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") clearFocus();
    if ((e.metaKey || e.ctrlKey) && (e.key === "=" || e.key === "+")) {
      e.preventDefault();
      zoomBy(SCALE_STEP);
    }
    if ((e.metaKey || e.ctrlKey) && e.key === "-") {
      e.preventDefault();
      zoomBy(-SCALE_STEP);
    }
    if ((e.metaKey || e.ctrlKey) && e.key === "0") {
      e.preventDefault();
      setScale(1);
    }
  });

  document.getElementById("btn-zoom-in").addEventListener("click", () => zoomBy(SCALE_STEP));
  document.getElementById("btn-zoom-out").addEventListener("click", () => zoomBy(-SCALE_STEP));
  document.getElementById("btn-zoom-pct").addEventListener("click", () => setScale(1));
  document.getElementById("btn-fit").addEventListener("click", () => fitBoard());

  document.getElementById("btn-copy").addEventListener("click", async () => {
    const link = `${location.href.split("#")[0]}#${state.current}`;
    const btn = document.getElementById("btn-copy");
    try {
      await navigator.clipboard.writeText(link);
      btn.textContent = "Скопировано";
      setTimeout(() => (btn.textContent = "Копировать ссылку"), 1500);
    } catch {
      prompt("Скопируйте ссылку:", link);
    }
  });

  document.getElementById("btn-fs").addEventListener("click", () => {
    if (!document.fullscreenElement) document.documentElement.requestFullscreen?.();
    else document.exitFullscreen?.();
  });

  // Не даём странице зумиться браузером, если pinch попал мимо доски.
  document.addEventListener(
    "wheel",
    (e) => {
      if (e.ctrlKey || e.metaKey) e.preventDefault();
    },
    { passive: false }
  );

  window.addEventListener("resize", () => {
    if (state.fit) fitBoard();
    else applyScale(state.current);
  });

  const hash = location.hash.replace(/^#/, "");
  activate(BOARDS[hash] ? hash : "offline");
}

init();
