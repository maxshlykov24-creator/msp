// Расчёт статистики CRM и логистики MANSBAND.
// Пустые поля не достраиваются. Чекбокс «Целевой» число не меняет.

export const SALES = 9601214;
export const OFFLINE = 11482373;
export const ONLINE = 11482377;
export const REPORT_PIPELINES = [SALES, OFFLINE, ONLINE];
export const DIGEST_PIPELINES = [9601214, 9601246, 10730010, 11482373, 11482377, 11482381, 11482385];
export const WON = 142;
export const LOST = 143;
export const TEST_TAG = "ТЕСТ НЕ ОТПРАВЛЯТЬ";
export const SPAM = "Спам";
export const DUPLICATE_REASON = "Сдвоенная заявка";

export const FIELD_NAMES = {
  3434463: "Дата Взято в работу",
  3434465: "Дата Квалификация",
  3796755: "Целевой",
  3781223: "Источник",
  3134043: "Источник (техническое)",
  3134039: "Тип клиента",
  3134051: "Причина провала",
  3169911: "Сертификат действителен До",
  3279145: "Переход на след. этап до",
  3382311: "Дата встречи",
  3185681: "Примерная дата встречи / отправки",
  3815335: "Актуально до (обещание)",
  3810739: "Отложка до",
  3815337: "Аренда от",
  3322895: "Аренда до",
  3134989: "План. дата оплаты",
};

export const STAGE_DATE_FIELDS = {
  "Дано обещание": 3815335,
  "Товар отложен": 3810739,
  "Встреча назначена": 3382311,
  "Аренда оплачена": 3815337,
  "В аренде": 3322895,
  "Счет выставлен": 3134989,
  "Сертификат оплачен": 3169911,
};

const EFFECT = {
  off: ["Взято в работу", "Дано обещание", "Хочет прийти", "Ждет товар", "Товар в магазине", "Товар отложен", "Встреча назначена"],
  on: ["Взято в работу", "Дано обещание", "Хочет заказать", "Ждет товар", "Товар в магазине", "Товар отложен", "Передан на сборку"],
};
const LOGISTICS = {
  off: ["Дано обещание", "Ждет товар", "Товар в магазине"],
  on: ["Дано обещание", "Ждет товар", "Товар в магазине", "Передан на сборку", "Собран", "Отправлен", "Доставлен"],
};

export const QUALITY_SOURCES = {
  response: { available: false, reason: "Журнал сообщений Wazzup в этот срез не вошёл." },
};

export function classifyTouch({ historyComplete, history, createdInTerminal, kassaCreated, priorOpenLead, priorLeadKnown }) {
  if (priorOpenLead) return { touch: "yes", bindingError: true };
  const hadOpenStage = historyComplete && (history ?? []).some((event) => event.stage !== "Успех" && event.stage !== "Провал");
  if (hadOpenStage) return { touch: "yes", bindingError: false };
  if (createdInTerminal && kassaCreated === true && priorLeadKnown === false) return { touch: "no", bindingError: false };
  return { touch: "unknown", bindingError: false };
}

export function acceptance(input) {
  const blockers = [];
  if (input.fieldProblems?.length) blockers.push("Имена полей amo не совпали с формулами");
  if (!input.eventsAvailable) blockers.push("События amo не прочитаны");
  if (!input.tasksAvailable) blockers.push("Задачи amo не прочитаны");
  if (!input.timezoneConfirmed) blockers.push("Часовой пояс аккаунта amo не прочитан");
  if (!input.weekStartConfirmed) blockers.push("Первый день недели в аккаунте amo не прочитан");
  if (!input.reopenVerified) blockers.push("Повторное открытие не проверено на живой сделке");
  if (input.partial) blockers.push("Выборка amo оборвалась");
  if (input.discrepancies) blockers.push("Есть расхождения с фильтром amo");
  if (input.checkboxCount) blockers.push("Чекбокс «Целевой» расходится с датой взятия в работу");
  if (input.kassa === "mismatch") blockers.push("Касса за эту неделю расходится с amo");
  else if (input.kassa !== "checked") blockers.push("Касса за эту неделю не сверена");
  return { accepted: blockers.length === 0, blockers };
}

/** Полный поиск без события не блокирует бейдж. Оборванный поиск и неснятый closed_at блокируют. */
export function reopenGate({ scanComplete, found, closedAtCleared }) {
  if (!scanComplete) return { verified: false, note: "Поиск повторного открытия оборвался, правило не проверено" };
  if (!found) return { verified: true, note: "За закрытую неделю повторного открытия не было" };
  if (closedAtCleared) return { verified: true, note: "Повторно открытая сделка выпала из закрытых: closed_at пуст" };
  return { verified: false, note: "После выхода из успеха карточка не совпала с правилом" };
}

export function kassaStatus(readOk, cases) {
  if (!readOk) return "unavailable";
  const empty = !cases.onlyKassa.length && !cases.closedOpen.length && !cases.amoMissing.length;
  return empty ? "checked" : "mismatch";
}

function successInstant(sale) {
  if (sale.completedAt != null) {
    const direct = typeof sale.completedAt === "number" ? sale.completedAt : Date.parse(sale.completedAt);
    if (Number.isFinite(direct)) return direct;
  }
  const company = sale.kind === "company" && sale.stage === "Товар выдан" && sale.issued === true;
  const pattern = company ? /→ Успех|→ Товар выдан|товар выдан/i : /→ Успех/;
  const stamp = [...(sale.history ?? [])].reverse().find((row) => pattern.test(row.action ?? ""))?.at;
  const at = typeof stamp === "number" ? stamp : Date.parse(stamp ?? "");
  return Number.isFinite(at) ? at : null;
}

/** Успех кассы за неделю. Открытая отложка, провал, тест и непроведённое зеркало сюда не входят. */
export function isKassaWeekSuccess(sale, from, to) {
  if (!sale || sale.isTest) return false;
  if (typeof sale.id === "string" && sale.id.startsWith("amo-")) {
    const paid = sale.paid ?? 0;
    const paidLine = (sale.payments ?? []).some((payment) => payment.amount > 0);
    if (paid <= 0 && !paidLine && !sale.msOrderId && !sale.msDemandId) return false;
  }
  const success = sale.stage === "Успех" || (sale.kind === "company" && sale.stage === "Товар выдан" && sale.issued === true);
  if (!success) return false;
  const at = successInstant(sale);
  return at != null && at >= from && at < to;
}

export function kassaComparableSale(sale) {
  const at = successInstant(sale);
  if (at == null) return null;
  const lead = sale.amoLeadId == null || sale.amoLeadId === "" ? null : Number(sale.amoLeadId);
  return {
    number: Number(sale.number),
    amoLeadId: Number.isInteger(lead) ? lead : null,
    isTest: false,
    completedAt: at,
  };
}

/** Номер из ссылки кассы. Id сделки amo номером не становится, потолка в миллион нет. */
export function kassaNumberFromLink(link, leadId) {
  const numberMatch = typeof link === "string" ? link.match(/#board\/all\/all\/(\d+)/) : null;
  const number = numberMatch ? Number(numberMatch[1]) : null;
  return Number.isInteger(number) && number > 0 && number !== Number(leadId) ? number : null;
}

export const DICTIONARY = [
  { id: "all", name: "Все заявки", period: "Результаты: дата закрытия. Источники: дата создания.", source: "amoCRM", empty: "Ноль, если выборка пуста." },
  { id: "target", name: "Целевые", period: "Та же когорта.", source: "Дата Взято в работу 3434463 либо успех к границе периода.", empty: "Пустая дата не считается целевой. Чекбокс 3796755 не используется." },
  { id: "qual", name: "Квалы", period: "Та же когорта.", source: "Дата Квалификация 3434465 либо успех. Провал «Спам» квалом не считается.", empty: "Пустая дата не дорисовывается из истории этапов." },
  { id: "success", name: "Успех", period: "Результаты: текущий успех и дата закрытия. Источники: успех на конец периода по событиям.", source: "Статус 142.", empty: "Неполная история в источниках даёт «исход неизвестен», не текущий успех." },
  { id: "fail", name: "Провал", period: "Как у успеха.", source: "Статус 143 и причина 3134051.", empty: "Пустая причина остаётся «причина не указана»." },
  { id: "overdueTasks", name: "Просроченные задачи", period: "Задачи сделок, закрытых в периоде, включая уже выполненные.", source: "complete_till и updated_at выполненной задачи. Отдельного времени выполнения amo не отдаёт.", empty: "Нет задач — ноль. Не путать с открытыми задачами на сейчас." },
  { id: "overdueStages", name: "Просроченные этапы", period: "Закрытые в периоде, только сохранившийся срок.", source: "Поля STAGE_DATE_FIELDS и задачи. Поле 3279145 для прошлого этапа не используется.", empty: "Нет сохранённого срока — строка «срок не сохранился», не просрочка." },
  { id: "digest", name: "Сейчас просрочено", period: "Открытые сделки и задачи на момент среза.", source: "Те же правила, что ежедневная сводка моста, календарь Москвы.", empty: "Пропуски сводки показываются отдельно и не добиваются." },
  { id: "response", name: "Среднее время ответа", period: "Сделки, закрытые в выбранном периоде.", source: "Сообщения Wazzup за 90 дней до начала недели и до среза: от первого входящего в серии до следующего исходящего. В среднее входят минуты с 10:00 до 23:00 МСК, каждый день. С 23:00 до 10:00 счёт стоит. Звонки и прочие каналы не входят.", empty: "Нет пары входящее и ответ: прочерк, не ноль. Неотвеченная серия в среднее не входит." },
];

export function canonicalStage(name) {
  return String(name ?? "").replaceAll("Счёт", "Счет");
}

export function catalogProblems(fields) {
  const byId = new Map(fields.map((field) => [field.id, field.name]));
  const problems = [];
  for (const [id, name] of Object.entries(FIELD_NAMES)) {
    const live = byId.get(Number(id));
    if (live !== name) problems.push(`${id}: в каталоге «${live ?? "нет поля"}», ожидалось «${name}»`);
  }
  return problems;
}

export function liveStageNames(pipelines) {
  const names = new Set();
  for (const pipeline of pipelines) {
    if (!REPORT_PIPELINES.includes(pipeline.id)) continue;
    for (const status of pipeline._embedded?.statuses ?? []) names.add(canonicalStage(status.name));
  }
  return names;
}

export function chain(kind, lane, liveNames) {
  const source = (kind === "logistics" ? LOGISTICS : EFFECT)[lane] ?? [];
  const ordered = [...liveNames];
  const goods = ordered.filter((name) => name === "Товар в пути" || name === "Ждет товар");
  const used = new Set();
  const stages = [];
  for (const name of source) {
    const found = name === "Ждет товар" ? goods : (liveNames.has(name) ? [name] : []);
    for (const stage of found) {
      if (used.has(stage)) continue;
      used.add(stage);
      stages.push(stage);
    }
  }
  return stages;
}

const WEEKDAY = { Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6 };

export function zonedParts(ms, timeZone) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en-US", {
    timeZone, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", weekday: "short",
  }).formatToParts(new Date(ms)).map((part) => [part.type, part.value]));
  return {
    y: Number(parts.year), m: Number(parts.month), d: Number(parts.day),
    hh: Number(parts.hour), mm: Number(parts.minute), ss: Number(parts.second),
    weekday: WEEKDAY[parts.weekday],
  };
}

export function zonedTimeToUtc(y, m, d, hh, mm, ss, timeZone) {
  let utc = Date.UTC(y, m - 1, d, hh, mm, ss);
  for (let attempt = 0; attempt < 4; attempt += 1) {
    const zone = zonedParts(utc, timeZone);
    const got = Date.UTC(zone.y, zone.m - 1, zone.d, zone.hh, zone.mm, zone.ss);
    const want = Date.UTC(y, m - 1, d, hh, mm, ss);
    if (got === want) return utc;
    utc += want - got;
  }
  return utc;
}

function addDays(y, m, d, days) {
  const date = new Date(Date.UTC(y, m - 1, d + days));
  return { y: date.getUTCFullYear(), m: date.getUTCMonth() + 1, d: date.getUTCDate() };
}

export function periodBounds(kind, anchorMs, nowMs, timeZone, weekStartsOn = 1) {
  const zone = zonedParts(anchorMs, timeZone);
  let start = { y: zone.y, m: zone.m, d: zone.d };
  if (kind === "week") start = addDays(zone.y, zone.m, zone.d, -((zone.weekday - weekStartsOn + 7) % 7));
  if (kind === "month") start = { y: zone.y, m: zone.m, d: 1 };
  if (kind === "year") start = { y: zone.y, m: 1, d: 1 };
  const end = kind === "day" ? addDays(start.y, start.m, start.d, 1)
    : kind === "week" ? addDays(start.y, start.m, start.d, 7)
    : kind === "month" ? (start.m === 12 ? { y: start.y + 1, m: 1, d: 1 } : { y: start.y, m: start.m + 1, d: 1 })
    : { y: start.y + 1, m: 1, d: 1 };
  const from = zonedTimeToUtc(start.y, start.m, start.d, 0, 0, 0, timeZone);
  const to = zonedTimeToUtc(end.y, end.m, end.d, 0, 0, 0, timeZone);
  return { from, to, cut: Math.min(to, nowMs) };
}

function unixDate(value) {
  if (typeof value === "boolean" || value == null || value === "") return null;
  const number = Number(value);
  if (!Number.isFinite(number) || number <= 0) return null;
  return number > 10_000_000_000 ? number : number * 1000;
}

function fieldValue(lead, fieldId) {
  const field = (lead.custom_fields_values ?? []).find((item) => item.field_id === fieldId);
  const value = field?.values?.[0]?.value;
  if (value && typeof value === "object") return value.value ?? value.enum ?? null;
  return value ?? null;
}

function calendarDay(ms, timeZone) {
  const zone = zonedParts(ms, timeZone);
  return zone.y * 10000 + zone.m * 100 + zone.d;
}

export function normalizeLead(lead, users, extras = {}) {
  const tags = lead._embedded?.tags ?? [];
  const test = tags.some((tag) => String(tag.name ?? "").trim().toUpperCase() === TEST_TAG) || extras.kassaTest === true;
  const statusId = Number(lead.status_id);
  const result = statusId === WON ? "Успех" : statusId === LOST ? "Провал" : null;
  const closed = result ? unixDate(lead.closed_at) : null;
  const pipelineId = Number(lead.pipeline_id);
  const type = String(fieldValue(lead, 3134039) ?? "");
  const typeLane = type === "Оффлайн" ? "off" : type === "Онлайн" ? "on" : "unknown";
  const pipelineLane = pipelineId === OFFLINE ? "off" : pipelineId === ONLINE ? "on" : null;
  const lane = pipelineLane ?? typeLane;
  const laneConflict = pipelineLane != null && typeLane !== "unknown" && pipelineLane !== typeLane;
  const source = String(fieldValue(lead, 3781223) ?? "").trim();
  const technical = String(fieldValue(lead, 3134043) ?? "").trim();
  const reasonValue = fieldValue(lead, 3134051);
  const reason = result === "Провал" ? (String(reasonValue ?? "").trim() || "Причина не указана") : null;
  const history = (extras.history ?? []).map((event) => ({ stage: canonicalStage(event.stage), at: event.at }))
    .filter((event) => event.stage && Number.isFinite(event.at))
    .sort((a, b) => a.at - b.at);
  const checkbox = fieldValue(lead, 3796755);
  const ownerId = Number(lead.responsible_user_id);
  return {
    id: Number(lead.id),
    created: unixDate(lead.created_at) ?? 0,
    closed,
    result,
    pipelineId,
    stage: canonicalStage(extras.stage ?? ""),
    lane: laneConflict ? "unknown" : lane,
    laneConflict,
    source: source || "Не указан",
    sourceTechnical: technical || null,
    sourceConflict: Boolean(source && technical && source !== technical),
    touch: extras.touch ?? "unknown",
    takenAt: unixDate(fieldValue(lead, 3434463)),
    qualAt: unixDate(fieldValue(lead, 3434465)),
    targetFlag: checkbox == null || checkbox === "" ? null : checkbox === true || checkbox === 1 || checkbox === "1",
    reason,
    history,
    historyComplete: extras.historyComplete === true,
    tasks: extras.tasks ?? [],
    stageDue: stageDue(lead, extras.stage),
    stageDues: stageDues(lead),
    fallbackDue: unixDate(fieldValue(lead, 3279145)),
    test,
    kassaNumber: extras.kassaNumber ?? null,
    unsorted: extras.unsorted === true,
    duplicate: reason === DUPLICATE_REASON,
    owner: users.get(ownerId) ?? (ownerId ? `Сотрудник ${ownerId}` : "Не указан"),
    messages: Array.isArray(extras.messages)
      ? extras.messages.filter((item) => item && (item.dir === "in" || item.dir === "out") && Number.isFinite(Number(item.at))).map((item) => ({ at: Number(item.at), dir: item.dir }))
      : null,
  };
}

function readDue(lead, fieldId) {
  const due = unixDate(fieldValue(lead, fieldId));
  if (due == null) return null;
  if ((fieldId === 3382311 || fieldId === 3185681) && calendarDay(due, "Europe/Moscow") === 20010101) return null;
  return due;
}

function stageDue(lead, stage) {
  const name = canonicalStage(stage ?? "");
  const fieldId = STAGE_DATE_FIELDS[name];
  if (!fieldId) return null;
  const due = readDue(lead, fieldId);
  return due == null ? null : { stage: name, due, fieldId, dateOnly: true };
}

function stageDues(lead) {
  const dues = {};
  for (const [stage, fieldId] of Object.entries(STAGE_DATE_FIELDS)) {
    const due = readDue(lead, fieldId);
    if (due != null) dues[stage] = { stage, due, fieldId, dateOnly: true };
  }
  return dues;
}

export function successBy(deal, at) {
  if (!deal.historyComplete) return null;
  const stage = [...deal.history].reverse().find((event) => event.at <= at)?.stage ?? "";
  if (stage === "Успех") return true;
  if (stage === "Провал") return false;
  return null;
}

export function isTarget(deal, at, mode) {
  if (mode === "results") return deal.result === "Успех" || (deal.takenAt != null && deal.takenAt <= at);
  const success = successBy(deal, at);
  if (success === true) return true;
  return deal.takenAt != null && deal.takenAt <= at;
}

export function isQual(deal, at, mode) {
  if (mode === "results") {
    if (deal.result === "Успех") return true;
    if (deal.result === "Провал" && deal.reason === SPAM) return false;
    return deal.qualAt != null && deal.qualAt <= at;
  }
  if (successBy(deal, at) === true) return true;
  if (deal.qualAt == null || deal.qualAt > at) return false;
  if (deal.result === "Провал" && deal.reason === SPAM && deal.closed != null && deal.closed <= at) return false;
  return true;
}

export function outcomeAt(deal, at) {
  if (!deal.historyComplete) return "unknown";
  const success = successBy(deal, at);
  if (success === true) return "Успех";
  if (success === false) return "Провал";
  return "В работе";
}

export function cohort(deals, mode, bounds, filters = {}) {
  return deals.filter((deal) => {
    if (deal.test || !REPORT_PIPELINES.includes(deal.pipelineId)) return false;
    if (filters.touch && filters.touch !== "*" && deal.touch !== filters.touch) return false;
    if (filters.source && filters.source !== "*" && deal.source !== filters.source) return false;
    if (filters.lane && filters.lane !== "*" && deal.lane !== filters.lane) return false;
    if (mode === "results") return deal.closed != null && deal.result != null && deal.closed >= bounds.from && deal.closed < bounds.cut;
    return deal.created >= bounds.from && deal.created < bounds.cut;
  });
}

export function general(deals, mode, bounds, filters) {
  const rows = cohort(deals, mode, bounds, filters);
  const groups = {
    all: rows,
    target: rows.filter((deal) => isTarget(deal, bounds.cut, mode) && outcomeAt(deal, bounds.cut) !== "unknown"),
    qual: rows.filter((deal) => isQual(deal, bounds.cut, mode) && (mode === "results" || outcomeAt(deal, bounds.cut) !== "unknown" || (deal.qualAt != null && deal.qualAt <= bounds.cut))),
    success: [],
    fail: [],
    unknown: [],
  };
  if (mode === "results") {
    groups.success = rows.filter((deal) => deal.result === "Успех");
    groups.fail = rows.filter((deal) => deal.result === "Провал");
    groups.target = rows.filter((deal) => isTarget(deal, bounds.cut, mode));
    groups.qual = rows.filter((deal) => isQual(deal, bounds.cut, mode));
  } else {
    for (const deal of rows) {
      const outcome = outcomeAt(deal, bounds.cut);
      if (outcome === "Успех") groups.success.push(deal);
      else if (outcome === "Провал") groups.fail.push(deal);
      else if (outcome === "unknown") groups.unknown.push(deal);
    }
    groups.target = rows.filter((deal) => (deal.takenAt != null && deal.takenAt <= bounds.cut) || groups.success.includes(deal));
    groups.qual = rows.filter((deal) => groups.success.includes(deal) || (deal.qualAt != null && deal.qualAt <= bounds.cut && !(deal.result === "Провал" && deal.reason === SPAM && deal.closed != null && deal.closed <= bounds.cut)));
  }
  return groups;
}

export function qualOutsideTarget(groups, mode, bounds) {
  const targetIds = new Set(groups.target.map((deal) => deal.id));
  return groups.qual.filter((deal) => !targetIds.has(deal.id) && !(mode === "results" && deal.result === "Успех") && successBy(deal, bounds.cut) !== true);
}

export function checkboxDisagreements(deals) {
  return deals.filter((deal) => {
    if (deal.test || !REPORT_PIPELINES.includes(deal.pipelineId)) return false;
    const byDate = deal.result === "Успех" || deal.takenAt != null;
    if (deal.targetFlag == null) return false;
    return deal.targetFlag !== byDate;
  });
}

function taskOverdue(task, closed) {
  const done = task.done;
  const limit = done ?? closed;
  if (limit == null) return false;
  return task.due < limit;
}

export function overdueTasks(rows) {
  const records = [];
  for (const deal of rows) {
    for (const task of deal.tasks) records.push({ deal, task, overdue: taskOverdue(task, deal.closed) });
  }
  return records.filter((record) => record.overdue);
}

export function overdueStages(rows, nowMs) {
  const overdue = [];
  const missing = [];
  for (const deal of rows) {
    if (!deal.historyComplete) {
      missing.push({ deal, stage: deal.stage || "История неполная" });
      continue;
    }
    for (let index = 0; index < deal.history.length; index += 1) {
      const event = deal.history[index];
      if (event.stage === "Успех" || event.stage === "Провал") continue;
      const until = deal.history[index + 1]?.at ?? deal.closed ?? nowMs;
      const due = deal.stageDues?.[event.stage] ?? null;
      if (!due || due.fieldId === 3279145) {
        missing.push({ deal, stage: event.stage });
        continue;
      }
      const dueDay = calendarDay(due.due, "Europe/Moscow");
      const exitDay = calendarDay(until, "Europe/Moscow");
      if (exitDay > dueDay) overdue.push({ deal, stage: event.stage, due: due.due, until });
    }
  }
  return { overdue, missing };
}

export function digestOverdue(deals, nowMs) {
  const tasks = [];
  const stages = [];
  const missingDates = [];
  const unavailableTasks = [];
  for (const deal of deals) {
    if (deal.test || !DIGEST_PIPELINES.includes(deal.pipelineId) || deal.result != null) continue;
    for (const task of deal.tasks) {
      if (task.done != null) continue;
      if (task.leadMissing) unavailableTasks.push(task.id);
      else if (task.due < nowMs) tasks.push({ deal, task });
    }
    if (!deal.stage || deal.unsorted || deal.stage === "Неразобранное") continue;
    const fieldId = STAGE_DATE_FIELDS[deal.stage] ?? 3279145;
    const due = fieldId === deal.stageDue?.fieldId ? deal.stageDue.due : (fieldId === 3279145 ? deal.fallbackDue ?? null : null);
    if (due == null || calendarDay(due, "Europe/Moscow") < 20200101) {
      if (fieldId !== 3279145) missingDates.push(deal.id);
      continue;
    }
    if (calendarDay(due, "Europe/Moscow") < calendarDay(nowMs, "Europe/Moscow")) stages.push({ deal, due });
  }
  return { tasks, stages, missingDates, unavailableTasks };
}

export function funnel(rows, stages) {
  const goal = stages.at(-1) ?? "";
  const known = new Set(stages);
  const inChain = (event) => (event.pipelineId == null || REPORT_PIPELINES.includes(event.pipelineId)) && known.has(event.stage);
  const analysis = stages.map((stage) => {
    const visited = rows.filter((deal) => deal.historyComplete && deal.history.some((event) => inChain(event) && event.stage === stage));
    const hit = visited.filter((deal) => {
      const first = deal.history.find((event) => inChain(event) && event.stage === stage);
      return deal.history.some((event) => inChain(event) && event.stage === goal && event.at >= first.at);
    });
    return { stage, visited, hit, missed: visited.filter((deal) => !hit.includes(deal)) };
  });
  const incomplete = rows.filter((deal) => !deal.historyComplete);
  const remainder = rows.filter((deal) => deal.historyComplete && !deal.history.some((event) => inChain(event)));
  return { goal, analysis, remainder, incomplete };
}

export function queueAt(deals, at) {
  return deals.filter((deal) => !deal.test && REPORT_PIPELINES.includes(deal.pipelineId) && deal.created <= at && (deal.closed == null || deal.closed > at));
}

/** Этап на момент времени. Пустая строка, если история оборвана: текущий этап карточки сюда не подставляется. */
export function stageAt(deal, at) {
  if (!deal?.historyComplete || !deal.history?.length) return "";
  let stage = "";
  for (const event of deal.history) {
    if (event.at <= at && event.stage) stage = event.stage;
  }
  return stage;
}

/** Начало суток, с которых хватает закрытых сделок для queueHistory. */
export function queueArchiveStart(nowMs, timeZone, days = 14) {
  const today = zonedParts(nowMs, timeZone);
  const day = addDays(today.y, today.m, today.d, -days);
  return zonedTimeToUtc(day.y, day.m, day.d, 0, 0, 0, timeZone);
}

export function queueHistory(deals, nowMs, timeZone, days = 14) {
  const today = zonedParts(nowMs, timeZone);
  const points = [];
  for (let index = days; index >= 1; index -= 1) {
    const day = addDays(today.y, today.m, today.d, -index);
    const next = addDays(day.y, day.m, day.d, 1);
    const at = zonedTimeToUtc(next.y, next.m, next.d, 0, 0, 0, timeZone) - 1;
    const rows = queueAt(deals, at);
    points.push({ at, ids: rows.map((deal) => deal.id), incomplete: rows.some((deal) => !deal.historyComplete) });
  }
  return points;
}

export function periodCovered(mode, bounds, coverage) {
  if (!coverage?.complete) return false;
  const from = mode === "results" ? coverage.closedFrom : coverage.createdFrom;
  const to = mode === "results" ? coverage.closedTo : coverage.createdTo;
  return bounds.from >= from && bounds.cut <= to;
}

export function kassaCases(crmDeals, sales, from, to) {
  const crm = new Map(crmDeals.map((deal) => [deal.id, deal]));
  const onlyKassa = [];
  const closedOpen = [];
  const amoMissing = [];
  for (const sale of sales) {
    if (sale.isTest) continue;
    const at = sale.completedAt ?? sale.createdAt;
    if (at == null || at < from || at >= to) continue;
    if (sale.amoLeadId == null) {
      onlyKassa.push(sale.number);
      continue;
    }
    const deal = crm.get(sale.amoLeadId);
    if (!deal) amoMissing.push({ number: sale.number, leadId: sale.amoLeadId });
    else if (deal.result == null) closedOpen.push({ number: sale.number, leadId: sale.amoLeadId });
  }
  return { onlyKassa, closedOpen, amoMissing };
}

function shiftDay(y, m, d) {
  const date = new Date(Date.UTC(y, m - 1, d + 1));
  return { y: date.getUTCFullYear(), m: date.getUTCMonth() + 1, d: date.getUTCDate() };
}

/** Минуты внутри 10:00–23:00. С 23:00 до 10:00 интервал не растёт и продолжается на следующий день. */
export function workingMinutes(fromMs, toMs, timeZone = "Europe/Moscow") {
  if (!Number.isFinite(fromMs) || !Number.isFinite(toMs) || toMs <= fromMs) return 0;
  const end = zonedParts(toMs, timeZone);
  let cursor = zonedParts(fromMs, timeZone);
  let total = 0;
  for (let guard = 0; guard < 400; guard += 1) {
    const open = zonedTimeToUtc(cursor.y, cursor.m, cursor.d, 10, 0, 0, timeZone);
    const close = zonedTimeToUtc(cursor.y, cursor.m, cursor.d, 23, 0, 0, timeZone);
    const sliceFrom = Math.max(fromMs, open);
    const sliceTo = Math.min(toMs, close);
    if (sliceTo > sliceFrom) total += (sliceTo - sliceFrom) / 60000;
    if (cursor.y === end.y && cursor.m === end.m && cursor.d === end.d) break;
    cursor = shiftDay(cursor.y, cursor.m, cursor.d);
  }
  return total;
}

/** Минуты до ответа Wazzup внутри рабочего окна. Повторные входящие в той же серии таймер не сбрасывают. Исходящее без вопроса не считается. */
export function wazzupReplies(messages, timeZone = "Europe/Moscow") {
  const sorted = [...(messages ?? [])].filter((item) => item && (item.dir === "in" || item.dir === "out") && Number.isFinite(item.at)).sort((a, b) => a.at - b.at);
  const series = [];
  let waiting = null;
  for (const message of sorted) {
    if (message.dir === "in") {
      if (waiting == null) waiting = message.at;
    } else if (waiting != null) {
      series.push({ from: waiting, to: message.at, minutes: workingMinutes(waiting, message.at, timeZone) });
      waiting = null;
    }
  }
  return { minutes: series.map((item) => item.minutes), series, pending: waiting == null ? 0 : 1, pendingFrom: waiting };
}

export function responseSummary(deals, timeZone = "Europe/Moscow") {
  const minutes = [];
  const answered = [];
  let pending = 0;
  for (const deal of deals) {
    if (!Array.isArray(deal.messages)) continue;
    const stat = wazzupReplies(deal.messages, timeZone);
    pending += stat.pending;
    if (!stat.minutes.length) continue;
    minutes.push(...stat.minutes);
    answered.push(deal);
  }
  return {
    mean: minutes.length ? minutes.reduce((sum, value) => sum + value, 0) / minutes.length : null,
    count: minutes.length,
    pending,
    deals: answered,
  };
}

/** Порог для проверки: не ниже часа в рабочее время и выше типичного максимума по сделкам периода. */
export function slowReplyCutoff(dealMaxMinutes) {
  const values = dealMaxMinutes.filter((value) => Number.isFinite(value));
  if (!values.length) return 60;
  const mean = values.reduce((sum, value) => sum + value, 0) / values.length;
  return Math.max(60, mean * 1.5);
}

/** Сделки с ответом Wazzup: сортировка по самому долгому ответу, отдельно список на проверку. */
export function replyAudit(deals, timeZone = "Europe/Moscow") {
  const rows = [];
  for (const deal of deals) {
    if (!Array.isArray(deal.messages)) continue;
    const stat = wazzupReplies(deal.messages, timeZone);
    if (!stat.series.length && !stat.pending) continue;
    const max = stat.series.length ? Math.max(...stat.series.map((item) => item.minutes)) : null;
    rows.push({ deal, stat, max, pending: stat.pending > 0 });
  }
  const cutoff = slowReplyCutoff(rows.map((row) => row.max).filter((value) => value != null));
  const rank = (row) => (row.pending ? Number.POSITIVE_INFINITY : row.max ?? -1);
  rows.sort((a, b) => rank(b) - rank(a));
  const slow = rows.filter((row) => row.pending || (row.max != null && row.max >= cutoff));
  return { cutoff, rows, slow: slow.map((row) => row.deal), all: rows.map((row) => row.deal) };
}

export function medianHours(rows, stages) {
  const start = stages[0];
  const goal = stages.at(-1);
  if (!start || !goal) return { hours: null, ids: [] };
  const samples = [];
  for (const deal of rows) {
    if (!deal.historyComplete) continue;
    const from = deal.history.find((event) => event.stage === start);
    const to = from && deal.history.find((event) => event.stage === goal && event.at >= from.at);
    if (from && to) samples.push({ id: deal.id, hours: (to.at - from.at) / 3600000 });
  }
  samples.sort((a, b) => a.hours - b.hours);
  if (!samples.length) return { hours: null, ids: [] };
  const mid = Math.floor(samples.length / 2);
  const hours = samples.length % 2 ? samples[mid].hours : (samples[mid - 1].hours + samples[mid].hours) / 2;
  return { hours, ids: samples.map((sample) => sample.id) };
}
