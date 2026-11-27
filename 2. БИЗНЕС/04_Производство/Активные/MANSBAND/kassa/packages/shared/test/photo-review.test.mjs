import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { photoCodeKey, settlePhotoReview } from "../dist/index.js";

const raw = JSON.parse(readFileSync(
  new URL("../../../apps/api/src/data/catalogPhotoReview.json", import.meta.url),
  "utf8",
));
const settled = settlePhotoReview(raw);

function names(row) {
  return row.photoIds.map(id => settled.photos[id].name);
}

function find(cell, code) {
  return settled.cases.filter(row => row.cell === cell && row.code === code);
}

test("косая черта и дефис в коде одного артикула не различаются", () => {
  assert.equal(photoCodeKey("SB03/501MM"), photoCodeKey("sb03-501mm.ARW"));
  assert.notEqual(photoCodeKey("s03-501m10"), photoCodeKey("SB03/501MM"));
});

test("свой кадр артикула уходит из очереди при разных цветах и кроях", () => {
  assert.equal(find("B2", "sb03-501mm").length, 0);
  assert.equal(find("B2", "s03-501m10").length, 0);
});

test("файл другого артикула той же ячейки нигде не предлагается как выбор", () => {
  const byCell = new Map();
  for (const row of settled.cases) {
    const list = byCell.get(row.cell) ?? [];
    list.push(row);
    byCell.set(row.cell, list);
  }
  for (const [cell, rows] of byCell) {
    const keys = new Set(rows.map(row => photoCodeKey(row.code)));
    for (const row of rows) {
      const own = photoCodeKey(row.code);
      for (const name of names(row)) {
        const key = photoCodeKey(name);
        assert.equal(key === own || !keys.has(key), true, `${cell} ${row.code} ← ${name}`);
      }
    }
  }
  assert.equal(settled.cases.some(row => row.reason === "Выбрать подходящий кадр"), false);
});

test("однозначный кадр уходит из очереди, чужой похожий код остаётся без файла", () => {
  assert.equal(find("J34", "s23-48c2").length, 0);
  assert.equal(find("J34", "s23-48m10").length, 0);
  assert.equal(find("B183", "m02-464mb3").length, 0);
  assert.equal(find("B183", "m02-464b3+").length, 0);
  const withoutFile = find("B183", "m02-464c2+");
  assert.equal(withoutFile.length, 1);
  assert.deepEqual(withoutFile[0].photoIds, []);
  assert.equal(withoutFile[0].reason, "Нужен исходный кадр");
});

test("s23/37b2+ не числится без кадра: тот же артикул уже закрыт своим файлом", () => {
  assert.equal(find("B393", "s23/37b2+").length, 0);
  assert.equal(find("B404", "s23-37b2+").length, 0);
});

test("похожее, но другое имя файла не приклеивается и не выбрасывается", () => {
  const row = find("B139", "s03-508m")[0];
  assert.deepEqual(names(row), ["m03-508mb3.ARW"]);
  assert.equal(row.reason, "Уточнить вид изделия и крой");
  const missing = find("B85", "m02-454b3+");
  assert.equal(missing.length, 1);
  assert.equal(missing[0].reason, "Нужен исходный кадр");
});

test("повторный проход ничего не меняет", () => {
  assert.deepEqual(settlePhotoReview(settled), settled);
  assert.equal(settled.cases.length, 110);
  assert.equal(settled.unpublished.length, 59);
});
