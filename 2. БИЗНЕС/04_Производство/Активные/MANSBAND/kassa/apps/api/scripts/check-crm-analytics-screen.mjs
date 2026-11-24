import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { loadCrmSnapshot, renderCrmAnalytics, snapshotPath } from "../src/services/crmAnalyticsReport.ts";

const dir = new URL("../reports/", import.meta.url);
const files = {
  shell: readFileSync(new URL("crm-analytics.html", dir), "utf8"),
  calc: readFileSync(new URL("crm-analytics-calc.mjs", dir), "utf8"),
  view: readFileSync(new URL("crm-analytics-view.js", dir), "utf8"),
};
const scratch = mkdtempSync(path.join(tmpdir(), "crm-analytics-"));
writeFileSync(path.join(scratch, "broken.json"), "{");
assert.equal(loadCrmSnapshot(path.join(scratch, "missing.json")), null);
assert.equal(loadCrmSnapshot(path.join(scratch, "broken.json")), null);
const previous = process.env.CRM_ANALYTICS_DIR;
process.env.CRM_ANALYTICS_DIR = scratch;
assert.equal(snapshotPath(), path.join(scratch, "crm-analytics-snapshot.json"));
if (previous == null) delete process.env.CRM_ANALYTICS_DIR;
else process.env.CRM_ANALYTICS_DIR = previous;
rmSync(scratch, { recursive: true, force: true });
const demo = renderCrmAnalytics(null, files);
assert.equal(demo.mode, "demo");
assert.equal(demo.html.includes("function buildDeals"), true);
assert.equal(demo.html.includes("Демоданные"), true);

const snapshot = {
  meta: {
    accepted: false,
    builtAt: "2026-11-24T09:00:00.000Z",
    timeZone: "Europe/Moscow",
    weekStartsOn: 1,
    weekStartConfirmed: false,
    fieldProblems: [],
    week: { from: Date.parse("2026-11-15T21:00:00Z"), to: Date.parse("2026-11-22T21:00:00Z"), label: "16.11.2026 — 22.11.2026", closed: 1, created: 1, discrepancies: 0, checkbox: 1, duplicates: 0 },
    coverage: { complete: true, closedFrom: Date.parse("2026-11-15T21:00:00Z"), closedTo: Date.parse("2026-11-22T21:00:00Z"), createdFrom: Date.parse("2026-11-15T21:00:00Z"), createdTo: Date.parse("2026-11-22T21:00:00Z") },
    queueComplete: true,
    queueHistory: "not_archive",
    liveStages: ["Дано обещание", "Ждет товар", "Товар в пути", "Товар в магазине", "Встреча назначена", "Передан на сборку", "Доставлен"],
    reopen: { note: "не проверено" },
    blockers: ["Первый день недели в аккаунте amo не прочитан"],
    kassa: "unavailable",
    digest: null,
  },
  deals: [{
    id: 42, created: Date.parse("2026-11-16T10:00:00Z"), closed: Date.parse("2026-11-18T10:00:00Z"), result: "Успех",
    pipelineId: 9601214, stage: "Успех", lane: "off", source: "Сайт <script>", sourceConflict: false, touch: "yes",
    takenAt: null, qualAt: null, targetFlag: null, reason: null, history: [{ stage: "Успех", at: Date.parse("2026-11-18T10:00:00Z") }],
    historyComplete: true, tasks: [], stageDue: null, stageDues: {}, fallbackDue: null, test: false, kassaNumber: null,
    duplicate: false, owner: "Менеджер", unsorted: false, laneConflict: false,
  }],
};
const review = renderCrmAnalytics(snapshot, files);
assert.equal(review.mode, "review");
assert.equal(review.html.includes("function buildDeals"), false);
assert.equal(review.html.includes("нет источника"), true);
assert.equal(review.html.includes("только в кассе"), true);
assert.equal(review.html.includes("<script>"), true);
assert.equal(review.html.includes("\\u003cscript"), true);
assert.equal(review.html.includes("Сайт <script>"), false);
const live = renderCrmAnalytics({ ...snapshot, meta: { ...snapshot.meta, accepted: true } }, files);
assert.equal(live.mode, "live");
console.log("check-crm-analytics-screen: ok");
