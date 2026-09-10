import assert from "node:assert/strict";
import test from "node:test";
import { classifyHall, isSuitPieceForHall, normalizeHallSection } from "../dist/index.js";

test("hall sections ignore MoySklad numbering", () => {
  assert.equal(normalizeHallSection("1. Костюмы"), "suits");
  assert.equal(normalizeHallSection("2. Одежда"), "clothes");
  assert.equal(normalizeHallSection("3. Верхняя одежда"), "outerwear");
  assert.equal(normalizeHallSection("одежда"), "clothes");
});

test("suit jackets and suit trousers do not become loose clothes", () => {
  assert.equal(
    isSuitPieceForHall({ name: "Брюки (48/6)", category: "1. Костюмы", suitPart: "trousers" }),
    true
  );
  assert.equal(
    isSuitPieceForHall({ name: "Пиджак однобортный (50/6)", category: "1. Костюмы", suitPart: "jacket" }),
    true
  );
  assert.equal(
    isSuitPieceForHall({ name: "Слаксы (48)", category: "2. Одежда/Брюки", suitPart: "trousers" }),
    false
  );
});

test("shirts from the suits folder land in clothes", () => {
  const row = classifyHall({ name: "Сорочка классик (41, белый)", category: "1. Костюмы" });
  assert.deepEqual(row, { section: "clothes", group: "shirts" });
});

test("standalone trousers stay in clothes", () => {
  assert.deepEqual(classifyHall({ name: "Слаксы (50, синий)", category: "2. Одежда/Брюки", suitPart: "trousers" }), {
    section: "clothes",
    group: "trousers",
  });
  assert.deepEqual(classifyHall({ name: "Брюки палаццо (48)", category: "2. Одежда/Брюки", suitPart: "trousers" }), {
    section: "clothes",
    group: "trousers",
  });
  assert.equal(
    isSuitPieceForHall({ name: "Брюки (48/6)", category: "", suitPart: "trousers" }),
    true
  );
  assert.equal(
    isSuitPieceForHall({ name: "Брюки чинос (48)", category: "2. Одежда/Брюки", suitPart: "trousers" }),
    false
  );
});

test("outerwear splits by kind, quilted beats coat", () => {
  assert.deepEqual(classifyHall({ name: "Пальто однобортное с воротником стойка (50)" }), {
    section: "outerwear",
    group: "coat",
  });
  assert.deepEqual(classifyHall({ name: "Пальто стеганное с воротником стойка (50)" }), {
    section: "outerwear",
    group: "quilted",
  });
  assert.deepEqual(classifyHall({ name: "Тренч однобортный с погонами (48)" }), {
    section: "outerwear",
    group: "trench",
  });
  assert.deepEqual(classifyHall({ name: "Сафари хлопковое (50)", category: "3. Верхняя одежда" }), {
    section: "outerwear",
    group: "safari",
  });
});

test("safari jacket stays a suit piece, not outerwear", () => {
  assert.deepEqual(classifyHall({ name: "Пиджак сафари (50)", suitPart: "jacket" }), {
    section: "suits",
    group: "parts",
  });
});

test("accessories and shoes split by kind", () => {
  assert.deepEqual(classifyHall({ name: "Галстук узкий (чёрный)", category: "5. Аксессуары" }), {
    section: "accessories",
    group: "ties",
  });
  assert.deepEqual(classifyHall({ name: "Кепка-восьмиклинка", category: "5. Аксессуары" }), {
    section: "accessories",
    group: "caps",
  });
  assert.deepEqual(classifyHall({ name: "Платок нагрудный (белый)", category: "5. Аксессуары" }), {
    section: "accessories",
    group: "scarves",
  });
  assert.deepEqual(classifyHall({ name: "Запонки круглые", category: "5. Аксессуары" }), {
    section: "accessories",
    group: "cufflinks",
  });
  assert.deepEqual(classifyHall({ name: "Оксфорды брогированные (42)", category: "4. Обувь" }), {
    section: "shoes",
    group: "oxford",
  });
});

test("certificates are hidden from the hall", () => {
  assert.equal(classifyHall({ name: "Сертификат", category: "3. Верхняя одежда" }), null);
});
