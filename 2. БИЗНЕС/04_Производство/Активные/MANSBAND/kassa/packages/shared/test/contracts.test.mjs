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
  isDealOverdue,
  isDealRelevantToUser,
} from "../dist/index.js";

test("overdue remains until closed or extended; today's deadline is not overdue", () => {
  const rental = { kind: "rental", stage: "Аренда оплачена", rentalTo: "2026-10-23" };
  assert.equal(isDealOverdue(rental, "2026-10-24"), true);
  assert.equal(isDealOverdue({ ...rental, stage: "Аренда просрочена" }, "2026-10-24"), true);
  assert.equal(isDealOverdue({ ...rental, rentalTo: "2026-10-24" }, "2026-10-24"), false);
  for (const stage of ["Успех", "Провал", "Не слив"]) {
    assert.equal(isDealOverdue({ ...rental, stage }, "2026-10-24"), false);
  }
});

test("overdue responsibility follows consultant, call manager and pending task role/store", () => {
  const deal = { number: 100, consultant: "Анна", callManager: "Нина" };
  const user = { name: "Иван", role: "consultant", store: "На Бауманской" };
  const task = { dealNumber: 100, assigneeRole: "consultant", store: "На Бауманской", status: "pending" };
  assert.equal(isDealRelevantToUser(deal, { ...user, name: "Анна" }, []), true);
  assert.equal(isDealRelevantToUser(deal, { ...user, name: "Нина", role: "crm" }, []), true);
  assert.equal(isDealRelevantToUser(deal, user, []), false);
  assert.equal(isDealRelevantToUser(deal, user, [task]), true);
  assert.equal(isDealRelevantToUser(deal, user, [{ ...task, status: "done" }]), false);
  assert.equal(isDealRelevantToUser(deal, user, [{ ...task, store: "На Пятницкой" }]), false);
  assert.equal(isDealRelevantToUser(deal, { ...user, role: "logist" }, [task]), false);
  assert.equal(isDealRelevantToUser(deal, { ...user, role: "logist" }, [{ ...task, assigneeRole: "logist" }]), true);
  assert.equal(isDealRelevantToUser(deal, { ...user, role: "admin" }, []), true);
  assert.equal(isDealRelevantToUser(deal, null, []), false);
});

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

// Сквозная проверка реальных сервисов, внешние CRM/МС и SQL заменены памятью.
// Проверяем назначение, переходы и повтор запроса без документов в рабочем МС.
test("promise-to-reserve logistics preserves kind, source queue and idempotency", async () => {
  const { readFileSync } = await import("node:fs");
  const { createRequire } = await import("node:module");
  const { runInNewContext } = await import("node:vm");
  const apiRequire = createRequire(new URL("../../../apps/api/package.json", import.meta.url));
  const ts = apiRequire("typescript");
  const shared = await import("../dist/index.js");
  for (const kind of ["sale", "company", "rental"]) {
    for (const sourceName of [shared.CENTRAL_WAREHOUSE, "На Новокузнецкой"]) {
      const table = (name, columns) => Object.assign({ table: name }, Object.fromEntries(columns.map((c) => [c, c])));
      const schema = {
        tasks: table("tasks", ["id", "kind", "dealNumber", "status", "idempotencyKey", "assigneeRole", "store", "createdAt"]),
        msRefs: table("refs", ["kind"]),
        dealItemState: table("items", ["dealNumber", "itemId"]),
        deals: table("deals", ["number", "stage", "data"]),
      };
      const state = { tasks: [], items: [], deals: [], refs: [
        { kind: "store", msId: "source", name: sourceName, payload: { name: sourceName, meta: {} } },
        { kind: "store", msId: "target", name: "На Бауманской", payload: { name: "На Бауманской", meta: {} } },
      ] };
      let serial = 0, moves = 0;
      const deal = { ...baseDeal, id: `flow-${kind}`, number: 9001, kind, stage: "Дано обещание", companyName: "Тест", actualUntil: "2026-11-07", comment: "Привезти размер", items: [{ productId: "product", name: "Комплект", qty: 1, price: 100 }], total: 100, paid: 0 };
      const query = (operation, fields) => {
        let target, predicate = () => true, values, patch, limit = Infinity, conflict, cached;
        const q = {
          from(t) { target = t.table; return q; }, where(p) { predicate = p || (() => true); return q; },
          limit(n) { limit = n; return q; }, orderBy() { return q; },
          values(v) { values = v; return q; }, set(v) { patch = v; return q; },
          onConflictDoNothing() { conflict = {}; return q; }, onConflictDoUpdate(v) { conflict = v; return q; },
          returning(f) { fields = f; return q; },
          catch(reject) { return q.then(undefined, reject); },
          then(resolve, reject) {
            if (!cached) cached = Promise.resolve().then(() => {
              let rows;
              if (operation === "select") rows = state[target].filter(predicate).slice(0, limit);
              if (operation === "update") { rows = state[target].filter(predicate); rows.forEach((r) => Object.assign(r, patch)); }
              if (operation === "insert") {
                const existing = state[target].find((r) => values.idempotencyKey ? r.idempotencyKey === values.idempotencyKey : conflict?.target && conflict.target.every((c) => r[c] === values[c]));
                if (existing) { if (conflict?.set) Object.assign(existing, conflict.set); rows = []; }
                else { const row = { id: String(++serial), status: "pending", createdAt: new Date(), ...values }; state[target].push(row); rows = [row]; }
              }
              return fields ? rows.map((r) => Object.fromEntries(Object.entries(fields).map(([key, col]) => [key, r[col]]))) : rows;
            });
            return cached.then(resolve, reject);
          },
        };
        return q;
      };
      const db = {
        select: (fields) => query("select", fields), insert: (t) => query("insert").from(t), update: (t) => query("update").from(t),
        transaction: (fn) => fn(db), execute: async () => {},
      };
      const orm = { eq: (c, v) => (r) => r[c] === v, ne: (c, v) => (r) => r[c] !== v, inArray: (c, vs) => (r) => vs.includes(r[c]), and: (...ps) => (r) => ps.every((p) => p(r)), desc: (v) => v, sql: () => () => true };
      const services = {};
      const dealService = {
        getByNumber: async () => deal,
        updateStage: async (_ref, stage) => {
          if (stage === "Товар отложен" && stage !== deal.stage) deal.reservedUntil = shared.defaultHoldUntil(deal.paid);
          deal.stage = stage; return { ...deal };
        },
      };
      const dependencies = {
        "@kassa/shared": shared, "drizzle-orm": orm, "../db/index.js": { db }, "../db/schema.js": schema,
        "./deals.js": dealService, "./audit.js": { appendSystemAudit: async () => {} }, "./queue.js": {},
        "./sale.js": { enqueueCompanyChain: async () => {} },
        "../clients/ms.js": { createMove: async () => { moves++; return { id: "test-move" }; } },
        "./bootstrap.js": { getMsRef: async () => ({ meta: {} }) },
        "./catalog.js": { resolveAssortment: async () => ({ href: "test", type: "variant" }), productNamesByMsIds: async () => new Map(), productCodesByMsIds: async () => new Map() },
      };
      for (const name of ["taskFlow", "tasks"]) {
        const filename = new URL(`../../../apps/api/src/services/${name}.ts`, import.meta.url);
        const code = ts.transpileModule(readFileSync(filename, "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
        const module = { exports: {} };
        runInNewContext(code, { module, exports: module.exports, require: (id) => {
          if (id === "./taskFlow.js") return services.taskFlow;
          if (!(id in dependencies)) throw new Error(`Unmocked dependency ${id}`);
          return dependencies[id];
        }, Date, console });
        services[name] = module.exports;
      }
      await services.taskFlow.onDealStage(deal, "Тест");
      assert.equal(state.tasks.length, 0, "promise does not dispatch logistics");
      deal.stage = "Ждет товар";
      await services.taskFlow.onDealStage(deal, "Тест");
      await services.taskFlow.onDealStage(deal, "Тест");
      assert.equal(state.tasks.length, 1, "one setup reminder");
      assert.equal(state.tasks[0].assigneeRole, "consultant");
      const input = { idempotencyKey: `move-${kind}-${sourceName}`, dealNumber: deal.number, fromStoreMsId: "source", toStoreMsId: "target", positions: [{ productId: "product", quantity: 1 }], store: "Неверный магазин", assigneeRole: "crm", title: "Перемещение" };
      const task = await services.tasks.createMovement(input, "Тест");
      await services.tasks.createMovement(input, "Тест");
      assert.equal(state.tasks.length, 1, "setup reused; retry doesn't duplicate");
      assert.equal(task.assigneeRole, sourceName === shared.CENTRAL_WAREHOUSE ? "logist" : "consultant");
      assert.equal(task.store, shared.storeForAcceptQueue(sourceName));
      await services.tasks.completeTask(task.id, "Тест");
      assert.equal(deal.stage, "Товар в пути");
      await services.tasks.createMovement(input, "Тест");
      assert.equal(deal.stage, "Товар в пути", "retry of dispatched movement must not reset stage");
      const acceptance = state.tasks.find((t) => t.kind === "movement_accept");
      assert.equal(acceptance.store, "На Бауманской");
      assert.equal(acceptance.assigneeRole, "consultant");
      await services.tasks.completeTask(acceptance.id, "Тест");
      assert.equal(deal.stage, "Товар в магазине");
      const reserve = state.tasks.find((t) => t.kind === "reserve");
      assert.ok(reserve, "acceptance creates reserve task");
      await services.tasks.completeTask(reserve.id, "Тест");
      assert.equal(deal.stage, "Товар отложен");
      assert.equal(deal.reservedUntil, shared.defaultHoldUntil(0));
      assert.equal(deal.kind, kind);
      await services.tasks.completeTask(acceptance.id, "Тест");
      assert.equal(moves, 1, "repeated acceptance doesn't create another movement");
    }
  }
});

test("hold stages require promise details and reserve dates without premature rental payment", async () => {
  const { holdStageError, defaultHoldUntil, isDealOverdue } = await import("../dist/index.js");
  for (const kind of ["sale", "company", "rental"]) {
    const promise = { ...baseDeal, kind, stage: "Дано обещание", companyName: "Тест", items: [{ productId: "p", name: "Товар", price: 100, qty: 1 }], total: 100, comment: "Привезти товар", actualUntil: "2026-11-07" };
    assert.equal(createDealSchema.safeParse(promise).success, true);
    assert.ok(holdStageError({ ...promise, comment: "" }));
    assert.ok(holdStageError({ ...promise, actualUntil: "" }));
    assert.ok(holdStageError({ ...promise, stage: "Товар отложен" }));
    assert.equal(holdStageError({ ...promise, stage: "Товар отложен", reservedUntil: "2026-11-08" }), null);
    assert.equal(isDealOverdue({ ...promise, stage: "Ждет товар", actualUntil: "2026-10-01" }, "2026-10-24"), false);
  }
  assert.equal(defaultHoldUntil(0, "2026-10-24"), "2026-10-25");
  assert.equal(defaultHoldUntil(100, "2026-10-24"), "2026-11-07");
});
