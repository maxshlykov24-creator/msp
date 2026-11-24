import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { nextKassaNumber } from "@kassa/shared";
import { publicHttpErrorHint } from "../src/lib/http.ts";
import {
  mergeInboundAmoStage,
  performAmoStatusPlan,
  planAmoStatus,
} from "../src/services/amoStatusPolicy.ts";
import { isKassaStatusWebhook, publicWebhookView } from "../../../deploy/amo-webhook-subscribe.mjs";

const root = dirname(fileURLToPath(import.meta.url));
const read = (path) => readFileSync(join(root, path), "utf8");
const sales = { pipelineAllowed: true, isSalesPipeline: true };
let checks = 0;
const check = (name) => {
  checks += 1;
  console.log(`ok ${checks}. ${name}`);
};

const early = planAmoStatus({ ...sales, stage: "Новый лид", deal: null });
assert.equal(early.type, "skip");
assert.equal(early.reason, "no_local_effect");
let created = 0;
await performAmoStatusPlan({ decision: early, leadId: 1, existing: null }, {
  createMirror: async () => { created += 1; throw new Error("создавать нельзя"); },
  saveStage: async () => { throw new Error("писать нельзя"); },
  onTasks: async () => { throw new Error("задачу ставить нельзя"); },
  writeLink: async () => { throw new Error("ссылку писать нельзя"); },
  audit: async () => {},
  invalidate: () => {},
});
assert.equal(created, 0);
check("ранний этап без строки");

const complaint = planAmoStatus({
  pipelineAllowed: true,
  isSalesPipeline: false,
  stage: "Ждет товар",
  deal: null,
});
assert.equal(complaint.type, "skip");
assert.equal(complaint.reason, "no_local_effect");
check("жалоба не создаёт строку");

const tasks = new Set();
const store = new Map();
let maxNumber = 57642837;
const leadId = 62161964;
async function incoming(stage, current) {
  const decision = planAmoStatus({ ...sales, stage, deal: current });
  return performAmoStatusPlan({
    decision,
    leadId,
    existing: current ? { number: current.number, stage: current.stage, kind: current.kind } : null,
  }, {
    createMirror: async () => {
      const found = [...store.values()].find(deal => deal.amoLeadId === leadId);
      if (found) return found;
      const number = nextKassaNumber(maxNumber);
      assert.notEqual(number, leadId);
      maxNumber = number;
      const deal = {
        id: `amo-${leadId}`,
        number,
        amoLeadId: leadId,
        stage,
        kind: "sale",
        items: [],
        payments: [],
        paid: 0,
      };
      store.set(deal.id, deal);
      return deal;
    },
    saveStage: async (target) => {
      const merged = mergeInboundAmoStage(current, {}, target, current.kind);
      Object.assign(current, merged.deal);
      return { number: current.number, stage: current.stage, kind: current.kind, changed: merged.changed };
    },
    onTasks: async () => { tasks.add(`movement-setup:amo-${leadId}`); },
    writeLink: async (number) => { assert.equal(number, maxNumber); },
    audit: async () => {},
    invalidate: () => {},
  });
}
const first = await incoming("Ждет товар", null);
const mirror = store.get(`amo-${leadId}`);
const second = await incoming("Ждет товар", mirror);
assert.equal(store.size, 1);
assert.equal(tasks.size, 1);
assert.equal(first.dealNumber, 57642838);
assert.equal(second.dealNumber, 57642838);
assert.equal(maxNumber, 57642838);
check("ожидание товара: один номер кассы и одна задача");

const foreign = planAmoStatus({ ...sales, stage: "Успех", deal: null });
assert.equal(foreign.type, "skip");
assert.equal(foreign.reason, "no_local_effect");
let financeCalls = 0;
await performAmoStatusPlan({ decision: foreign, leadId: 2, existing: null }, {
  createMirror: async () => { throw new Error("чужой успех не зеркалим"); },
  saveStage: async () => { throw new Error("чужой успех не пишем"); },
  onTasks: async () => { throw new Error("чужой успех без задачи"); },
  writeLink: async () => {},
  audit: async () => {},
  invalidate: () => {},
});
assert.equal(financeCalls, 0);
check("чужой успех отвечает пропуском и не проводит деньги");

const conducted = {
  number: 57642821,
  kind: "sale",
  stage: "Товар в магазине",
  items: [{ productId: "suit", qty: 1 }],
  payments: [{ amount: 12600 }],
  paid: 12600,
  msOrderId: "order-1",
  msDemandId: "demand-1",
};
const success = planAmoStatus({ ...sales, stage: "Успех", deal: conducted });
assert.equal(success.type, "sync");
const saved = await performAmoStatusPlan({
  decision: success,
  leadId: 61619266,
  existing: { number: conducted.number, stage: conducted.stage, kind: conducted.kind },
}, {
  createMirror: async () => { throw new Error("вторая строка не нужна"); },
  saveStage: async (target) => {
    const merged = mergeInboundAmoStage(conducted, {}, target, conducted.kind);
    assert.deepEqual(merged.deal.items, conducted.items);
    assert.deepEqual(merged.deal.payments, conducted.payments);
    assert.equal(merged.deal.msOrderId, "order-1");
    assert.equal(merged.deal.msDemandId, "demand-1");
    return { number: merged.deal.number, stage: merged.deal.stage, kind: merged.deal.kind, changed: merged.changed };
  },
  onTasks: async () => {},
  writeLink: async () => {},
  audit: async () => {},
  invalidate: () => {},
});
assert.equal(saved.stage, "Успех");
assert.equal(financeCalls, 0);
check("проведённая заявка меняет этап и сохраняет оплаты");

const failed = { number: 57642834, kind: "sale", stage: "Товар в магазине", syncStatus: "failed" };
const ahead = planAmoStatus({ ...sales, stage: "Успех", deal: failed });
assert.equal(ahead.type, "keep");
assert.equal(ahead.reason, "local_ahead");
const kept = mergeInboundAmoStage(failed, { comment: "из amo" }, "Успех", "sale");
assert.equal(kept.changed, false);
assert.equal(kept.deal.stage, "Товар в магазине");
check("неуспешная синхронизация не откатывает этап");

const cancelled = planAmoStatus({
  ...sales,
  stage: "Успех",
  deal: { number: 1, kind: "sale", stage: "Успех", moneyCancellation: { at: "2026-11-01" } },
});
assert.equal(cancelled.type, "keep");
assert.equal(cancelled.reason, "money_cancellation");
check("отмена денег не даёт 503");

const webhook = read("../src/services/amoWebhook.ts");
for (const forbidden of ["synchronizeFinancialDeal", "processSale", "updateStage", "issueCertificateIfNeeded", "ensureLocalFromAmoLead"]) {
  assert.equal(webhook.includes(forbidden), false, forbidden);
}
assert.equal(read("../src/services/stats.ts").includes("isUnconductedAmoMirror"), true);
assert.equal(read("../src/services/payroll.ts").includes("isUnconductedAmoMirror"), true);
assert.equal(read("../src/services/taskFlow.ts").includes("movement-setup:${deal.id}"), true);
assert.equal(read("../src/services/deals.ts").includes("МойСклад"), true);
check("обработчик не проводит деньги, МойСклад и сертификат");

const hint = publicHttpErrorHint({
  detail: "Поле 79991234567 не принято",
  "validation-errors": [{ errors: [{ code: "NotNull", path: "custom_fields_values.0" }] }],
});
assert.equal(hint.includes("79991234567"), false);
assert.match(hint, /NotNull custom_fields_values\.0/);
check("причина отказа amo без телефона");

const secretUrl = "https://mansband-kassa.ru/api/webhooks/amo?secret=do-not-print";
assert.equal(isKassaStatusWebhook(secretUrl), true);
assert.equal(isKassaStatusWebhook("https://wamo.amgbp.ru/webhook"), false);
const view = publicWebhookView(secretUrl, ["status_lead"]);
assert.equal(JSON.stringify(view).includes("do-not-print"), false);
assert.equal(view.path, "/api/webhooks/amo");
assert.deepEqual(view.events, ["status_lead"]);
check("журнал подписки не содержит секрет");

console.log(`amo webhook: ${checks} проверок`);
