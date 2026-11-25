import assert from "node:assert/strict";
import {
  DICTIONARY, QUALITY_SOURCES, SALES, OFFLINE, ONLINE, TEST_TAG, acceptance,
  catalogProblems, chain, classifyTouch, checkboxDisagreements, cohort, digestOverdue,
  dealOutcome, funnel, general, isKassaWeekSuccess, kassaCases, kassaNumberFromLink, kassaStatus, liveStageNames, medianHours, normalizeLead, outcomeAt, overdueStages, reopenGate, replyAudit, responseSummary, restrictDeals, slowReplyCutoff, wazzupReplies,
  overdueTasks, periodBounds, periodCovered, qualOutsideTarget, queueArchiveStart, queueAt, queueHistory, stageAt, zonedParts, zonedTimeToUtc,
} from "../reports/crm-analytics-calc.mjs";

const TZ = "Europe/Moscow";
const users = new Map([[1, "Менеджер"]]);
const monday = zonedTimeToUtc(2026, 11, 16, 0, 0, 0, TZ);
assert.equal(monday, Date.parse("2026-11-15T21:00:00Z"));
const sundayEvening = Date.parse("2026-11-15T20:00:00Z");
assert.equal(zonedParts(sundayEvening, TZ).weekday, 0);
assert.equal(zonedParts(sundayEvening, TZ).hh, 23);

const week = periodBounds("week", monday + 3600_000, monday + 8 * 86400_000, TZ, 1);
assert.equal(week.from, monday);
assert.equal(week.to, monday + 7 * 86400_000);
const previous = periodBounds("week", sundayEvening, monday + 86400_000, TZ, 1);
assert.equal(previous.from, zonedTimeToUtc(2026, 11, 9, 0, 0, 0, TZ));

function field(id, value) {
  return { field_id: id, values: [{ value }] };
}
function raw(over = {}) {
  return {
    id: over.id ?? 10,
    pipeline_id: over.pipeline ?? SALES,
    status_id: over.status ?? 100,
    created_at: Math.floor((over.created ?? monday) / 1000),
    closed_at: over.closed == null ? null : Math.floor(over.closed / 1000),
    responsible_user_id: 1,
    custom_fields_values: over.fields ?? [],
    _embedded: { tags: over.tags ?? [] },
    name: over.name ?? "Клиент",
  };
}
function deal(over = {}, extras = {}) {
  return normalizeLead(raw(over), users, extras);
}

const bounds = { from: monday, to: monday + 7 * 86400_000, cut: monday + 7 * 86400_000 };
const won = deal({
  id: 1, status: 142, created: monday - 86400_000, closed: monday + 86400_000, fields: [],
}, { history: [{ stage: "Успех", at: monday + 86400_000 }], historyComplete: true, touch: "yes" });
assert.equal(won.test, false);
const groups = general([won], "results", bounds, {});
assert.equal(groups.all.length, 1);
assert.equal(groups.target.length, 1);
assert.equal(groups.qual.length, 1);
assert.equal(groups.success.length + groups.fail.length, groups.all.length);

const lost = deal({ id: 2, status: 143, created: monday, closed: monday + 2 * 86400_000, fields: [field(3134051, "Не подошла цена")] }, { historyComplete: true, touch: "yes" });
assert.equal(general([lost], "results", bounds, {}).target.length, 0);

const lateTaken = deal({
  id: 3, status: 100, created: monday + 86400_000, fields: [field(3434463, Math.floor((bounds.to + 86400_000) / 1000))],
}, { history: [{ stage: "Взято в работу", at: monday + 86400_000 }], historyComplete: true, touch: "yes" });
assert.equal(general([lateTaken], "sources", bounds, {}).target.length, 0);

const spam = deal({
  id: 4, status: 143, created: monday, closed: monday + 86400_000,
  fields: [field(3434465, Math.floor(monday / 1000)), field(3134051, "Спам")],
}, { history: [{ stage: "Провал", at: monday + 86400_000 }], historyComplete: true, touch: "yes" });
assert.equal(general([spam], "results", bounds, {}).qual.length, 0);

const qualOnly = deal({
  id: 5, status: 100, created: monday, fields: [field(3434465, Math.floor((monday + 1000) / 1000))],
}, { history: [{ stage: "Новая заявка", at: monday }], historyComplete: true, touch: "yes" });
const qualGroups = general([qualOnly], "sources", bounds, {});
assert.equal(qualGroups.target.length, 0);
assert.equal(qualGroups.qual.length, 1);
assert.equal(qualOutsideTarget(qualGroups, "sources", bounds).length, 1);

const open = deal({ id: 6, status: 100, created: monday + 1000 }, { history: [{ stage: "Новая заявка", at: monday + 1000 }], historyComplete: true, touch: "yes" });
const sourceGroups = general([open], "sources", bounds, {});
assert.equal(sourceGroups.all.length, 1);
assert.equal(sourceGroups.success.length, 0);
assert.equal(sourceGroups.unknown.length, 0);
assert.equal(outcomeAt(open, bounds.cut), "В работе");

const broken = deal({ id: 7, status: 142, created: monday, closed: bounds.to + 86400_000 }, { historyComplete: false, touch: "unknown" });
const brokenGroups = general([broken], "sources", bounds, {});
assert.equal(brokenGroups.success.length, 0);
assert.equal(brokenGroups.target.length, 0);
assert.equal(brokenGroups.unknown.length, 1);

const placeholder = deal({ id: 8, fields: [field(3382311, Math.floor(Date.parse("2001-01-01T00:00:00+03:00") / 1000))] }, { stage: "Встреча назначена" });
assert.equal(placeholder.stageDue, null);

const names = new Set(["Взято в работу", "Дано обещание", "Хочет прийти", "Товар в пути", "Ждет товар", "Товар в магазине", "Товар отложен", "Встреча назначена"]);
const both = chain("effect", "off", names);
assert.equal(both.filter((name) => name === "Товар в пути" || name === "Ждет товар").length, 2);
const missing = chain("effect", "off", new Set(["Взято в работу", "Дано обещание", "Хочет прийти", "Товар в магазине", "Товар отложен", "Встреча назначена"]));
assert.equal(missing.includes("Товар в пути"), false);
assert.equal(missing.includes("Ждет товар"), false);

const unknownTouch = cohort([deal({ id: 9, status: 142, closed: monday + 1000 }, { touch: "unknown", historyComplete: true })], "results", bounds, { touch: "yes" });
const unknownAll = cohort([deal({ id: 9, status: 142, closed: monday + 1000 }, { touch: "unknown", historyComplete: true })], "results", bounds, {});
assert.equal(unknownTouch.length, 0);
assert.equal(unknownAll.length, 1);
assert.equal(classifyTouch({ historyComplete: false, history: [], createdInTerminal: true, kassaCreated: false, priorOpenLead: false, priorLeadKnown: null }).touch, "unknown");
assert.equal(classifyTouch({ historyComplete: true, history: [{ stage: "Успех", at: 1 }], createdInTerminal: true, kassaCreated: true, priorOpenLead: false, priorLeadKnown: false }).touch, "no");
assert.equal(classifyTouch({ historyComplete: true, history: [], createdInTerminal: true, kassaCreated: true, priorOpenLead: true, priorLeadKnown: true }).bindingError, true);

const tagged = deal({ id: 11, tags: [{ name: TEST_TAG }] });
const named = deal({ id: 12, name: "тест клиента" });
assert.equal(tagged.test, true);
assert.equal(named.test, false);
assert.equal(cohort([tagged, named], "sources", bounds, {}).length, 1);

const conflict = deal({ id: 13, pipeline: OFFLINE, fields: [field(3134039, "Онлайн")] });
assert.equal(conflict.lane, "unknown");
assert.equal(conflict.laneConflict, true);
const offlineOnly = cohort([conflict], "sources", bounds, { lane: "off" });
assert.equal(offlineOnly.length, 0);

assert.equal(QUALITY_SOURCES.response.available, false);
assert.equal(QUALITY_SOURCES.calls, undefined);
assert.equal(DICTIONARY.some((row) => row.id === "calls"), false);
assert.equal(DICTIONARY.some((row) => row.id === "response" && row.empty.includes("не ноль")), true);

const now = zonedTimeToUtc(2026, 11, 20, 15, 0, 0, TZ);
const dueToday = zonedTimeToUtc(2026, 11, 20, 0, 0, 0, TZ);
const dueYesterday = zonedTimeToUtc(2026, 11, 19, 0, 0, 0, TZ);
const openStage = deal({
  id: 14, pipeline: SALES, status: 100, fields: [field(3382311, Math.floor(dueToday / 1000))],
}, { stage: "Встреча назначена", tasks: [{ id: 1, due: now - 1000, done: null, responsibleId: 1 }] });
const digest = digestOverdue([openStage], now);
assert.equal(digest.stages.length, 0);
const lateStage = deal({
  id: 15, pipeline: ONLINE, status: 100, fields: [field(3382311, Math.floor(dueYesterday / 1000))],
}, { stage: "Встреча назначена" });
assert.equal(digestOverdue([lateStage], now).stages.length, 1);

const closed = deal({ id: 16, status: 142, closed: monday + 3 * 86400_000 }, {
  historyComplete: true,
  tasks: [{ id: 9, due: monday, done: monday + 4 * 86400_000, responsibleId: 2, name: "Связаться" }],
});
assert.equal(overdueTasks(cohort([closed], "results", bounds, {})).length, 1);
const nightDone = zonedTimeToUtc(2026, 11, 16, 9, 30, 0, TZ);
const afterOpen = zonedTimeToUtc(2026, 11, 16, 10, 20, 0, TZ);
const nightDue = zonedTimeToUtc(2026, 11, 16, 4, 0, 0, TZ);
const closedNight = deal({ id: 160, status: 142, closed: monday + 86400_000 }, { historyComplete: true, tasks: [{ id: 90, due: nightDue, done: nightDone, name: "Ночная" }] });
const closedAfterOpen = deal({ id: 161, status: 142, closed: monday + 86400_000 }, { historyComplete: true, tasks: [{ id: 91, due: nightDue, done: afterOpen, name: "После открытия" }] });
const afterCloseDue = zonedTimeToUtc(2026, 11, 16, 23, 30, 0, TZ);
const beforeNextOpen = deal({ id: 162, status: 142, closed: monday + 2 * 86400_000 }, { historyComplete: true, tasks: [{ id: 92, due: afterCloseDue, done: zonedTimeToUtc(2026, 11, 17, 9, 0, 0, TZ), name: "До открытия" }] });
const afterNextOpen = deal({ id: 163, status: 142, closed: monday + 2 * 86400_000 }, { historyComplete: true, tasks: [{ id: 93, due: afterCloseDue, done: zonedTimeToUtc(2026, 11, 17, 10, 15, 0, TZ), name: "После следующего открытия" }] });
assert.equal(overdueTasks([closedNight]).length, 0);
assert.equal(overdueTasks([closedAfterOpen]).length, 1);
assert.equal(overdueTasks([beforeNextOpen]).length, 0);
assert.equal(overdueTasks([afterNextOpen]).length, 1);

const visited = deal({ id: 17, status: 142, closed: monday + 86400_000 }, {
  historyComplete: true, touch: "yes", lane: "off",
  history: [{ stage: "Успех", at: monday + 86400_000 }],
});
const report = funnel(cohort([visited], "results", bounds, {}), chain("effect", "off", names));
assert.equal(report.remainder.length, 1);
assert.equal(report.analysis.every((row) => row.visited.length === 0), true);

const pipelines = [{ id: SALES, _embedded: { statuses: [{ name: "Ждет товар" }, { name: "Счёт выставлен" }] } }, { id: 1, _embedded: { statuses: [{ name: "Товар в пути" }] } }];
assert.deepEqual([...liveStageNames(pipelines)], ["Ждет товар", "Счет выставлен"]);
const problems = catalogProblems([{ id: 3434463, name: "Другое" }]);
assert.equal(problems.includes("3434463: в каталоге «Другое», ожидалось «Дата Взято в работу»"), true);

const duplicate = deal({ id: 18, status: 143, closed: monday + 1000, fields: [field(3134051, "Сдвоенная заявка")] }, { historyComplete: true, touch: "yes" });
const duplicateGroups = general([duplicate, won], "results", bounds, {});
assert.equal(duplicateGroups.fail.length + duplicateGroups.success.length, duplicateGroups.all.length);
assert.equal(duplicate.duplicate, true);

const queue = queueAt([
  deal({ id: 19, created: monday }, { historyComplete: false }),
  deal({ id: 20, status: 142, created: monday - 86400_000, closed: monday + 100 }, { historyComplete: true }),
], monday + 500);
assert.deepEqual(queue.map((row) => row.id), [19]);

const outside = deal({ id: 21, status: 142, closed: bounds.to + 1000 }, { historyComplete: true });
assert.equal(general([outside], "results", bounds, {}).all.length, 0);

const flagged = deal({ id: 22, fields: [field(3796755, true)] });
const dated = deal({ id: 23, fields: [field(3796755, false), field(3434463, Math.floor(monday / 1000))] });
assert.equal(checkboxDisagreements([flagged, dated]).map((row) => row.id).join(","), "22,23");

const stageRows = overdueStages([deal({ id: 24, status: 142, closed: monday + 2 * 86400_000, fields: [field(3382311, Math.floor(monday / 1000))] }, {
  historyComplete: true,
  history: [{ stage: "Встреча назначена", at: monday }, { stage: "Успех", at: monday + 2 * 86400_000 }],
})], now);
assert.equal(stageRows.overdue.length, 1);
assert.equal(medianHours([], ["Дано обещание", "Доставлен"]).hours, null);
const noonReply = zonedTimeToUtc(2026, 11, 16, 12, 0, 0, TZ);
const replies = wazzupReplies([{ at: noonReply, dir: "in" }, { at: noonReply + 60_000, dir: "in" }, { at: noonReply + 5 * 60_000, dir: "out" }, { at: noonReply + 10 * 60_000, dir: "out" }, { at: noonReply + 11 * 60_000, dir: "in" }]);
assert.deepEqual(replies.minutes, [5]);
assert.equal(replies.series[0].from, noonReply);
assert.equal(replies.series[0].to, noonReply + 5 * 60_000);
assert.equal(replies.pending, 1);
assert.equal(replies.pendingFrom, noonReply + 11 * 60_000);
assert.equal(wazzupReplies([{ at: zonedTimeToUtc(2026, 11, 16, 22, 50, 0, TZ), dir: "in" }, { at: zonedTimeToUtc(2026, 11, 17, 10, 20, 0, TZ), dir: "out" }]).minutes[0], 30);
assert.equal(wazzupReplies([{ at: zonedTimeToUtc(2026, 11, 16, 23, 30, 0, TZ), dir: "in" }, { at: zonedTimeToUtc(2026, 11, 17, 10, 5, 0, TZ), dir: "out" }]).minutes[0], 5);
assert.equal(wazzupReplies([{ at: zonedTimeToUtc(2026, 11, 16, 9, 0, 0, TZ), dir: "in" }, { at: zonedTimeToUtc(2026, 11, 16, 10, 5, 0, TZ), dir: "out" }]).minutes[0], 5);
assert.equal(wazzupReplies([{ at: zonedTimeToUtc(2026, 11, 20, 22, 50, 0, TZ), dir: "in" }, { at: zonedTimeToUtc(2026, 11, 21, 10, 10, 0, TZ), dir: "out" }]).minutes[0], 20);
const marked = wazzupReplies([
  { at: noonReply, dir: "in", talk: 7 },
  { at: noonReply + 3 * 60_000, dir: "dismiss", talk: 7, reason: "no_reply" },
  { at: noonReply + 50 * 60_000, dir: "out", talk: 7 },
]);
assert.deepEqual(marked.minutes, [3]);
assert.equal(marked.pending, 0);
assert.equal(marked.series[0].close, "no_reply");
const closedTalk = wazzupReplies([
  { at: noonReply, dir: "in", talk: 8 },
  { at: noonReply + 4 * 60_000, dir: "dismiss", talk: 8, reason: "closed" },
]);
assert.deepEqual(closedTalk.minutes, [4]);
assert.equal(closedTalk.series[0].close, "closed");
assert.equal(closedTalk.pending, 0);
const otherTalk = wazzupReplies([
  { at: noonReply, dir: "in", talk: 1 },
  { at: noonReply + 2 * 60_000, dir: "dismiss", talk: 2, reason: "closed" },
  { at: noonReply + 9 * 60_000, dir: "out", talk: 1 },
]);
assert.deepEqual(otherTalk.minutes, [9]);
assert.equal(otherTalk.pending, 0);
const sameSecond = wazzupReplies([
  { at: noonReply, dir: "in", talk: 3 },
  { at: noonReply + 6 * 60_000, dir: "dismiss", talk: 3, reason: "no_reply" },
  { at: noonReply + 6 * 60_000, dir: "out", talk: 3 },
]);
assert.equal(sameSecond.series[0].close, "reply");
assert.equal(sameSecond.minutes[0], 6);
const stored = deal({}, {
  messages: [
    { at: noonReply, dir: "in", talk: 7 },
    { at: noonReply + 3 * 60_000, dir: "dismiss", talk: 7, reason: "closed" },
    { at: noonReply, dir: "note" },
  ],
});
assert.equal(stored.messages.length, 2);
assert.equal(stored.messages[1].dir, "dismiss");
assert.equal(stored.messages[1].reason, "closed");
assert.equal(stored.messages[1].talk, 7);
assert.deepEqual(wazzupReplies(stored.messages).minutes, [3]);
assert.equal(responseSummary([{ messages: [{ at: noonReply, dir: "in" }, { at: noonReply + 2 * 60_000, dir: "out" }] }, { messages: null }, { messages: [] }]).mean, 2);
assert.equal(responseSummary([{ messages: [{ at: noonReply, dir: "out" }] }]).mean, null);
assert.equal(slowReplyCutoff([5, 8, 12, 200]), 84.375);
assert.equal(slowReplyCutoff([90, 100, 110]), 150);
const audit = replyAudit([
  { id: 1, messages: [{ at: noonReply, dir: "in" }, { at: noonReply + 2 * 60_000, dir: "out" }] },
  { id: 2, messages: [{ at: noonReply, dir: "in" }, { at: noonReply + 90 * 60_000, dir: "out" }] },
  { id: 3, messages: [{ at: noonReply, dir: "in" }] },
], TZ);
assert.equal(audit.cutoff, 69);
assert.deepEqual(audit.slow.map((deal) => deal.id), [3, 2]);
assert.equal(audit.all[0].id, 3);
assert.equal(audit.all[1].id, 2);
const mixed = replyAudit([
  { id: 11, messages: [{ at: noonReply, dir: "in" }, { at: noonReply + 2 * 60_000, dir: "out" }, { at: noonReply + 3 * 60_000, dir: "in" }] },
  { id: 12, messages: [{ at: noonReply, dir: "in" }, { at: noonReply + 90 * 60_000, dir: "out" }] },
], TZ);
assert.deepEqual(mixed.all.map((deal) => deal.id), [12, 11]);
assert.equal(mixed.rows[1].max < mixed.cutoff, true);
assert.equal(mixed.rows[1].pending, true);
const stillOpen = deal({ id: 31, status: 100, created: monday - 86400_000 }, { history: [{ stage: "Новая заявка", at: monday - 86400_000 }], historyComplete: true });
const closedLater = deal({ id: 32, status: 142, created: monday, closed: bounds.cut + 86400_000 }, { history: [{ stage: "Новая заявка", at: monday }, { stage: "Успех", at: bounds.cut + 86400_000 }], historyComplete: true });
assert.equal(dealOutcome(won, "results", bounds), "success");
assert.equal(dealOutcome(lost, "results", bounds), "fail");
assert.equal(dealOutcome(stillOpen, "results", bounds), "open");
assert.equal(dealOutcome(closedLater, "results", bounds), "open");
const closedRows = [won, lost, spam];
assert.deepEqual(restrictDeals(closedRows, [stillOpen, closedLater], "results", bounds, {}).map((deal) => deal.id), [1, 2, 4]);
assert.deepEqual(restrictDeals(closedRows, [stillOpen, closedLater], "results", bounds, { outcomes: ["fail", "open"] }).map((deal) => deal.id), [2, 4, 31, 32]);
assert.deepEqual(restrictDeals(closedRows, [stillOpen], "results", bounds, { outcomes: ["success", "fail", "open"], reasons: ["Спам"] }).map((deal) => deal.id), [1, 4]);
assert.deepEqual(restrictDeals(closedRows, [stillOpen], "results", bounds, { outcomes: [] }).map((deal) => deal.id), []);
assert.equal(general(restrictDeals(closedRows, [], "results", bounds, { reasons: ["Спам"] }), "results", bounds, { ready: true }).fail.length, 1);
const sourceFail = deal({ id: 41, status: 143, created: monday, closed: monday + 86400_000, fields: [field(3134051, "Спам")] }, { history: [{ stage: "Провал", at: monday + 86400_000 }], historyComplete: true });
const sourceOpen = deal({ id: 42, status: 100, created: monday }, { history: [{ stage: "Новая заявка", at: monday }], historyComplete: true });
const sourceWon = deal({ id: 43, status: 142, created: monday, closed: monday + 86400_000 }, { history: [{ stage: "Успех", at: monday + 86400_000 }], historyComplete: true });
const sourceRows = cohort([sourceFail, sourceOpen, sourceWon], "sources", bounds, {});
assert.deepEqual(restrictDeals(sourceRows, [], "sources", bounds, { outcomes: ["fail"], reasons: ["Спам"] }).map((item) => item.id), [41]);
assert.deepEqual(restrictDeals(sourceRows, [], "sources", bounds, { outcomes: ["open"] }).map((item) => item.id), [42]);
assert.equal(general(restrictDeals(sourceRows, [], "sources", bounds, { outcomes: ["success", "open"] }), "sources", bounds, { ready: true }).success.length, 1);
const paced = deal({ id: 25, status: 142, closed: monday + 5 * 3600000 }, {
  historyComplete: true,
  history: [{ stage: "Дано обещание", at: monday }, { stage: "Доставлен", at: monday + 5 * 3600000 }],
});
assert.equal(medianHours([paced], ["Дано обещание", "Доставлен"]).hours, 5);
assert.equal(stageAt(paced, monday + 1000), "Дано обещание");
assert.equal(stageAt(paced, monday + 5 * 3600000), "Доставлен");
assert.equal(stageAt(deal({ id: 26 }, { historyComplete: false, history: [{ stage: "Ждет товар", at: monday }] }), monday + 1000), "");
const noon = zonedTimeToUtc(2026, 11, 20, 12, 0, 0, TZ);
const archive = queueArchiveStart(noon, TZ, 14);
const points = queueHistory([
  deal({ id: 40, created: archive - 86400_000 }, { historyComplete: true }),
  deal({ id: 41, status: 142, created: archive - 86400_000, closed: archive + 3 * 86400_000 }, { historyComplete: true }),
], noon, TZ, 14);
assert.equal(points.length, 14);
assert.equal(points[0].at >= archive, true);
assert.equal(points[0].ids.includes(40) && points[0].ids.includes(41), true);
assert.equal(points.filter((point) => point.at > archive + 3 * 86400_000).every((point) => point.ids.includes(40) && !point.ids.includes(41)), true);
const gate = acceptance({ fieldProblems: [], eventsAvailable: true, tasksAvailable: true, timezoneConfirmed: true, weekStartConfirmed: false, reopenVerified: true, partial: false, discrepancies: 0, checkboxCount: 0, kassa: "checked" });
assert.equal(gate.accepted, false);
const clearWeek = { fieldProblems: [], eventsAvailable: true, tasksAvailable: true, timezoneConfirmed: true, weekStartConfirmed: true, reopenVerified: true, partial: false, discrepancies: 0, checkboxCount: 0, kassa: "checked" };
assert.equal(acceptance(clearWeek).accepted, true);
assert.equal(reopenGate({ scanComplete: true, found: false, closedAtCleared: false }).verified, true);
assert.equal(acceptance({ ...clearWeek, reopenVerified: reopenGate({ scanComplete: true, found: false, closedAtCleared: false }).verified }).accepted, true);
assert.equal(reopenGate({ scanComplete: false, found: false, closedAtCleared: false }).verified, false);
assert.equal(reopenGate({ scanComplete: true, found: true, closedAtCleared: false }).verified, false);
const mismatch = kassaStatus(true, { onlyKassa: [1], closedOpen: [], amoMissing: [] });
assert.equal(mismatch, "mismatch");
assert.equal(acceptance({ ...clearWeek, kassa: mismatch }).accepted, false);
assert.equal(acceptance({ ...clearWeek, kassa: mismatch }).blockers.includes("Касса за эту неделю расходится с amo"), true);
assert.equal(kassaStatus(true, { onlyKassa: [], closedOpen: [], amoMissing: [] }), "checked");
assert.equal(kassaStatus(false, { onlyKassa: [], closedOpen: [], amoMissing: [] }), "unavailable");
assert.equal(isKassaWeekSuccess({ stage: "Успех", completedAt: monday + 1000 }, bounds.from, bounds.to), true);
assert.equal(isKassaWeekSuccess({ stage: "Ждет товар", completedAt: monday + 1000 }, bounds.from, bounds.to), false);
assert.equal(isKassaWeekSuccess({ stage: "Успех", isTest: true, completedAt: monday + 1000 }, bounds.from, bounds.to), false);
assert.equal(isKassaWeekSuccess({ id: "amo-1", stage: "Успех", paid: 0, payments: [], completedAt: monday + 1000 }, bounds.from, bounds.to), false);
assert.equal(isKassaWeekSuccess({ id: "amo-2", stage: "Успех", paid: 100, completedAt: monday + 1000 }, bounds.from, bounds.to), true);
assert.equal(isKassaWeekSuccess({ kind: "company", stage: "Товар выдан", issued: true, history: [{ action: "Этап изменён: Оплачено → Товар выдан", at: monday + 2000 }] }, bounds.from, bounds.to), true);
assert.equal(isKassaWeekSuccess({ kind: "company", stage: "Товар выдан", issued: false, completedAt: monday + 1000 }, bounds.from, bounds.to), false);
assert.equal(isKassaWeekSuccess({ stage: "Успех", completedAt: bounds.to + 1000 }, bounds.from, bounds.to), false);
assert.equal(kassaNumberFromLink("https://kassa/#board/all/all/57642834", 61703360), 57642834);
assert.equal(kassaNumberFromLink("https://kassa/#board/all/all/61703360", 61703360), null);
const sales = [
  { number: 1, amoLeadId: null, isTest: false, completedAt: monday + 1000 },
  { number: 2, amoLeadId: 1, isTest: false, completedAt: monday + 1000 },
  { number: 3, amoLeadId: 99, isTest: false, completedAt: monday + 1000 },
  { number: 4, amoLeadId: null, isTest: true, completedAt: monday + 1000 },
];
const cases = kassaCases([won], sales, bounds.from, bounds.to);
assert.deepEqual(cases.onlyKassa, [1]);
assert.deepEqual(cases.amoMissing, [{ number: 3, leadId: 99 }]);
assert.equal(cases.closedOpen.length, 0);
assert.equal(periodCovered("results", bounds, { complete: true, closedFrom: bounds.from, closedTo: bounds.to }), true);
assert.equal(periodCovered("sources", bounds, { complete: true, closedFrom: bounds.from, closedTo: bounds.to, createdFrom: bounds.to, createdTo: bounds.to }), false);

console.log("check-crm-analytics: ok");
