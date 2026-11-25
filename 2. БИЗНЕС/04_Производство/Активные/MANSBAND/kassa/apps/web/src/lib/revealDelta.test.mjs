import assert from "node:assert/strict";
import test from "node:test";
import { revealDelta } from "./revealDelta.mjs";

const zone = { top: 12, bottom: 600 };

test("строка под панелью остаётся вместе с поиском, если они помещаются", () => {
  const { delta, companionKept } = revealDelta({
    zone,
    keep: { top: 620, bottom: 740 },
    companion: { top: 560, bottom: 610 },
  });
  assert.equal(companionKept, true);
  assert.equal(delta, 140);
});

test("длинный список оставляет последнюю строку и отпускает поиск", () => {
  const { delta, companionKept } = revealDelta({
    zone,
    keep: { top: 700, bottom: 820 },
    companion: { top: 40, bottom: 90 },
  });
  assert.equal(companionKept, false);
  assert.equal(delta, 220);
});

test("сдача открывается сверху и не вытесняет строку оплаты", () => {
  const roomy = revealDelta({
    zone,
    keep: { top: 400, bottom: 460 },
    show: { top: 640, bottom: 1200 },
  });
  assert.equal(roomy.delta, 136);

  const tight = revealDelta({
    zone,
    keep: { top: 30, bottom: 90 },
    show: { top: 700, bottom: 1400 },
  });
  assert.equal(tight.delta, 18);
});

test("строка выше экрана поднимается к себе", () => {
  const { delta } = revealDelta({
    zone,
    keep: { top: -200, bottom: -80 },
  });
  assert.equal(delta, -212);
});

test("сдвиг меньше 12px не выполняется", () => {
  const { delta } = revealDelta({
    zone,
    keep: { top: 500, bottom: 608 },
  });
  assert.equal(delta, 0);
});

test("блок выше зоны прижимается верхом", () => {
  const { delta, companionKept } = revealDelta({
    zone,
    keep: { top: 100, bottom: 900 },
    companion: { top: 40, bottom: 80 },
  });
  assert.equal(delta, 88);
  assert.equal(companionKept, false);
});

test("после номера поиск остаётся над панелью с запасом, телефон не уезжает", () => {
  const roomy = revealDelta({
    zone,
    keep: { top: 420, bottom: 490 },
    show: { top: 640, bottom: 690 },
    cushion: 168,
  });
  assert.equal(roomy.delta, 258);

  const tight = revealDelta({
    zone,
    keep: { top: 30, bottom: 100 },
    show: { top: 640, bottom: 690 },
    cushion: 168,
  });
  assert.equal(tight.delta, 18);
});

test("уже видимое поле и верх высокого блока ниже панели не едут к низу блока", () => {
  const still = revealDelta({
    zone,
    keep: { top: 100, bottom: 200 },
  });
  assert.equal(still.delta, 0);

  const peek = revealDelta({
    zone,
    keep: { top: 100, bottom: 160 },
    show: { top: 500, bottom: 2000 },
  });
  assert.equal(peek.delta, 0);
});
