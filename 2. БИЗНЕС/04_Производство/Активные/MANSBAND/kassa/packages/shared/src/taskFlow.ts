import type { Deal, DealKind, Task, TaskAssigneeRole, TaskKind, User } from "./types.js";

/** Продажа сохраняет вид на этапах обещания, доставки в магазин и резерва. */
export const HOLD_DEAL_KINDS: DealKind[] = ["sale", "company", "rental", "deferred", "promise"];
export const HOLD_STAGES = ["Дано обещание", "Ждет товар", "Товар в пути", "Товар в магазине", "Товар отложен"];
export const AUTOMATIC_HOLD_STAGES = ["Товар в пути", "Товар отложен"];
export function isHoldStage(stage: string): boolean { return HOLD_STAGES.includes(stage); }
export function defaultHoldUntil(paid: number, today = new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Moscow" }).format(new Date())): string {
  const date = new Date(`${today}T12:00:00Z`);
  date.setUTCDate(date.getUTCDate() + (paid > 0 ? 14 : 1));
  return date.toISOString().slice(0, 10);
}
export function holdStageError(deal: Pick<Deal, "kind" | "stage" | "comment" | "actualUntil" | "reservedUntil" | "deferredUntil" | "companyName" | "items">): string | null {
  if (!HOLD_DEAL_KINDS.includes(deal.kind) || !isHoldStage(deal.stage)) return null;
  if (deal.stage === "Дано обещание") {
    if (!deal.comment?.trim()) return "Для обещания нужен комментарий";
    if (!/^\d{4}-\d{2}-\d{2}$/.test(deal.actualUntil || "")) return "Укажите, до какого числа актуально обещание";
  }
  if (deal.kind === "company" && !deal.companyName?.trim()) return "Укажите наименование компании";
  if (deal.kind === "company" && !deal.items.length) return "Укажите товары компании";
  if (deal.stage === "Товар отложен" && !/^\d{4}-\d{2}-\d{2}$/.test(deal.reservedUntil || deal.deferredUntil || "")) return "Укажите срок отложки";
  return null;
}

/** Открытая заявка с истёкшим календарным сроком. Сегодня ещё не просрочка. */
export function isDealOverdue(deal: Deal, today: string): boolean {
  if (["Успех", "Провал", "Не слив"].includes(deal.stage)) return false;
  const deadline = deal.reservedUntil || deal.actualUntil || deal.deferredUntil || deal.validUntil || deal.rentalTo;
  return !!deadline && deadline.slice(0, 10) < today;
}

/** Ответственный в заявке или в незакрытой задаче; руководители видят всё. */
export function isDealRelevantToUser(deal: Deal, user: User | null, tasks: Task[]): boolean {
  if (!user) return false;
  if (user.role === "admin" || user.role === "rop") return true;
  const sameName = (value?: string) => !!value?.trim() && value.trim().toLowerCase() === user.name.trim().toLowerCase();
  if (sameName(deal.consultant) || sameName(deal.callManager)) return true;
  const role = user.role === "seller" ? "consultant" : user.role;
  return tasks.some((task) => task.dealNumber === deal.number && task.status === "pending"
    && task.assigneeRole === role
    && (role !== "consultant" || (!!user.store && task.store === user.store)));
}

/**
 * Формула задачи (созвон 05.08.2026, п.7–9): откуда появляется задача → кому
 * прилетает → что внутри → куда переходит заявка после выполнения.
 *
 * Этот реестр — единственный источник правды. По нему работает движок задач на
 * бэкенде (`services/taskFlow.ts`), подписи в очередях на фронте и документ
 * «ФОРМУЛА_ЗАДАЧ.md» для владельца. Добавляя вид задачи, правь только здесь.
 */

export type TaskTriggerSource =
  /** Заявку создали (этап роли не важен). */
  | "deal_created"
  /** Заявка встала на конкретный этап. */
  | "stage"
  /** Вышел срок в заявке (например «Резерв до»). */
  | "overdue"
  /** Предыдущая задача цепочки закрыта. */
  | "task_done"
  /** Только руками из карточки заявки или экрана очередей. */
  | "manual";

export interface TaskFlowRule {
  kind: TaskKind;
  /** Название задачи в очереди и в документе. */
  label: string;
  /** Подпись кнопки завершения («Готово», «Отправлено», «Принял»). */
  actionLabel: string;
  trigger: {
    source: TaskTriggerSource;
    /** Виды заявок, из которых задача рождается. Пусто — любой вид. */
    dealKinds?: DealKind[];
    /** Этап заявки для `source: "stage"`. */
    stage?: string;
    /** Поле заявки с датой, по просрочке которой рождается задача. */
    overdueField?: "reservedUntil" | "deferredUntil";
    /** Задача-предшественник для `source: "task_done"`. */
    afterKind?: TaskKind;
    /** Человеческая формулировка «откуда» — идёт в UI и документ. */
    description: string;
  };
  /**
   * Кому прилетает. `source_warehouse` — логисту, если товар едет с центрального
   * склада, иначе консультанту магазина-источника (ответственность на источнике).
   */
  assignee: TaskAssigneeRole | "source_warehouse" | "target_warehouse";
  /** Этап, на который заявка встаёт автоматически после выполнения задачи. */
  nextStage?: string;
  /** Задача, которая рождается следом. */
  followUp?: TaskKind;
  /** Что консультант делает внутри задачи. */
  inside?: string;
  note?: string;
}

const IN_STORE_STAGE = "Товар в магазине";
/** После «Отправлено»: товар едет, у получателя задача «Принять товар». */
export const IN_TRANSIT_STAGE = "Товар в пути";
/** Доставка: между «Собран» и «Отправлен» (правки владельца 10.08.2026, п.1.5). */
export const COURIER_STAGE = "Вызван курьер";

export const TASK_FLOW: TaskFlowRule[] = [
  {
    kind: "movement",
    label: "Сделать перемещение",
    actionLabel: "Товар в пути",
    trigger: {
      source: "stage",
      stage: "Ждет товар",
      // Продажа, компания и аренда сохраняют вид; старые виды поддерживаются.
      dealKinds: HOLD_DEAL_KINDS,
      description: "Отложка или обещание встала на этап «Ждет товар» / создано перемещение",
    },
    assignee: "source_warehouse",
    nextStage: IN_TRANSIT_STAGE,
    followUp: "movement_accept",
    inside: "Откуда и куда везём + позиции заявки; «Отправлено» → этап «Товар в пути» и задача приёмки у получателя",
    note: "При создании перемещения вид сохраняется, этап → «Ждет товар»",
  },
  {
    kind: "movement_accept",
    label: "Принять товар",
    actionLabel: "Товар принят",
    trigger: {
      source: "task_done",
      afterKind: "movement",
      description: "Отправитель отметил «Товар в пути»",
    },
    assignee: "target_warehouse",
    nextStage: IN_STORE_STAGE,
    inside: "Приёмка: документ в МойСклад → «Товар в магазине» → задача «Сделать отложку»",
  },
  {
    kind: "reserve",
    label: "Сделать отложку",
    actionLabel: "Отложил",
    trigger: {
      source: "stage",
      stage: IN_STORE_STAGE,
      // Отложка/обещание: товар уже на точке (без перемещения или после приёмки).
      dealKinds: HOLD_DEAL_KINDS,
      description: "Отложка/обещание на этапе «Товар в магазине» — отложить сейчас",
    },
    assignee: "consultant",
    nextStage: "Товар отложен",
    inside: "Скан позиций для перепроверки, затем «Отложил»; этап → «Товар отложен»",
  },
  {
    kind: "reserve_call",
    label: "Связаться по отложке",
    actionLabel: "Связался",
    trigger: {
      source: "overdue",
      stage: "Товар отложен",
      overdueField: "reservedUntil",
      description: "Срок отложки вышел",
    },
    assignee: "crm",
    followUp: "unreserve",
    inside: "Позвонить клиенту: продлеваем отложку или снимаем",
    note: "Продлил срок в заявке — задача снимается и отложка не разбирается",
  },
  {
    kind: "unreserve",
    label: "Убрать отложку",
    actionLabel: "Отложка убрана",
    trigger: {
      source: "overdue",
      stage: "Товар отложен",
      overdueField: "reservedUntil",
      description: "Крайняя дата отложки вышла — на следующий день в 10:00 по Москве",
    },
    assignee: "consultant",
    inside: "Разобрать отложку и вернуть комплект в зал магазина заявки",
    note: "Консультанту магазина заявки. Этап автоматически не меняем — решение за колл-менеджером",
  },
  {
    kind: "assemble_cdek",
    label: "Собрать для СДЭКа",
    actionLabel: "Заказ собран",
    trigger: {
      source: "stage",
      stage: "Передан на сборку",
      dealKinds: ["delivery"],
      description: "Доставку передали на сборку / этап сменили на «Передан на сборку»",
    },
    assignee: "consultant",
    nextStage: "Собран",
    inside: "Собрать заказ: скан позиций для перепроверки, затем «Заказ собран»",
  },
  {
    kind: "call_courier",
    label: "Вызвать курьера",
    actionLabel: "Вызвал",
    trigger: {
      source: "stage",
      stage: "Собран",
      dealKinds: ["delivery"],
      description: "Консультант собрал заказ",
    },
    assignee: "crm",
    nextStage: COURIER_STAGE,
    inside: "Вызвать курьера СДЭК на заказ",
  },
  {
    kind: "take_to_cdek",
    label: "Отнести в СДЭК",
    actionLabel: "Заказ отправлен",
    trigger: {
      source: "stage",
      stage: COURIER_STAGE,
      dealKinds: ["delivery"],
      description: "Курьер вызван",
    },
    assignee: "consultant",
    nextStage: "Отправлен",
    inside: "Сдать посылку в пункт СДЭК; затем «Заказ отправлен» — этап «Отправлен», позиции в расположение «СДЭК»",
  },
  {
    kind: "pickup_from_cdek",
    label: "Забрать с СДЭКа",
    actionLabel: "Забрал",
    trigger: {
      source: "stage",
      stage: "Не выкуплен",
      dealKinds: ["delivery"],
      description: "Клиент не выкупил заказ — этап «Не выкуплен»",
    },
    // Очередь консультанта магазина заявки (store = deal.store), не логист.
    assignee: "consultant",
    inside: "Забрать возврат из пункта СДЭК; позиции остаются в расположении «СДЭК» до приёмки в магазин",
  },
  {
    kind: "sary_send",
    label: "Перевести сарафан",
    actionLabel: "Перевел",
    trigger: {
      source: "deal_created",
      description:
        "Источник «Сарафан», указан телефон друга, бонус 1000 ₽ не списан в чеке — заявка в «Успех»",
    },
    assignee: "crm",
    inside: "Перевести 1000 ₽ на телефон из задачи и отметить «Перевел»",
  },
  {
    kind: "barcode",
    label: "Проштрихкодировать",
    actionLabel: "Готово",
    trigger: {
      source: "deal_created",
      description: "В заявке есть позиции без штрихкода",
    },
    assignee: "consultant",
    inside: "Наклеить штрихкод и сверить позицию с МойСклад",
  },
  {
    kind: "manual_check",
    label: "Проверить позицию вручную",
    actionLabel: "Проверил",
    trigger: {
      source: "deal_created",
      dealKinds: ["refund", "exchange"],
      description: "Возврат или обмен, где позицию не удалось найти по штрихкоду",
    },
    assignee: "consultant",
    inside: "Найти позицию в МойСклад и подтвердить возврат",
  },
];

const FLOW_BY_KIND = new Map<TaskKind, TaskFlowRule>(TASK_FLOW.map((rule) => [rule.kind, rule]));

export function taskFlowOf(kind: string): TaskFlowRule | undefined {
  return FLOW_BY_KIND.get(kind as TaskKind);
}

export function taskKindLabel(kind: string): string {
  return taskFlowOf(kind)?.label ?? kind;
}

/**
 * Единый формат названия задачи в любой очереди: «Действие (№ заявки)»
 * (правки владельца 10.08.2026, пп.4, 7–9). Без номера — только действие.
 */
export function taskTitle(kind: string, dealNumber?: number, suffix?: string): string {
  const base = taskKindLabel(kind);
  const withNumber = dealNumber ? `${base} (№${dealNumber})` : base;
  return suffix ? `${withNumber} · ${suffix}` : withNumber;
}

/** Подпись кнопки завершения задачи. */
export function taskActionLabel(kind: string): string {
  return taskFlowOf(kind)?.actionLabel ?? "Готово";
}

/** Правила, которые срабатывают при постановке заявки на этап. */
export function flowsForStage(stage: string, dealKind: DealKind): TaskFlowRule[] {
  return TASK_FLOW.filter(
    (rule) =>
      rule.trigger.source === "stage" &&
      rule.trigger.stage === stage &&
      (!rule.trigger.dealKinds || rule.trigger.dealKinds.includes(dealKind))
  );
}

/** Порядок очередей на экране задач — от самой загруженной роли к редкой. */
export const TASK_QUEUES: Array<{ role: TaskAssigneeRole; label: string; short: string }> = [
  { role: "consultant", label: "Очередь консультанта", short: "Консультант" },
  { role: "logist", label: "Очередь логиста", short: "Логист" },
  { role: "crm", label: "Очередь колл-менеджера", short: "Колл-менеджер" },
];

/** Виды задач, которые попадают в очередь роли (для подгрупп внутри очереди). */
export function taskKindsForRole(role: TaskAssigneeRole): TaskKind[] {
  const kinds = TASK_FLOW.filter((rule) => {
    if (rule.assignee === role) return true;
    // Перемещение и приёмка ходят и к логисту, и к консультанту — по складу.
    return (
      (rule.assignee === "source_warehouse" || rule.assignee === "target_warehouse") &&
      (role === "consultant" || role === "logist")
    );
  }).map((rule) => rule.kind);
  return [...new Set(kinds)];
}
