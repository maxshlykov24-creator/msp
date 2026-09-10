import assert from "node:assert/strict";
import test from "node:test";
import {
  computeCompleteness,
  countSuitsInLines,
  isHalfSetWarehouse,
  suitFamilyOf,
  suitLineOf,
  suitPartOf,
  suitTitle,
} from "../dist/index.js";

// Костюм в кассе — вычисление по характеристике «Вариация»: комплектов
// МойСклада нет, поэтому проверяем именно расчёт комплектности.

function line(part, size, qty, extra = {}) {
  return {
    msId: `${part}-${size}-${extra.warehouse ?? "novo"}`,
    variation: "S23/33S3",
    size,
    height: "6",
    color: "чёрный",
    pattern: "однотонный",
    fit: "slim fit",
    part,
    line: "regular",
    warehouse: extra.warehouse ?? "На Новокузнецкой",
    qty,
  };
}

test("suit part and line are read from the product kind name", () => {
  assert.equal(suitPartOf("Пиджак однобортный (48/6, чёрный)"), "jacket");
  assert.equal(suitPartOf("Блейзер смокинг (50/6)"), "jacket");
  assert.equal(suitPartOf("Брюки классические (48/6)"), "trousers");
  assert.equal(suitPartOf("Слаксы (48)"), "trousers");
  assert.equal(suitPartOf("Жилет (48/6)"), "vest");
  // Не костюмный вид в комплектность не попадает.
  assert.equal(suitPartOf("Рубашка (41)"), null);
  assert.equal(suitLineOf("Пиджак смокинг двубортный"), "smoking");
  assert.equal(suitLineOf("Пиджак однобортный"), "regular");
});

test("suit title reads like the consultant says it", () => {
  assert.equal(
    suitTitle({ hasVest: true, line: "regular", color: "чёрный", pattern: "однотонный", fit: "slim fit" }),
    "Костюм тройка чёрный однотонный slim fit"
  );
  assert.equal(suitTitle({ hasVest: false, line: "smoking" }), "Смокинг двойка");
});

test("suit family splits doubles, triples and smokings", () => {
  assert.equal(suitFamilyOf({ line: "regular", composition: ["jacket", "trousers"] }), "double");
  assert.equal(suitFamilyOf({ line: "regular", composition: ["jacket", "trousers", "vest"] }), "triple");
  assert.equal(suitFamilyOf({ line: "smoking", composition: ["jacket", "trousers"] }), "smoking");
  assert.equal(suitFamilyOf({ line: "smoking", composition: ["jacket", "trousers", "vest"] }), "smoking");
});

test("half-set warehouses are recognised by name", () => {
  assert.equal(isHalfSetWarehouse("Полупарки на Бауманской"), true);
  assert.equal(isHalfSetWarehouse("На Бауманской"), false);
});

test("whole suits are the minimum across the composition at one size", () => {
  const res = computeCompleteness(
    [line("jacket", "48", 2), line("trousers", "48", 3), line("vest", "48", 2)],
    { tolerance: 0 }
  );
  assert.equal(res.totals.models, 1);
  assert.equal(res.totals.whole, 2);
  // Лишние брюки остаются без пары: это неправильный полупарк.
  assert.equal(res.totals.orphans, 1);
  const model = res.models[0];
  assert.deepEqual(model.composition, ["jacket", "trousers", "vest"]);
  assert.equal(model.title, "Костюм тройка чёрный однотонный slim fit");
});

test("size tolerance turns a mismatch into a sellable suit", () => {
  const lines = [line("jacket", "48", 1), line("trousers", "50", 1)];
  const strict = computeCompleteness(lines, { tolerance: 0 });
  assert.equal(strict.totals.whole, 0);
  assert.equal(strict.totals.tolerant, 0);
  assert.equal(strict.totals.orphans, 2);

  const tolerant = computeCompleteness(lines, { tolerance: 2 });
  assert.equal(tolerant.totals.whole, 0);
  assert.equal(tolerant.totals.tolerant, 1);
  assert.equal(tolerant.totals.orphans, 0);
});

test("orphan row says what is missing and where the pair lies", () => {
  const res = computeCompleteness(
    [line("jacket", "48", 1), line("trousers", "48", 1, { warehouse: "Центральный склад" })],
    { tolerance: 0, warehouse: "На Новокузнецкой" }
  );
  // По складу магазина цельного костюма нет: брюки лежат на центральном.
  assert.equal(res.totals.whole, 0);
  const orphan = res.models[0].sizes[0].orphans[0];
  assert.equal(orphan.part, "jacket");
  assert.deepEqual(orphan.missing, ["trousers"]);
});

test("physical half-set stock is counted apart from computed orphans", () => {
  const res = computeCompleteness(
    [
      line("jacket", "48", 1),
      line("trousers", "48", 1),
      line("jacket", "52", 2, { warehouse: "Полупарки на Новокузнецкой" }),
    ],
    { tolerance: 0 }
  );
  assert.equal(res.totals.whole, 1);
  assert.equal(res.totals.orphans, 0);
  // Склад полупарков не смешивается с расчётом, иначе изделие попало бы дважды.
  assert.equal(res.models[0].onHalfSetWarehouse, 2);
});

test("a three-piece sale is one suit and one unit, not three", () => {
  const sold = countSuitsInLines([
    { qty: 1, part: "jacket", variation: "S23/33S3" },
    { qty: 1, part: "trousers", variation: "S23/33S3" },
    { qty: 1, part: "vest", variation: "S23/33S3" },
  ]);
  assert.equal(sold.suits, 1);
  assert.equal(sold.units, 1);
});

test("suit counts even when the sizes differ, but a lone jacket does not", () => {
  // Размеры верха и низа могут расходиться — костюм всё равно продан.
  const mixedSizes = countSuitsInLines([
    { qty: 1, part: "jacket", variation: "S23/33S3" },
    { qty: 1, part: "trousers", variation: "S23/33S3" },
  ]);
  assert.equal(mixedSizes.suits, 1);

  const jacketOnly = countSuitsInLines([{ qty: 1, part: "jacket", variation: "S23/33S3" }]);
  assert.equal(jacketOnly.suits, 0);
  assert.equal(jacketOnly.units, 1);
});

test("two suits in one check give two suits, leftovers stay separate units", () => {
  const sold = countSuitsInLines([
    { qty: 2, part: "jacket", variation: "S23/33S3" },
    { qty: 2, part: "trousers", variation: "S23/33S3" },
    { qty: 1, part: "vest", variation: "S23/33S3" },
    { qty: 1, part: "trousers", variation: "M03/636S" },
    { qty: 1, part: null, variation: null }, // рубашка
  ]);
  assert.equal(sold.suits, 2);
  // Два костюма плюс одиночные брюки плюс рубашка.
  assert.equal(sold.units, 4);
});

test("parts of different models do not glue into a suit", () => {
  const sold = countSuitsInLines([
    { qty: 1, part: "jacket", variation: "S23/33S3" },
    { qty: 1, part: "trousers", variation: "M03/636S" },
  ]);
  assert.equal(sold.suits, 0);
  assert.equal(sold.units, 2);
});

test("a model without a jacket is standalone trousers, not a broken suit", () => {
  const res = computeCompleteness([line("trousers", "48", 5)], { tolerance: 0 });
  assert.equal(res.totals.models, 0);
});
