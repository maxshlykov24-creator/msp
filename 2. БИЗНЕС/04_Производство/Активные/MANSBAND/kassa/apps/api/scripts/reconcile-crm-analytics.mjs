// Только чтение amoCRM и кассы. Снимок пишется в CRM_ANALYTICS_DIR, не в vault.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  DIGEST_PIPELINES, REPORT_PIPELINES, WON, LOST, acceptance, canonicalStage, catalogProblems,
  chain, checkboxDisagreements, classifyTouch, digestOverdue, funnel, general, isKassaWeekSuccess,
  isQual, isTarget, kassaCases, kassaComparableSale, kassaNumberFromLink, kassaStatus, liveStageNames, normalizeLead, queueArchiveStart,
  overdueStages, overdueTasks, periodBounds, reopenGate,
} from "../reports/crm-analytics-calc.mjs";

function findEnv(start) {
  let current = start;
  for (let step = 0; step < 12; step += 1) {
    const candidate = path.join(current, "2. БИЗНЕС/04_Производство/Активные/MANSBAND/_private/kassa_access.env");
    if (fs.existsSync(candidate)) return candidate;
    const parent = path.dirname(current);
    if (parent === current) break;
    current = parent;
  }
  throw new Error("Файл доступа amo не найден");
}
const probeOnly = process.argv.includes("--probe");
const dir = process.env.CRM_ANALYTICS_DIR || "/root/.config/mansband";
const snapshotPath = path.join(dir, "crm-analytics-snapshot.json");
const reviewPath = path.join(dir, "crm-analytics-review.json");
const checkpointPath = path.join(dir, "crm-analytics-checkpoint.json");

function loadEnv(file) {
  const env = {};
  for (const line of fs.readFileSync(file, "utf8").split("\n")) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#") || !trimmed.includes("=")) continue;
    const index = trimmed.indexOf("=");
    let value = trimmed.slice(index + 1).trim();
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) value = value.slice(1, -1);
    env[trimmed.slice(0, index).trim()] = value;
  }
  return env;
}

const env = process.env.AMOCRM_API_BASE && process.env.AMOCRM_LONG_LIVED_TOKEN
  ? { AMOCRM_API_BASE: process.env.AMOCRM_API_BASE, AMOCRM_LONG_LIVED_TOKEN: process.env.AMOCRM_LONG_LIVED_TOKEN }
  : loadEnv(findEnv(path.dirname(fileURLToPath(import.meta.url))));
const base = env.AMOCRM_API_BASE.replace(/\/$/, "");
let nextAt = 0;
async function amo(pathname, query = {}) {
  const wait = nextAt - Date.now();
  if (wait > 0) await new Promise((resolve) => setTimeout(resolve, wait));
  nextAt = Date.now() + 350;
  const url = new URL(base + pathname);
  for (const [key, value] of Object.entries(query)) {
    if (Array.isArray(value)) value.forEach((item) => url.searchParams.append(key, String(item)));
    else if (value != null) url.searchParams.set(key, String(value));
  }
  let last = 0;
  for (let attempt = 0; attempt < 6; attempt += 1) {
    try {
      const response = await fetch(url, { headers: { Authorization: `Bearer ${env.AMOCRM_LONG_LIVED_TOKEN}` } });
      last = response.status;
      if (response.status === 204) return null;
      if (response.status === 429 || response.status >= 500) {
        await new Promise((resolve) => setTimeout(resolve, 1000 * (attempt + 1)));
        continue;
      }
      const text = await response.text();
      if (!response.ok) {
        const error = new Error(`amo ${response.status} ${pathname}`);
        error.status = response.status;
        throw error;
      }
      return text ? JSON.parse(text) : null;
    } catch (error) {
      if (error.status && error.status < 500) throw error;
      if (attempt === 5) throw error;
      await new Promise((resolve) => setTimeout(resolve, 1000 * (attempt + 1)));
    }
  }
  throw new Error(`amo retries exhausted ${pathname} ${last}`);
}

async function pages(pathname, key, query, cap = 80) {
  const rows = [];
  for (let page = 1; page <= cap; page += 1) {
    const data = await amo(pathname, { ...query, limit: 250, page });
    const batch = data?._embedded?.[key] ?? [];
    rows.push(...batch);
    if (!data?._links?.next || batch.length === 0) return { rows, complete: true };
  }
  return { rows, complete: false };
}

function statusMap(pipelines) {
  const names = new Map();
  const types = new Map();
  for (const pipeline of pipelines) {
    for (const status of pipeline._embedded?.statuses ?? []) {
      names.set(`${pipeline.id}:${status.id}`, canonicalStage(status.name));
      types.set(`${pipeline.id}:${status.id}`, status.type ?? 0);
    }
  }
  return { names, types };
}

function historyFrom(events, names, created) {
  const sorted = [...events].sort((a, b) => a.created_at - b.created_at || String(a.id).localeCompare(String(b.id)));
  const history = [];
  for (const event of sorted) {
    const before = event.value_before?.[0]?.lead_status;
    const after = event.value_after?.[0]?.lead_status;
    if (history.length === 0 && before) {
      history.push({
        stage: names.get(`${before.pipeline_id}:${before.id}`) ?? "",
        at: created,
        pipelineId: Number(before.pipeline_id),
      });
    }
    if (after) {
      history.push({
        stage: names.get(`${after.pipeline_id}:${after.id}`) ?? "",
        at: Number(event.created_at) * 1000,
        pipelineId: Number(after.pipeline_id),
      });
    }
  }
  return history.filter((event) => event.stage);
}

function writePrivate(file, value) {
  fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
  const tmp = `${file}.${process.pid}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(value), { mode: 0o600 });
  fs.renameSync(tmp, file);
  fs.chmodSync(file, 0o600);
}

const account = await amo("/account", { with: "datetime_settings,users_groups" });
const embedded = account?._embedded ?? {};
const settings = account?.datetime_settings ?? embedded.datetime_settings ?? {};
const timeZone = settings.timezone || "Europe/Moscow";
const timezoneConfirmed = Boolean(settings.timezone);
const weekKey = Object.keys(settings).find((key) => /week/i.test(key));
const weekStartConfirmed = weekKey != null && Number.isInteger(Number(settings[weekKey]));
const weekStartsOn = weekStartConfirmed ? Number(settings[weekKey]) : 1;
const now = Date.now();
const current = periodBounds("week", now, now, timeZone, weekStartsOn);
const week = periodBounds("week", current.from - 1, now, timeZone, weekStartsOn);
const pipelines = (await amo("/leads/pipelines"))?._embedded?.pipelines ?? [];
const fields = (await pages("/leads/custom_fields", "custom_fields", {}, 20)).rows;
const fieldProblems = catalogProblems(fields);
const stages = [...liveStageNames(pipelines)];
const { names, types } = statusMap(pipelines);
const linkField = fields.find((field) => field.name === "Ссылка на кассу");
let eventsAvailable = false;
let tasksAvailable = false;
try {
  await amo("/events", { "filter[entity]": "lead", "filter[type]": "lead_status_changed", limit: 1, page: 1 });
  eventsAvailable = true;
} catch (error) {
  if (error.status !== 403 && error.status !== 402 && error.status !== 404) throw error;
}
try {
  await amo("/tasks", { "filter[is_completed]": 1, limit: 1, page: 1 });
  tasksAvailable = true;
} catch (error) {
  if (error.status !== 403 && error.status !== 402 && error.status !== 404) throw error;
}
console.log(JSON.stringify({
  timeZone,
  weekStartConfirmed,
  weekStartsOn,
  accountKeys: Object.keys(account ?? {}),
  embeddedKeys: Object.keys(embedded),
  timezoneConfirmed,
  datetimeKeys: Object.keys(settings),
  fieldProblems,
  stages,
  eventsAvailable,
  tasksAvailable,
  linkField: Boolean(linkField),
  weekFrom: new Date(week.from).toISOString(),
  weekTo: new Date(week.to).toISOString(),
}, null, 0));
if (probeOnly && !process.argv.includes("--full")) {
  const sample = await pages("/leads", "leads", {
    "filter[pipeline_id]": REPORT_PIPELINES[0],
    "filter[closed_at][from]": Math.floor(week.from / 1000),
    "filter[closed_at][to]": Math.floor((week.to - 1) / 1000),
    with: "contacts",
  }, 1);
  const lead = sample.rows.find((item) => !item.is_deleted);
  let eventStatus = "no-lead";
  let taskStatus = "no-lead";
  if (lead && eventsAvailable) {
    const events = await amo("/events", { "filter[entity]": "lead", "filter[entity_id]": lead.id, "filter[type]": "lead_status_changed", limit: 5, page: 1 });
    const batch = events?._embedded?.events ?? [];
    eventStatus = batch.every((event) => Number(event.entity_id) === Number(lead.id)) ? `ok ${batch.length}` : "filter-ignored";
  }
  if (lead && tasksAvailable) {
    const tasks = await amo("/tasks", { "filter[entity_type]": "leads", "filter[entity_id]": lead.id, "filter[is_completed]": 1, limit: 5, page: 1 });
    const batch = tasks?._embedded?.tasks ?? [];
    taskStatus = batch.every((task) => Number(task.entity_id) === Number(lead.id)) ? `ok ${batch.length}` : "filter-ignored";
  }
  console.log(JSON.stringify({ sampleLeads: sample.rows.length, sampleComplete: sample.complete, eventStatus, taskStatus, tagKeys: Object.keys(lead?._embedded ?? {}) }));
  process.exit(0);
}

async function leadsBetween(field, fromMs, toMs) {
  const rows = [];
  let complete = true;
  for (const pipeline of REPORT_PIPELINES) {
    const page = await pages("/leads", "leads", {
      "filter[pipeline_id]": pipeline,
      [`filter[${field}][from]`]: Math.floor(fromMs / 1000),
      [`filter[${field}][to]`]: Math.floor((toMs - 1) / 1000),
      with: "contacts",
    }, 80);
    rows.push(...page.rows);
    complete &&= page.complete;
  }
  return { rows, complete };
}
async function leadsFor(field) {
  return leadsBetween(field, week.from, week.to);
}
async function openLeads() {
  const rows = [];
  let complete = true;
  for (const pipeline of REPORT_PIPELINES) {
    const statuses = (pipelines.find((item) => item.id === pipeline)?._embedded?.statuses ?? []).filter((status) => status.id !== WON && status.id !== LOST);
    const query = { with: "contacts" };
    statuses.forEach((status, index) => {
      query[`filter[statuses][${index}][pipeline_id]`] = pipeline;
      query[`filter[statuses][${index}][status_id]`] = status.id;
    });
    const page = await pages("/leads", "leads", query, 20);
    rows.push(...page.rows);
    complete &&= page.complete;
  }
  return { rows, complete };
}

const closedFetch = await leadsFor("closed_at");
const createdFetch = await leadsFor("created_at");
const byId = new Map();
for (const lead of [...closedFetch.rows, ...createdFetch.rows]) {
  if (!lead?.id || lead.is_deleted) continue;
  byId.set(lead.id, lead);
}
const fetchComplete = closedFetch.complete && createdFetch.complete;
console.log(JSON.stringify({ closedRows: closedFetch.rows.length, createdRows: createdFetch.rows.length, unique: byId.size, fetchComplete }));
if (!fetchComplete) {
  writePrivate(reviewPath, { partial: true, reason: "Список сделок оборвался, срез не опубликован", at: new Date().toISOString() });
  throw new Error("lead fetch incomplete");
}

const users = new Map();
const directory = await pages("/users", "users", {}, 10);
for (const user of directory.rows) if (user?.id && user.name) users.set(user.id, user.name);

let checkpoint = null;
if (fs.existsSync(checkpointPath)) {
  try {
    const saved = JSON.parse(fs.readFileSync(checkpointPath, "utf8"));
    if (saved.weekFrom === week.from && saved.weekTo === week.to && saved.leads) checkpoint = saved;
  } catch {
    checkpoint = null;
  }
}
if (!checkpoint) checkpoint = { weekFrom: week.from, weekTo: week.to, leads: {} };

async function entityPages(pathname, key, leadId, completed) {
  const query = { "filter[entity]": "lead", "filter[entity_id]": leadId, "filter[type]": "lead_status_changed" };
  if (pathname.endsWith("/tasks")) {
    delete query["filter[entity]"];
    delete query["filter[type]"];
    query["filter[entity_type]"] = "leads";
    query["filter[is_completed]"] = completed;
  }
  const result = await pages(pathname, key, query, 20);
  const foreign = result.rows.filter((row) => Number(row.entity_id) !== Number(leadId));
  return { rows: result.rows.filter((row) => Number(row.entity_id) === Number(leadId)), complete: result.complete && foreign.length === 0, ignored: foreign.length > 0 };
}

const CHAT_LOOKBACK_DAYS = 90;
const CHAT_PAGE_CAP = 180;
async function collectWazzup(ids, fromMs, toMs) {
  const wanted = new Set(ids.map((id) => Number(id)));
  const byLead = new Map();
  for (let page = 1; page <= CHAT_PAGE_CAP; page += 1) {
    const data = await amo("/events", {
      "filter[type][]": ["incoming_chat_message", "outgoing_chat_message"],
      "filter[created_at][from]": Math.floor(fromMs / 1000),
      "filter[created_at][to]": Math.floor(toMs / 1000),
      limit: 250,
      page,
    });
    const batch = data?._embedded?.events ?? [];
    for (const event of batch) {
      if (event.entity_type && event.entity_type !== "lead") continue;
      const id = Number(event.entity_id);
      if (!wanted.has(id)) continue;
      const list = byLead.get(id) ?? [];
      list.push(event);
      byLead.set(id, list);
    }
    if (page % 25 === 0) console.log(JSON.stringify({ chatPages: page, matched: byLead.size }));
    if (!data?._links?.next || batch.length === 0) return { byLead, complete: true };
  }
  return { byLead, complete: false };
}
function wazzupMessages(rows) {
  const messages = [];
  for (const event of rows) {
    if (event.type !== "incoming_chat_message" && event.type !== "outgoing_chat_message") continue;
    const origin = String(event.value_after?.[0]?.message?.origin ?? "");
    if (!origin.startsWith("com.wazzup24")) continue;
    const at = Number(event.created_at) * 1000;
    if (!Number.isFinite(at)) continue;
    messages.push({ at, dir: event.type === "incoming_chat_message" ? "in" : "out" });
  }
  return messages;
}
async function storeCard(id, allowDisable) {
  const savedLead = checkpoint.leads[id];
  const needsEvents = eventsAvailable && !savedLead?.eventsComplete;
  const needsTasks = tasksAvailable && !savedLead?.tasksComplete;
  if (savedLead && !needsEvents && !needsTasks) return "ready";
  let events = savedLead?.events ?? [];
  let eventsComplete = savedLead?.eventsComplete === true;
  let tasks = savedLead?.tasks ?? [];
  let tasksComplete = savedLead?.tasksComplete === true;
  if (!savedLead || needsEvents || needsTasks) {
    const eventPage = eventsAvailable ? await entityPages("/events", "events", id) : { rows: [], complete: false, ignored: false };
    const openTasks = tasksAvailable ? await entityPages("/tasks", "tasks", id, 0) : { rows: [], complete: false, ignored: false };
    const doneTasks = tasksAvailable ? await entityPages("/tasks", "tasks", id, 1) : { rows: [], complete: false, ignored: false };
    if (eventPage.ignored || openTasks.ignored || doneTasks.ignored) {
      if (allowDisable) {
        eventsAvailable = eventPage.ignored ? false : eventsAvailable;
        tasksAvailable = openTasks.ignored || doneTasks.ignored ? false : tasksAvailable;
      }
      console.log(JSON.stringify({ filterIgnored: true, events: eventPage.ignored, tasks: openTasks.ignored || doneTasks.ignored, allowDisable }));
      return "ignored";
    }
    events = eventPage.rows.map((event) => ({ id: event.id, created_at: event.created_at, value_before: event.value_before, value_after: event.value_after }));
    tasks = [...openTasks.rows, ...doneTasks.rows].map((task) => ({
      id: task.id, text: String(task.text ?? "Задача").replace(/\d{5,}/g, "••••").slice(0, 80), complete_till: task.complete_till, is_completed: task.is_completed,
      updated_at: task.updated_at, responsible_user_id: task.responsible_user_id, entity_id: task.entity_id,
    }));
    eventsComplete = eventPage.complete;
    tasksComplete = openTasks.complete && doneTasks.complete;
  }
  checkpoint.leads[id] = {
    events, tasks, eventsComplete, tasksComplete,
    messages: savedLead?.messages ?? [],
    messagesComplete: savedLead?.messagesComplete === true,
  };
  return "read";
}
async function readCards(ids, allowDisable) {
  let done = 0;
  for (const id of ids) {
    const status = await storeCard(id, allowDisable);
    if (status === "ignored") {
      writePrivate(checkpointPath, checkpoint);
      return false;
    }
    done += 1;
    if (done % 5 === 0) writePrivate(checkpointPath, checkpoint);
    if (done % 25 === 0) console.log(JSON.stringify({ progress: done, total: ids.length, allowDisable }));
  }
  writePrivate(checkpointPath, checkpoint);
  return true;
}
const weekIds = [...byId.keys()];
await readCards(weekIds, true);
const unread = weekIds.filter((id) => {
  const saved = checkpoint.leads[id];
  if (!saved) return eventsAvailable || tasksAvailable;
  return (eventsAvailable && !saved.eventsComplete) || (tasksAvailable && !saved.tasksComplete);
});
if (unread.length) {
  writePrivate(reviewPath, { partial: true, reason: "Карточки прочитаны не до конца, срез не опубликован", count: unread.length, at: new Date().toISOString() });
  throw new Error(`incomplete cards ${unread.length}`);
}
const chatFrom = week.from - CHAT_LOOKBACK_DAYS * 24 * 60 * 60 * 1000;
for (const id of weekIds) {
  const saved = checkpoint.leads[id];
  if (!saved) continue;
  saved.messages = [];
  saved.messagesComplete = false;
}
try {
  const journal = await collectWazzup(weekIds, chatFrom, now + 1000);
  if (journal.complete) {
    let leadsWithMessages = 0;
    for (const id of weekIds) {
      const saved = checkpoint.leads[id];
      if (!saved) continue;
      saved.messages = wazzupMessages(journal.byLead.get(Number(id)) ?? []);
      saved.messagesComplete = true;
      if (saved.messages.length) leadsWithMessages += 1;
    }
    console.log(JSON.stringify({ chat: "wazzup", leadsWithMessages }));
  } else {
    console.log(JSON.stringify({ chat: "partial" }));
  }
} catch (error) {
  console.log(JSON.stringify({ chat: "error", message: error.message }));
}
writePrivate(checkpointPath, checkpoint);
const archiveFrom = queueArchiveStart(now, timeZone);
const archiveFetch = await leadsBetween("closed_at", archiveFrom, now + 1000);
const openFetch = await openLeads();
const extraIds = [];
function takeLead(lead) {
  if (!lead?.id || lead.is_deleted || byId.has(lead.id)) return;
  byId.set(lead.id, lead);
  extraIds.push(lead.id);
}
if (archiveFetch.complete) for (const lead of archiveFetch.rows) takeLead(lead);
if (openFetch.complete) for (const lead of openFetch.rows) takeLead(lead);
let extraCards = true;
if (extraIds.length) {
  try {
    extraCards = await readCards(extraIds, false);
  } catch (error) {
    extraCards = false;
    console.log(JSON.stringify({ extraCards: "stopped", message: error.message }));
  }
}
console.log(JSON.stringify({
  archiveFrom: new Date(archiveFrom).toISOString(),
  archiveRows: archiveFetch.rows.length,
  archiveComplete: archiveFetch.complete,
  openRows: openFetch.rows.length,
  openComplete: openFetch.complete,
  extraCards,
}));

function build(lead) {
  const saved = checkpoint.leads[lead.id] ?? { events: [], tasks: [], eventsComplete: false, tasksComplete: false };
  const created = Number(lead.created_at) * 1000;
  const history = historyFrom(saved.events, names, created);
  const terminal = history.some((event) => event.stage === "Успех" || event.stage === "Провал");
  const closedStatus = lead.status_id === WON || lead.status_id === LOST;
  const historyComplete = saved.eventsComplete && (!closedStatus || terminal);
  const createdInTerminal = historyComplete && history.length > 0 && history.every((event) => event.stage === "Успех" || event.stage === "Провал");
  const link = linkField ? (lead.custom_fields_values ?? []).find((field) => field.field_id === linkField.id)?.values?.[0]?.value : null;
  const kassaNumber = kassaNumberFromLink(link, lead.id);
  const touch = classifyTouch({
    historyComplete, history, createdInTerminal, kassaCreated: false, priorOpenLead: false, priorLeadKnown: null,
  });
  const stage = names.get(`${lead.pipeline_id}:${lead.status_id}`) ?? "";
  return normalizeLead(lead, users, {
    history, historyComplete, touch: touch.touch, stage,
    unsorted: types.get(`${lead.pipeline_id}:${lead.status_id}`) === 1,
    kassaNumber: kassaNumber,
    tasks: saved.tasks.map((task) => ({
      id: task.id, name: task.text || "Задача", due: Number(task.complete_till) * 1000,
      done: task.is_completed ? Number(task.updated_at) * 1000 : null,
      responsibleId: task.responsible_user_id, completion: "updated_at",
    })),
    messages: saved.messagesComplete ? (saved.messages ?? []) : null,
  });
}

const deals = [...byId.values()].map(build);
const bounds = { from: week.from, to: week.to, cut: week.to };
const results = general(deals, "results", bounds, {});
const sources = general(deals, "sources", bounds, {});
const disagreements = [];
for (const deal of results.all) {
  const target = isTarget(deal, bounds.cut, "results");
  const qual = isQual(deal, bounds.cut, "results");
  if (results.target.some((row) => row.id === deal.id) !== target) disagreements.push({ id: deal.id, kind: "target" });
  if (results.qual.some((row) => row.id === deal.id) !== qual) disagreements.push({ id: deal.id, kind: "qual" });
  if ((deal.result === "Успех") !== results.success.some((row) => row.id === deal.id)) disagreements.push({ id: deal.id, kind: "success" });
  if ((deal.result === "Провал") !== results.fail.some((row) => row.id === deal.id)) disagreements.push({ id: deal.id, kind: "fail" });
}
if (results.success.length + results.fail.length !== results.all.length) disagreements.push({ id: 0, kind: "sum" });
const remoteClosed = closedFetch.rows.filter((lead) => {
  const at = Number(lead.closed_at) * 1000;
  return !lead.is_deleted && (lead.status_id === WON || lead.status_id === LOST) && at >= week.from && at < week.to && REPORT_PIPELINES.includes(lead.pipeline_id);
});
const remoteIds = new Set(remoteClosed.filter((lead) => !(lead._embedded?.tags ?? []).some((tag) => String(tag.name ?? "").trim().toUpperCase() === "ТЕСТ НЕ ОТПРАВЛЯТЬ")).map((lead) => lead.id));
for (const id of remoteIds) if (!results.all.some((deal) => deal.id === id)) disagreements.push({ id, kind: "нет в формуле" });
for (const deal of results.all) if (!remoteIds.has(deal.id)) disagreements.push({ id: deal.id, kind: "нет в фильтре amo" });

const checkbox = checkboxDisagreements(deals.filter((deal) => deal.closed != null && deal.closed >= week.from && deal.closed < week.to));
const reopenScan = await pages("/events", "events", {
  "filter[entity]": "lead", "filter[type]": "lead_status_changed",
  "filter[created_at][from]": Math.floor(week.from / 1000),
  "filter[created_at][to]": Math.floor((week.to - 1) / 1000),
}, 12);
const reopened = reopenScan.rows.find((event) => {
  const before = Number(event.value_before?.[0]?.lead_status?.id);
  const after = Number(event.value_after?.[0]?.lead_status?.id);
  return before === WON && after !== WON && after !== LOST;
});
let reopen = reopenGate({ scanComplete: reopenScan.complete, found: false, closedAtCleared: false });
if (reopened && reopenScan.complete) {
  const lead = await amo(`/leads/${reopened.entity_id}`);
  const openNow = lead && lead.status_id !== WON && lead.status_id !== LOST;
  const closedAtCleared = Boolean(openNow && (lead.closed_at == null || Number(lead.closed_at) === 0));
  reopen = reopenGate({ scanComplete: true, found: true, closedAtCleared });
  if (!reopen.verified) disagreements.push({ id: Number(reopened.entity_id), kind: "closed_at не снят после повторного открытия" });
}
const reopenVerified = reopen.verified;
const reopenNote = reopen.note;

let digest = null;
const digestTasks = await pages("/tasks", "tasks", { "filter[is_completed]": 0 }, 20);
if (digestTasks.complete) {
  const leadIds = [...new Set(digestTasks.rows.filter((task) => task.entity_type === "leads").map((task) => Number(task.entity_id)))];
  const digestLeads = [];
  let digestComplete = true;
  for (let index = 0; index < leadIds.length; index += 40) {
    const chunk = leadIds.slice(index, index + 40);
    const query = { with: "contacts" };
    chunk.forEach((id, position) => { query[`filter[id][${position}]`] = id; });
    const page = await pages("/leads", "leads", query, 3);
    if (!page.complete || page.rows.some((lead) => !chunk.includes(lead.id))) {
      digestComplete = false;
      break;
    }
    digestLeads.push(...page.rows);
  }
  if (digestComplete) {
    const known = new Map(deals.map((deal) => [deal.id, deal]));
    const taskByLead = new Map();
    for (const task of digestTasks.rows) {
      if (task.entity_type !== "leads") continue;
      const list = taskByLead.get(Number(task.entity_id)) ?? [];
      list.push({
        id: task.id, name: String(task.text ?? "Задача").replace(/\d{5,}/g, "••••").slice(0, 80),
        due: Number(task.complete_till) * 1000, done: task.is_completed ? Number(task.updated_at) * 1000 : null,
        responsibleId: task.responsible_user_id, leadMissing: false,
      });
      taskByLead.set(Number(task.entity_id), list);
    }
    const digestDeals = digestLeads.map((lead) => {
      const existing = known.get(lead.id);
      const stage = names.get(`${lead.pipeline_id}:${lead.status_id}`) ?? "";
      const built = existing ?? normalizeLead(lead, users, {
        history: [], historyComplete: false, touch: "unknown", stage,
        unsorted: types.get(`${lead.pipeline_id}:${lead.status_id}`) === 1,
        tasks: taskByLead.get(lead.id) ?? [],
      });
      return { ...built, tasks: taskByLead.get(lead.id) ?? built.tasks };
    });
    for (const task of digestTasks.rows) {
      if (task.entity_type === "leads" && !digestLeads.some((lead) => lead.id === Number(task.entity_id))) {
        digestDeals.push(normalizeLead({ id: 0, pipeline_id: 0, status_id: 0, custom_fields_values: [] }, users, {
          tasks: [{ id: task.id, due: Number(task.complete_till) * 1000, done: null, leadMissing: true }],
        }));
      }
    }
    const collected = digestOverdue(digestDeals, now);
    digest = {
      complete: true,
      tasks: collected.tasks.length,
      stages: collected.stages.length,
      missing: collected.missingDates.length,
      unavailable: collected.unavailableTasks.length,
    };
  }
}

const effect = funnel(results.all.filter((deal) => deal.lane === "off"), chain("effect", "off", new Set(stages)));
const logistics = funnel(results.all.filter((deal) => deal.lane === "on"), chain("logistics", "on", new Set(stages)));
const taskRows = overdueTasks(results.all);
const stageRows = overdueStages(results.all, now);
const kassaRead = await readKassaSales(week.from, week.to);
const kassa = kassaCases(deals, kassaRead.sales, week.from, week.to);
const kassaState = kassaStatus(kassaRead.ok, kassa);
const gate = acceptance({
  fieldProblems, eventsAvailable, tasksAvailable, timezoneConfirmed, weekStartConfirmed, reopenVerified,
  partial: false, discrepancies: disagreements.length, checkboxCount: checkbox.length, kassa: kassaState,
});
const meta = {
  builtAt: new Date(now).toISOString(),
  timeZone, timezoneConfirmed, weekStartsOn, weekStartConfirmed, fieldProblems, eventsAvailable, tasksAvailable,
  kassa: kassaState, accepted: gate.accepted, blockers: gate.blockers,
  week: {
    from: week.from, to: week.to,
    label: `${new Date(week.from).toLocaleDateString("ru-RU", { timeZone })} — ${new Date(week.to - 1).toLocaleDateString("ru-RU", { timeZone })}`,
    closed: results.all.length, created: sources.all.length, discrepancies: disagreements.length,
    checkbox: checkbox.length, duplicates: results.all.filter((deal) => deal.duplicate).length,
    unknownTouch: results.all.filter((deal) => deal.touch === "unknown").length,
    unknownOutcome: sources.unknown.length,
  },
  reopen: { verified: reopenVerified, note: reopenNote },
  coverage: {
    complete: true, closedFrom: week.from, closedTo: week.to, createdFrom: week.from, createdTo: week.to,
  },
  queueHistory: archiveFetch.complete && openFetch.complete ? "closed_at" : "not_archive",
  queueArchiveFrom: archiveFetch.complete && openFetch.complete ? archiveFrom : null,
  queueComplete: openFetch.complete,
  chat: weekIds.every((id) => checkpoint.leads[id]?.messagesComplete) ? "wazzup" : "missing",
  digest,
  liveStages: stages,
  chains: {
    effectOff: chain("effect", "off", new Set(stages)),
    effectOn: chain("effect", "on", new Set(stages)),
    logOff: chain("logistics", "off", new Set(stages)),
    logOn: chain("logistics", "on", new Set(stages)),
  },
  funnel: {
    effectRemainder: effect.remainder.length, effectIncomplete: effect.incomplete.length,
    logisticsRemainder: logistics.remainder.length, logisticsIncomplete: logistics.incomplete.length,
  },
  overdue: { tasks: taskRows.length, stages: stageRows.overdue.length, missingDue: stageRows.missing.length },
  qualityRow: {
    noSource: results.all.filter((deal) => deal.source === "Не указан").length,
    sourceConflict: results.all.filter((deal) => deal.sourceConflict).length,
    checkbox: checkbox.length,
    noType: results.all.filter((deal) => deal.pipelineId === REPORT_PIPELINES[0] && deal.lane === "unknown" && !deal.laneConflict).length,
    noReason: results.fail.filter((deal) => deal.reason === "Причина не указана").length,
    stageWithoutQual: results.all.filter((deal) => deal.historyComplete && deal.qualAt == null && deal.result !== "Успех" && deal.history.some((event) => ["Встреча назначена", "Передан на сборку", "Сертификат оплачен"].includes(event.stage))).length,
    missingDue: stageRows.missing.length,
    touchUnknown: results.all.filter((deal) => deal.touch === "unknown").length,
    laneConflict: results.all.filter((deal) => deal.laneConflict).length,
  },
  kassaCases: kassa,
  dictionary: true,
};
const snapshot = {
  meta,
  deals: deals.map((deal) => ({ ...deal, tasks: deal.tasks })),
};
writePrivate(snapshotPath, snapshot);
writePrivate(reviewPath, {
  at: meta.builtAt,
  discrepancies: disagreements,
  checkbox: checkbox.map((deal) => deal.id),
  duplicates: results.all.filter((deal) => deal.duplicate).map((deal) => deal.id),
  qualOutsideTarget: sources.qual.filter((deal) => !sources.target.some((row) => row.id === deal.id)).map((deal) => deal.id),
});
console.log(JSON.stringify({
  accepted: gate.accepted, blockers: gate.blockers, closed: results.all.length, created: sources.all.length,
  discrepancies: disagreements.length, checkbox: checkbox.length, duplicates: meta.week.duplicates,
  unknownTouch: meta.week.unknownTouch,
  kassa: kassaState, onlyKassa: kassa.onlyKassa.length, amoMissing: kassa.amoMissing.length, closedOpen: kassa.closedOpen.length,
  reopenVerified,
}));

async function readKassaSales(from, to) {
  if (!process.env.DATABASE_URL) return { ok: false, sales: [] };
  let sql;
  try {
    const postgres = (await import("postgres")).default;
    sql = postgres(process.env.DATABASE_URL, { max: 1 });
    const rows = await sql`select number, amo_lead_id, stage, ms_order_id, ms_demand_id, data from deals`;
    const sales = [];
    for (const row of rows) {
      const data = row.data && typeof row.data === "object" ? row.data : {};
      const sale = {
        ...data,
        number: row.number,
        stage: row.stage ?? data.stage,
        amoLeadId: data.amoLeadId ?? row.amo_lead_id ?? null,
        msOrderId: data.msOrderId ?? row.ms_order_id ?? null,
        msDemandId: data.msDemandId ?? row.ms_demand_id ?? null,
      };
      if (!isKassaWeekSuccess(sale, from, to)) continue;
      const comparable = kassaComparableSale(sale);
      if (comparable) sales.push(comparable);
    }
    return { ok: true, sales };
  } catch {
    return { ok: false, sales: [] };
  } finally {
    if (sql) await sql.end({ timeout: 1 }).catch(() => {});
  }
}
void DIGEST_PIPELINES;
void digestOverdue;
