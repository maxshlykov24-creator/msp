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
  isObsoleteStageTask,
  isDealRelevantToUser,
  isReturnSource,
  returnableItems,
  recalculateDealTotals,
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
  assert.equal(createDealSchema.safeParse({ ...baseDeal, stage: "Встреча назначена", meetingDate: "2026-11-03" }).success, true);
});

test("want-to-come stages require approximate date", () => {
  assert.equal(createDealSchema.safeParse({ ...baseDeal, stage: "Хочет прийти" }).success, false);
  assert.equal(createDealSchema.safeParse({ ...baseDeal, stage: "Хочет заказать" }).success, false);
  assert.equal(
    createDealSchema.safeParse({ ...baseDeal, stage: "Хочет прийти", approxMeetingDate: "2026-11-02" }).success,
    true
  );
});

test("company success requires issued flag", () => {
  const parsed = createDealSchema.safeParse({ ...baseDeal, kind: "company", stage: "Успех" });
  assert.equal(parsed.success, false);
});

test("company success accepts paid and issued invoice", () => {
  const parsed = createDealSchema.safeParse({
    ...baseDeal,
    clientPhone: "+79990000000", channel: "Сайт", purpose: "Работа",
    items: [{ productId: "a", name: "A", price: 100, qty: 1 }],
    payments: [{ id: "p", methodId: "cash", amount: 100 }], total: 100, paid: 100,
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
  for (const kind of shared.HOLD_DEAL_KINDS) {
    for (const sourceName of [shared.CENTRAL_WAREHOUSE, "На Новокузнецкой"]) {
      const table = (name, columns) => Object.assign({ table: name }, Object.fromEntries(columns.map((c) => [c, c])));
      const schema = {
        tasks: table("tasks", ["id", "kind", "dealNumber", "status", "idempotencyKey", "assigneeRole", "store", "createdAt", "metadata"]),
        users: table("users", ["name", "role"]),
        msRefs: table("refs", ["kind"]),
        dealItemState: table("items", ["dealNumber", "itemId"]),
        deals: table("deals", ["number", "stage", "data"]),
      };
      const state = { tasks: [], users: [{ name: "Тест", role: "crm" }], items: [], deals: [], refs: [
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
      const orm = { eq: (c, v) => (r) => r[c] === v, ne: (c, v) => (r) => r[c] !== v, inArray: (c, vs) => (r) => vs.includes(r[c]), and: (...ps) => (r) => ps.every((p) => p(r)), desc: (v) => v, sql: (_strings, column, value) => (r) => String(r[column] ?? "").toLowerCase() === value };
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
      assert.equal(state.tasks.filter((t) => t.kind !== "promise_follow").length, 0, "promise does not dispatch logistics");
      const promiseTask = state.tasks.find((t) => t.kind === "promise_follow");
      assert.equal(promiseTask?.title, "Привезти размер");
      assert.equal(promiseTask?.metadata?.dueDate, "2026-11-07");
      assert.equal(promiseTask?.metadata?.assigneeName, "Тест");
      assert.equal(promiseTask?.createdBy, "Тест", "author is the logged-in actor, not the deal consultant");
      assert.equal(promiseTask?.assigneeRole, "crm", "queue follows the promise creator's role");
      await services.taskFlow.onDealStage(deal, "amoCRM");
      await services.taskFlow.onDealStage(deal, "Другой сотрудник");
      assert.equal(state.tasks.filter((t) => t.kind === "promise_follow").length, 1);
      assert.equal(promiseTask.metadata.assigneeName, "Тест", "CRM echo and edits preserve responsibility");
      assert.equal(promiseTask.createdBy, "Тест");
      assert.equal(promiseTask.assigneeRole, "crm");
      deal.stage = "Ждет товар";
      await services.taskFlow.onDealStage(deal, "Тест");
      await services.taskFlow.onDealStage(deal, "Тест");
      assert.equal(state.tasks.filter((t) => t.kind === "movement").length, 1, "one setup reminder");
      assert.equal(state.tasks.find((t) => t.kind === "movement").assigneeRole, "consultant");
      const input = { idempotencyKey: `move-${kind}-${sourceName}`, dealNumber: deal.number, fromStoreMsId: "source", toStoreMsId: "target", positions: [{ productId: "product", quantity: 1 }], store: "Неверный магазин", assigneeRole: "crm", title: "Перемещение" };
      const task = await services.tasks.createMovement(input, "Тест");
      await services.tasks.createMovement(input, "Тест");
      assert.equal(state.tasks.filter((t) => t.kind === "movement").length, 1, "setup reused; retry doesn't duplicate");
      assert.equal(task.assigneeRole, sourceName === shared.CENTRAL_WAREHOUSE ? "logist" : "consultant");
      assert.equal(task.store, shared.storeForAcceptQueue(sourceName));
      const second = await services.tasks.createMovement({ ...input, idempotencyKey: `${input.idempotencyKey}-second`, positions: [{ productId: "product-2", quantity: 1 }] }, "Тест");
      await services.tasks.completeTask(task.id, "Тест");
      assert.equal(deal.stage, "Товар в пути");
      await services.tasks.createMovement(input, "Тест");
      assert.equal(deal.stage, "Товар в пути", "retry of dispatched movement must not reset stage");
      const acceptance = state.tasks.find((t) => t.kind === "movement_accept");
      assert.equal(acceptance.store, "На Бауманской");
      assert.equal(acceptance.assigneeRole, "consultant");
      await services.tasks.completeTask(acceptance.id, "Тест");
      assert.equal(deal.stage, "Товар в пути", "first acceptance waits for remaining shipment");
      assert.equal(state.tasks.some((t) => t.kind === "reserve"), false);
      await services.tasks.completeTask(second.id, "Тест");
      const secondAcceptance = state.tasks.find((t) => t.kind === "movement_accept" && t.id !== acceptance.id);
      await services.tasks.completeTask(secondAcceptance.id, "Тест");
      assert.equal(deal.stage, "Товар в магазине");
      const reserve = state.tasks.find((t) => t.kind === "reserve");
      assert.ok(reserve, "acceptance creates reserve task");
      deal.paid = 2999;
      await assert.rejects(() => services.tasks.completeTask(reserve.id, "Тест"), /3 000/);
      assert.equal(reserve.status, "pending", "insufficient payment must not close the reserve task");
      deal.paid = 0;
      await services.tasks.completeTask(reserve.id, "Тест");
      assert.equal(deal.stage, "Товар отложен");
      assert.equal(deal.reservedUntil, shared.defaultHoldUntil(0));
      await services.taskFlow.cancelOnKindConvert(deal.number);
      assert.equal(promiseTask.status, "cancelled", "conversion to a non-hold stage retires the promise task");
      assert.equal(deal.kind, kind);
      await services.tasks.completeTask(acceptance.id, "Тест");
      assert.equal(moves, 2, "repeated acceptance doesn't create another movement");
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
  assert.equal(defaultHoldUntil(3000, "2026-10-24"), "2026-11-07");
  const { isPartialPayment, reserveUntilForSave } = await import("../dist/index.js");
  assert.equal(isPartialPayment(3000, 34900), true);
  assert.equal(isPartialPayment(34900, 34900), false);
  assert.equal(reserveUntilForSave({ paid: 3000, total: 34900, stage: "Товар в магазине", today: "2026-10-26" }), "2026-11-09");
  assert.equal(reserveUntilForSave({ paid: 0, total: 34900, stage: "Товар в магазине", today: "2026-10-26" }), "2026-10-27");
  assert.equal(reserveUntilForSave({ paid: 0, total: 34900, stage: "Успех", today: "2026-10-26" }), undefined);
  assert.equal(reserveUntilForSave({ paid: 3000, total: 34900, stage: "Товар в магазине", chosen: "2026-12-01", today: "2026-10-26" }), "2026-12-01");
  const { amoOtlozhka } = await import("../dist/index.js");
  assert.deepEqual(
    amoOtlozhka({ paid: 3000, total: 34900, stage: "Товар в магазине", reservedUntil: "2026-11-09" }),
    { kind: "Платная", until: "2026-11-09" }
  );
  assert.deepEqual(
    amoOtlozhka({ paid: 3000, total: 34900, stage: "Встреча назначена", today: "2026-10-26" }),
    { kind: "Платная", until: "2026-11-09" }
  );
  assert.deepEqual(
    amoOtlozhka({ paid: 0, total: 34900, stage: "Товар отложен", today: "2026-10-26" }),
    { kind: "Бесплатная", until: "2026-10-27" }
  );
  assert.deepEqual(
    amoOtlozhka({ paid: 34900, total: 34900, stage: "Товар в магазине", reservedUntil: "2026-11-09" }),
    { kind: null, until: "2026-11-09" }
  );
});


test("hold payment threshold is shared by dates, CRM and validation", async () => {
  const { amoOtlozhka, defaultHoldUntil, defaultPromiseUntil, holdStageError, holdPaymentError, reserveUntilForSave, HOLD_DEAL_KINDS } = await import("../dist/index.js");
  const today = "2026-11-03";
  assert.equal(defaultPromiseUntil(today), "2026-11-17", "promise default remains two weeks");
  for (const paid of [1, 100, 2999]) {
    assert.match(holdPaymentError(paid), /3 000/);
    assert.deepEqual(amoOtlozhka({ paid, total: 34900, stage: "Товар отложен", today }), { kind: null, until: null });
    assert.equal(reserveUntilForSave({ paid, total: 34900, stage: "Товар отложен", today }), undefined);
    for (const kind of HOLD_DEAL_KINDS) {
      assert.match(holdStageError({ ...baseDeal, kind, paid, companyName: "Тест", items: [{ productId: "p" }], stage: "Товар отложен", reservedUntil: "2026-11-17" }), /3 000/);
    }
  }
  for (const paid of [0, 3000, 34900]) {
    const expected = paid === 0 ? "2026-11-04" : "2026-11-17";
    assert.equal(defaultHoldUntil(paid, today), expected);
    assert.equal(reserveUntilForSave({ paid, total: 34900, stage: "Товар отложен", today }), expected);
    assert.deepEqual(amoOtlozhka({ paid, total: 34900, stage: "Товар отложен", today }), { kind: paid === 0 ? "Бесплатная" : "Платная", until: expected });
    assert.equal(holdPaymentError(paid), null);
  }
});


test("online conversion preserves the order and queues assembly after CRM stage sync", async () => {
  const { readFileSync } = await import("node:fs");
  const { createRequire } = await import("node:module");
  const { runInNewContext } = await import("node:vm");
  const ts = createRequire(new URL("../../../apps/api/package.json", import.meta.url))("typescript");
  const shared = await import("../dist/index.js");
  assert.equal(shared.convertKindSchema.safeParse({ kind: "delivery" }).success, false);
  assert.equal(shared.convertKindSchema.safeParse({ kind: "delivery", deliveryCity: "Москва" }).success, true);
  for (const kind of ["sale", "deferred", "promise"]) {
    const original = { ...baseDeal, id: `convert-${kind}`, number: 9100, kind, stage: "Дано обещание", amoLeadId: 123, consultant: "Консультант", clientPhone: "+70000000000", items: [{ productId: "product", name: "Товар", price: 5000, qty: 1 }], payments: [{ id: "p", methodId: "cash_stores", amount: 3000 }], total: 5000, paid: 3000 };
    const table = (table, columns) => Object.assign({ table }, Object.fromEntries(columns.map((c) => [c, c])));
    const schema = { deals: table("deals", ["number"]), amoMeta: table("meta", ["kind"]), tasks: table("tasks", ["id", "dealNumber", "kind", "status"]), users: table("users", ["name", "role"]), dealItemState: table("items", []) };
    const state = { deals: [{ number: original.number, data: original }], meta: [{ kind: "status", parentId: 99, amoId: 1234, name: "Передан на сборку" }], tasks: [], users: [] };
    const query = (operation, fields) => {
      let target, predicate = () => true, values, cached;
      const q = {
        from(t) { target = t.table; return q; }, where(p) { predicate = p; return q; }, limit() { return q; },
        values(v) { values = v; return q; }, onConflictDoUpdate() { return q; }, onConflictDoNothing() { return q; }, returning(f) { fields = f; return q; },
        then(resolve, reject) {
          cached ??= Promise.resolve().then(() => {
            if (operation === "select") return state[target].filter(predicate);
            if (target === "deals") { state.deals[0] = values; return fields ? [{ number: values.number }] : []; }
            const row = { id: "task-1", status: "pending", ...values }; state.tasks.push(row);
            return fields ? [row] : [];
          });
          return cached.then(resolve, reject);
        },
      }; return q;
    };
    const db = { select: (f) => query("select", f), insert: (t) => query("insert").from(t) };
    const writes = [];
    const dependencies = {
      "@kassa/shared": shared,
      "drizzle-orm": { eq: (c, v) => (r) => r[c] === v, inArray: (c, vs) => (r) => vs.includes(r[c]), and: (...ps) => (r) => ps.every((p) => p(r)), desc: (x) => x, sql: () => {} },
      "../db/index.js": { db }, "../db/schema.js": schema,
      "../clients/amo.js": { getLead: async () => ({ pipeline_id: 99 }), updateLead: async (...args) => writes.push(args), addLeadNote: async () => {} },
      "./bootstrap.js": {}, "../ws/hub.js": { broadcast: () => {} }, "./queue.js": {}, "./amoMapping.js": {},
      "./sale.js": {}, "./audit.js": { appendSystemAudit: async () => {} },
      "../lib/idempotency.js": { withOperationLock: async (_key, fn) => fn() },
      "../env.js": { getEnv: () => ({ AMOCRM_ASYNC_EDITS: "false" }) },
      "./amoEditQueue.js": { prepareAmoEdit: async () => { throw new Error("Conversion must not enqueue a descriptive edit"); } },
    };
    function load(name) {
      const code = ts.transpileModule(readFileSync(new URL(`../../../apps/api/src/services/${name}.ts`, import.meta.url), "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
      const module = { exports: {} };
      runInNewContext(code, { module, exports: module.exports, require: (id) => { if (!(id in dependencies)) throw new Error(`Unmocked ${id}`); return dependencies[id]; }, Date, console });
      return module.exports;
    }
    const deals = load("deals"); dependencies["./deals.js"] = deals;
    const flow = load("taskFlow");
    const converted = await deals.convertDealKind("9100", { kind: "delivery", deliveryCity: "Москва" }, "Автор");
    assert.equal(converted.kind, "delivery"); assert.equal(converted.funnel, "online"); assert.equal(converted.stage, "Передан на сборку");
    for (const key of ["number", "amoLeadId", "consultant", "store", "paid", "total"]) assert.equal(converted[key], original[key]);
    assert.equal(JSON.stringify(converted.items), JSON.stringify(original.items));
    assert.equal(JSON.stringify(converted.payments), JSON.stringify(original.payments));
    assert.equal(converted.recipientPhone, original.clientPhone);
    assert.equal(writes[0][1].statusId, 1234);
    await flow.onDealStage(converted, "Автор");
    assert.equal(state.tasks.length, 1); assert.equal(state.tasks[0].kind, "assemble_cdek");
    assert.equal(state.tasks[0].assigneeRole, "consultant"); assert.equal(state.tasks[0].store, original.store);
  }
});

test("no_sliv requires client name, complete phone and comment", () => {
  const valid = { ...baseDeal, kind: "no_sliv", stage: "Не слив", clientName: "Тест", clientPhone: "79999999999", comment: "Тест" };
  assert.equal(createDealSchema.safeParse(valid).success, true);
  for (const patch of [{clientName:" "},{clientPhone:""},{clientPhone:"123"},{comment:" "}]) {
    assert.equal(createDealSchema.safeParse({...valid,...patch}).success,false);
  }
});
test("return source excludes refunds, rentals, certificates and failed deals", () => {
  for (const kind of ["refund","rental","cert_plastic","cert_digital","promise"]) assert.equal(isReturnSource({kind,stage:"Успех"}),false);
  for (const kind of ["sale","company","delivery","exchange"]) assert.equal(isReturnSource({kind,stage:"Успех"}),true);
  assert.equal(isReturnSource({kind:"sale",stage:"Провал"}),false);
});
test("remaining returns account for aliases, partial quantities, exchanges and failed attempts", () => {
  const item={productId:"p",name:"Товар",price:100,qty:3};
  const source={...baseDeal,number:10,amoLeadId:20,stage:"Успех",items:[item]};
  const refund={...baseDeal,kind:"refund",stage:"Успех",linkedDealNumber:10,items:[{...item,qty:1,isReturn:true}]};
  const exchange={...baseDeal,kind:"exchange",stage:"Успех",linkedDealNumber:20,items:[{...item,qty:1,isReturn:true},{...item,qty:5}]};
  assert.equal(returnableItems(source,[refund,exchange])[0].qty,1);
  assert.deepEqual(returnableItems(source,[refund,exchange,{...refund,items:[{...item,qty:1}]}]),[]);
  assert.equal(returnableItems(source,[{...refund,stage:"Провал"}])[0].qty,3);
  assert.equal(source.items[0].qty,3);
});

test("editing products recalculates total and payments while keeping the receipt discount", () => {
  const original={...baseDeal,items:[{productId:"a",name:"A",price:17100,qty:1}],total:17100,paid:0};
  const items=[...original.items,{productId:"b",name:"B",price:5500,qty:1}];
  assert.equal(recalculateDealTotals(original,{items,total:17100}).total,22600);
  assert.equal(recalculateDealTotals({...original,total:16100},{items}).total,21600);
  assert.equal(recalculateDealTotals(original,{payments:[{id:"p",methodId:"cash",amount:1000}],paid:9999}).paid,1000);
  assert.equal(recalculateDealTotals(original,{}).total,17100);
  assert.equal(recalculateDealTotals({...original,items:[],total:17100},{items}).total,22600);
});


test("obsolete automatic tasks follow stages without cancelling manual or physical work", () => {
 const deal={...baseDeal,id:"task-test",kind:"sale",stage:"Успех"};
 const task=(kind,key,metadata)=>({kind,idempotencyKey:key,metadata});
 for(const kind of ["reserve","unreserve","assemble_cdek","call_courier","take_to_cdek","pickup_from_cdek"])
  assert.equal(isObsoleteStageTask(deal,task(kind,`${kind}:task-test`)),true,kind);
 assert.equal(isObsoleteStageTask(deal,task("promise_follow","promise:task-test:2026-11-04:hash")),true);
 assert.equal(isObsoleteStageTask(deal,task("reserve_call","reserve-call:task-test:2026-11-04")),true);
 assert.equal(isObsoleteStageTask(deal,task("reserve",undefined)),false);
 assert.equal(isObsoleteStageTask(deal,task("movement","movement-setup:task-test",{needsSetup:true})),true);
 assert.equal(isObsoleteStageTask(deal,task("movement","movement:task-test")),false);
 assert.equal(isObsoleteStageTask(deal,task("movement_accept","movement_accept:task-test")),false);
 assert.equal(isObsoleteStageTask(deal,task("sary_send","sary_send:task-test")),false);
 assert.equal(isObsoleteStageTask({...deal,stage:"Товар в магазине"},task("reserve","reserve:task-test")),false);
 assert.equal(isObsoleteStageTask({...deal,stage:"Ждет товар"},task("reserve","reserve:task-test")),true);
 assert.equal(isObsoleteStageTask({...deal,stage:"Собран",kind:"delivery"},task("assemble_cdek","assemble_cdek:task-test")),true);
});

test("explicitly confirmed removal survives a failed deal and extended deadlines cancel stale calls", () => {
 const deal={...baseDeal,id:"task-test",kind:"deferred",stage:"Провал"};
 assert.equal(isObsoleteStageTask(deal,{kind:"unreserve",idempotencyKey:"unreserve:task-test",metadata:{confirmedByRole:"rop"}}),false);
 assert.equal(isObsoleteStageTask({...deal,stage:"Товар отложен",reservedUntil:"2026-11-10"},{kind:"reserve_call",idempotencyKey:"reserve-call:task-test:2026-11-02",metadata:{reservedUntil:"2026-11-02"}}),true);
});


test("receipt and referral discounts stay in full and partial returns, including kopecks", async () => {
  const { settledItems, cartLineTotal } = await import('../dist/index.js');
  const source = { ...baseDeal, number: 991, stage: 'Успех', total: 9000, paid: 9000,
    items: [{ productId: 'a', name: 'A', price: 10000, qty: 1 }], saryBonus: 1000 };
  assert.equal(settledItems(source).reduce((s, i) => s + cartLineTotal(i), 0), 9000);
  assert.equal(returnableItems(source, []).reduce((s, i) => s + cartLineTotal(i), 0), 9000);
  const thirds = settledItems({ ...source, total: 1, items: [{ productId: 'a', name: 'A', price: 1, qty: 3 }] });
  assert.equal(thirds.reduce((s, i) => s + Math.round(cartLineTotal(i) * 100), 0), 100);
  assert.equal(thirds.reduce((s, i) => s + i.qty, 0), 3);
  const withFees = settledItems({ ...source, total: 9500, deliveryAmount: 500 });
  assert.equal(withFees.reduce((s, i) => s + cartLineTotal(i), 0), 9000);
});


test("server rejects unpaid success, forged totals, duplicate payments and missing defect evidence", async () => {
  const {financialDealError} = await import('../dist/index.js');
  const complete={...baseDeal,stage:'Успех',clientPhone:'+79990000000',channel:'Сайт',purpose:'Работа',items:[{productId:'a',name:'A',price:100,qty:1}],total:100,paid:100,payments:[{id:'p',methodId:'cash',amount:100}]};
  assert.equal(financialDealError(complete),null);
  assert(financialDealError({...complete,tips:50}));
  assert(financialDealError({...complete,changePayouts:[{methodId:"cash",amount:100}]}));
  for (const patch of [{items:[]},{paid:0,payments:[]},{paid:200,payments:[...complete.payments,...complete.payments]}]) assert(financialDealError({...complete,...patch}));
  assert(financialDealError({...complete,kind:'refund',total:-1000}));
  assert.equal(financialDealError({...complete,kind:'refund',total:-100,paid:0,payments:[]}),null);
  assert(financialDealError({...baseDeal,kind:'defect',items:complete.items,comment:'Дефект'}));
  assert.equal(financialDealError({...baseDeal,kind:'defect',items:complete.items,comment:'Дефект'},1),null);
});
test("report dates use Moscow completion, and returns/defects are not new clients", async () => {
  const {dealAccountingDay,isSalesVisit,isTestDeal}=await import('../dist/index.js');
  assert.equal(dealAccountingDay({...baseDeal,createdAt:'2026-10-01T10:00:00Z',stage:'Успех',completedAt:'2026-11-02T21:30:00Z'}),'2026-11-03');
  assert(isTestDeal({...baseDeal,clientName:'ТЕСТ кассы'}));
  assert(!isTestDeal({...baseDeal,clientName:'Клиент'}));
  for (const kind of ['refund','exchange','defect','drycleaning']) assert.equal(isSalesVisit({...baseDeal,kind}),false);
});


test("test returns do not consume real stock return allowance, but still count within test sales", () => {
  const source = {...baseDeal,number:100,stage:'Успех',total:100,items:[{productId:'a',name:'A',price:100,qty:1}]};
  const returned = {...source,number:101,kind:'refund',linkedDealNumber:100,isTest:true};
  assert.equal(returnableItems(source,[returned])[0].qty,1);
  assert.equal(returnableItems({...source,isTest:true},[returned]).length,0);
  assert.equal(returnableItems(source,[{...returned,isTest:false}]).length,0);
});
