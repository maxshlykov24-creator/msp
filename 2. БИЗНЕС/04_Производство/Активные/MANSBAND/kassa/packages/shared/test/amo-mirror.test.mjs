import assert from "node:assert/strict";
import test from "node:test";
import { isUnconductedAmoMirror, nextKassaNumber, visibleLocalDeals } from "../dist/index.js";

test("номер кассы идёт следом за кассой и не прыгает на id amo", () => {
  assert.equal(nextKassaNumber(57642837), 57642838);
  assert.notEqual(nextKassaNumber(57642837), 62161964);
  assert.throws(() => nextKassaNumber(62_000_000.5), /Некорректный номер кассы/);
});

test("непроведённое зеркало на успехе не входит в выручку", () => {
  const deals = [
    { id: "amo-61703360", stage: "Успех", total: 4500, paid: 0, payments: [], kind: "sale" },
    { id: "kassa-1", stage: "Успех", total: 1000, paid: 1000, payments: [{ amount: 1000 }], kind: "sale" },
    { id: "amo-paid", stage: "Успех", total: 200, paid: 200, payments: [{ amount: 200 }], kind: "sale" },
    { id: "amo-doc", stage: "Успех", total: 300, paid: 0, payments: [], msOrderId: "ms-1", kind: "sale" },
  ];
  const revenue = deals
    .filter(deal => deal.stage === "Успех" && !isUnconductedAmoMirror(deal))
    .reduce((sum, deal) => sum + deal.total, 0);
  assert.equal(revenue, 1500);
  assert.equal(isUnconductedAmoMirror(deals[0]), true);
  assert.equal(isUnconductedAmoMirror(deals[2]), false);
  assert.equal(isUnconductedAmoMirror(deals[3]), false);
});

test("проведённая заявка закрывает зеркало, позиции и оплаты остаются", () => {
  const mirror = { id: "amo-9", amoLeadId: 9, items: [], payments: [], total: 4500 };
  const real = {
    id: "deal-9",
    amoLeadId: 9,
    items: [{ productId: "suit", qty: 1 }],
    payments: [{ amount: 4500 }],
    total: 4500,
  };
  const visible = visibleLocalDeals([mirror, real]);
  assert.equal(visible.length, 1);
  assert.equal(visible[0].id, "deal-9");
  assert.deepEqual(visible[0].items, real.items);
  assert.deepEqual(visible[0].payments, real.payments);
});
