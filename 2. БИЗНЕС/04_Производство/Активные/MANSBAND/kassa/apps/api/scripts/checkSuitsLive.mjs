// Прогон расчёта комплектности на живых данных МойСклад, без базы.
// Запуск: MOYSKLAD_TOKEN=... node apps/api/scripts/checkSuitsLive.mjs
//
// Тянет assortment с характеристиками и отчёт по остаткам по складам, собирает
// строки в том же виде, в каком их отдаёт БД, и считает тем же кодом из shared.
import {
  computeCompleteness,
  isHalfSetWarehouse,
  suitLineOf,
  suitPartOf,
} from "../../../packages/shared/dist/index.js";

const TOKEN = process.env.MOYSKLAD_TOKEN;
if (!TOKEN) {
  console.error("Нужен MOYSKLAD_TOKEN в окружении");
  process.exit(1);
}
const BASE = "https://api.moysklad.ru/api/remap/1.2";

async function get(path) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { Authorization: `Bearer ${TOKEN}`, "Accept-Encoding": "gzip" },
  });
  if (!res.ok) throw new Error(`${res.status} ${path}: ${await res.text()}`);
  return res.json();
}

function chars(row) {
  const map = new Map();
  for (const c of row.characteristics ?? []) {
    const name = (c.name ?? "").trim().toLowerCase();
    const value = (c.value ?? "").trim();
    if (name && value) map.set(name, value);
  }
  return map;
}

const products = new Map();
for (let offset = 0; ; offset += 1000) {
  const page = await get(`/entity/assortment?limit=1000&offset=${offset}`);
  for (const row of page.rows) {
    if (row.meta.type !== "product" && row.meta.type !== "variant") continue;
    const part = suitPartOf(row.name);
    if (!part) continue;
    const c = chars(row);
    const variation = (c.get("вариация") ?? row.code ?? "").trim();
    if (!variation) continue;
    products.set(row.id, {
      msId: row.id,
      variation,
      size: c.get("размер") ?? c.get("рзамер") ?? "",
      height: c.get("ростовка") ?? "",
      color: c.get("цвет") ?? null,
      pattern: c.get("узорность") ?? null,
      fit: c.get("крой") ?? "",
      part,
      line: suitLineOf(row.name),
    });
  }
  if (page.rows.length < 1000) break;
}
console.log(`костюмных модификаций в каталоге: ${products.size}`);

const lines = [];
for (let offset = 0; ; offset += 1000) {
  const page = await get(`/report/stock/bystore?limit=1000&offset=${offset}`);
  for (const row of page.rows) {
    const msId = (row.meta?.href ?? "").split("/").pop()?.split("?")[0];
    const product = products.get(msId);
    if (!product) continue;
    for (const byStore of row.stockByStore ?? []) {
      const qty = Math.max(0, Math.floor(Number(byStore.stock) || 0));
      if (qty === 0) continue;
      lines.push({ ...product, warehouse: byStore.name, qty });
    }
  }
  if (page.rows.length < 1000) break;
}
const items = lines.reduce((s, l) => s + l.qty, 0);
const halfSetItems = lines
  .filter((l) => isHalfSetWarehouse(l.warehouse))
  .reduce((s, l) => s + l.qty, 0);
console.log(`строк остатка: ${lines.length}, изделий: ${items}, из них на складах полупарков: ${halfSetItems}`);

for (const tolerance of [0, 1, 2]) {
  const res = computeCompleteness(lines, { tolerance });
  console.log(
    `допуск ±${tolerance}: моделей ${res.totals.models}, цельных ${res.totals.whole}, ` +
      `в допуске ${res.totals.tolerant}, без пары ${res.totals.orphans}, изделий в расчёте ${res.totals.items}`
  );
}

const sample = computeCompleteness(lines, { tolerance: 1, limit: 3 });
for (const model of sample.models) {
  console.log(`\n${model.title} · ${model.variation} (${model.composition.join("+")})`);
  console.log(
    `  цельных ${model.whole}, в допуске ${model.tolerant}, без пары ${model.orphans}, на складах полупарков ${model.onHalfSetWarehouse}`
  );
  for (const size of model.sizes.slice(0, 4)) {
    const orphans = size.orphans
      .map((o) => `${o.part} ${o.qty} без ${o.missing.join("+") || "пары"}`)
      .join("; ");
    console.log(
      `  размер ${size.size}: пиджак ${size.parts.jacket}, брюки ${size.parts.trousers}, жилет ${size.parts.vest} → цельных ${size.whole}, в допуске ${size.tolerant}${orphans ? `, ${orphans}` : ""}`
    );
  }
}
