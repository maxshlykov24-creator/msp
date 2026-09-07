import assert from "node:assert/strict";
import test from "node:test";
import {
  catalogSearchSchema,
  createDealSchema,
  EDWIN_EXPENSE_CATEGORIES,
  expenseSchema,
  MANSBAND_PAYOUT_METHOD,
  mansbandPayoutAmount,
  movementTaskSchema,
  normalizePayouts,
  selfPayouts,
} from "../dist/index.js";

const baseDeal = {
  id: "request-123",
  funnel: "offline",
  kind: "sale",
  consultant: "Иван",
  clientName: "Клиент",
  clientPhone: "",
  store: "На Бауманской",
  items: [],
  payments: [],
  stage: "Новая заявка",
  total: 0,
  paid: 0,
};

test("POST /deals contract accepts legacy web payload", () => {
  assert.equal(createDealSchema.safeParse(baseDeal).success, true);
});

test("meeting stage requires date", () => {
  const parsed = createDealSchema.safeParse({ ...baseDeal, stage: "Встреча назначена" });
  assert.equal(parsed.success, false);
});

test("company success requires issued flag", () => {
  const parsed = createDealSchema.safeParse({ ...baseDeal, kind: "company", stage: "Успех" });
  assert.equal(parsed.success, false);
});

test("company success accepts paid and issued invoice", () => {
  const parsed = createDealSchema.safeParse({
    ...baseDeal,
    kind: "company",
    stage: "Успех",
    invoiceNo: "СЧ-1",
    invoiceDate: "2026-07-22",
    issued: true,
  });
  assert.equal(parsed.success, true);
});

test("rental success requires payment and issue/return facts", () => {
  assert.equal(createDealSchema.safeParse({ ...baseDeal, kind: "rental", stage: "Успех" }).success, false);
  assert.equal(createDealSchema.safeParse({
    ...baseDeal,
    kind: "rental",
    stage: "Успех",
    rentalFrom: "2026-07-22",
    rentalTo: "2026-07-24",
    rentalIssuedAt: "2026-07-22T00:00:00.000Z",
    rentalReturnedAt: "2026-07-24T00:00:00.000Z",
  }).success, true);
});

test("digital certificate number is server-assigned; plastic number is manual", () => {
  assert.equal(createDealSchema.safeParse({
    ...baseDeal,
    kind: "cert_digital",
    stage: "Сертификат оплачен",
  }).success, true);
  assert.equal(createDealSchema.safeParse({
    ...baseDeal,
    kind: "cert_plastic",
    stage: "Сертификат оплачен",
  }).success, false);
});

test("Edwin expense dictionary matches approved fixed order", () => {
  assert.deepEqual(EDWIN_EXPENSE_CATEGORIES, [
    "Возврат", "Сдача", "Чаевые", "Банковское обслуживание", "Логистика",
    "Закупка товара", "Хоз товары, канцелярия", "Зарплата", "Оплата подрядчикам",
    "Таргет", "Съемки", "Онлайн сервисы", "Ателье и ремонт изделий",
    "Оснащение магазина", "Аренда помещений", "Налоги",
    // Отправленные САР пишутся сюда автоматически (созвон 20.08).
    "Программа лояльности",
    "Прочее",
  ]);
  assert.equal(expenseSchema.safeParse({
    category: "СДЭК",
    account: "Наличные",
    amount: 1,
  }).success, false);
});

test("catalog can filter by Moysklad group without text query", () => {
  assert.equal(catalogSearchSchema.safeParse({ category: "Костюмы" }).success, true);
});

test("movement requires stable idempotency key", () => {
  const movement = {
    kind: "movement",
    fromStoreMsId: "from",
    toStoreMsId: "to",
    positions: [{ productId: "product", quantity: 1 }],
    assigneeRole: "logist",
    title: "Переместить товар",
  };
  assert.equal(movementTaskSchema.safeParse(movement).success, false);
  assert.equal(movementTaskSchema.safeParse({ ...movement, idempotencyKey: "movement-123" }).success, true);
});

test("payout rows always add up to the full payout", () => {
  // Пустой список даёт одну строку на всю сумму с пустым способом: в форме
  // заявки консультант обязан выбрать его сам, дефолта здесь нет.
  assert.deepEqual(normalizePayouts([], 1500), [{ methodId: "", amount: 1500 }]);
  assert.deepEqual(normalizePayouts([], 1500, "cash_zhenya"), [
    { methodId: "cash_zhenya", amount: 1500 },
  ]);
  // Последняя строка добирает остаток — «повисших» денег быть не может.
  assert.deepEqual(
    normalizePayouts(
      [
        { methodId: "cash_zhenya", amount: 500 },
        { methodId: MANSBAND_PAYOUT_METHOD, amount: 0 },
      ],
      1500
    ),
    [
      { methodId: "cash_zhenya", amount: 500 },
      { methodId: MANSBAND_PAYOUT_METHOD, amount: 1000 },
    ]
  );
  // Перебор в первой строке урезается, чтобы сумма не превысила выплату.
  const clamped = normalizePayouts(
    [
      { methodId: "cash_zhenya", amount: 5000 },
      { methodId: "sber_zhenya", amount: 300 },
    ],
    1000
  );
  assert.equal(clamped.reduce((sum, row) => sum + row.amount, 0), 1000);
  assert.equal(normalizePayouts([{ methodId: "cash_zhenya", amount: 100 }], 0).length, 0);
});

test("only mansband rows go to Edwin queue", () => {
  const rows = [
    { methodId: "cash_zhenya", amount: 400 },
    { methodId: MANSBAND_PAYOUT_METHOD, amount: 600 },
  ];
  assert.equal(mansbandPayoutAmount(rows), 600);
  assert.deepEqual(selfPayouts(rows), [{ methodId: "cash_zhenya", amount: 400 }]);
});
