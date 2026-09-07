import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowRightLeft, Check, Gift, ListPlus, Plus } from "lucide-react";
import {
  STORES,
  TASK_FLOW,
  TASK_QUEUES,
  taskActionLabel,
  taskKindLabel,
  taskKindsForRole,
  taskTitle,
} from "@kassa/shared";
import { api, USE_MOCK } from "../api/client";
import { useStore } from "../store";
import { Button, Modal } from "../components/ui";
import {
  assigneeLabel,
  CreateTaskForm,
  type CreatedTask,
  type TaskAssigneeRole,
} from "../components/CreateTaskForm";
import { MovementModal } from "../components/MovementModal";
import {
  positionsFromTaskMeta,
  routeFromTaskMeta,
  TaskDetail,
} from "../components/TaskDetail";
import { shortDate, timeOf } from "../lib/format";
import type { Deal } from "../data/types";
import { DealWorkspace } from "./DealWorkspace";
import { SaryScreen } from "./SaryScreen";

type CreateMode = "chooser" | "task" | "movement" | null;

type TaskStatus = "pending" | "done";
interface OperationTask {
  id: string;
  kind: string;
  title: string;
  store?: string;
  assigneeRole: TaskAssigneeRole;
  dealNumber?: number;
  status: TaskStatus;
  createdAt: string;
  createdBy?: string;
  metadata?: Record<string, unknown>;
}

const STORE_OPTIONS = [...STORES.filter((s) => s !== "Онлайн-магазин")];

/** Виды задач в порядке реестра — по нему группируем очередь роли. */
const KIND_ORDER = TASK_FLOW.map((rule) => rule.kind as string);

type QueueView = TaskAssigneeRole | "all";

function isQueueRole(value: string | undefined): value is TaskAssigneeRole {
  return TASK_QUEUES.some((queue) => queue.role === value);
}

function hashParts(): string[] {
  return window.location.hash.replace(/^#/, "").split("/");
}

function parseView(raw: string | undefined): QueueView {
  if (raw === "all") return "all";
  // Старый таб «Перемещения» → все задачи (фильтр по типу — отдельно).
  if (raw === "movements") return "all";
  if (isQueueRole(raw)) return raw;
  return "all";
}

const MOCK_TASKS: OperationTask[] = [
  {
    id: "t1",
    kind: "assemble_cdek",
    title: taskTitle("assemble_cdek", 1064),
    store: "На Бауманской",
    assigneeRole: "consultant",
    dealNumber: 1064,
    status: "pending",
    createdAt: "2026-07-22T09:10:00",
    createdBy: "Максим",
    metadata: { assigneeName: "Матвей" },
  },
  {
    id: "t2",
    kind: "movement",
    title: taskTitle("movement", 1070),
    assigneeRole: "logist",
    dealNumber: 1070,
    status: "pending",
    createdAt: "2026-07-21T16:30:00",
    createdBy: "Матвей",
    metadata: {
      assigneeName: "Илья",
      authorRole: "consultant",
      dealKind: "deferred",
      dealKindLabel: "Отложка",
      from: "На Бауманской",
      to: "На Новокузнецкой",
      positions: [
        {
          productId: "p1",
          quantity: 1,
          name: "Костюм Navy / 50",
          barcode: "2000000252667",
        },
      ],
    },
  },
];

const ROLE_SHORT: Record<string, string> = Object.fromEntries(
  TASK_QUEUES.map((q) => [q.role, q.short])
);

/** Кратко по позициям: имена товаров через « · » (не «N позиций»). */
function positionSummary(task: OperationTask): string | null {
  const list = positionsFromTaskMeta(task.metadata);
  const names = list.map((p) => p.name?.trim()).filter(Boolean) as string[];
  if (names.length === 0) return null;
  return names.join(" · ");
}

function isReserveOps(kind: string): boolean {
  return kind === "reserve" || kind === "reserve_call" || kind === "unreserve";
}

function isDeliveryOps(kind: string): boolean {
  return (
    kind === "assemble_cdek" ||
    kind === "call_courier" ||
    kind === "take_to_cdek" ||
    kind === "pickup_from_cdek"
  );
}

function isSaryOps(kind: string): boolean {
  return kind === "sary_send";
}

function moneyRub(amount: unknown): string | null {
  const n = typeof amount === "number" ? amount : Number(amount);
  if (!Number.isFinite(n) || n <= 0) return null;
  return `${n.toLocaleString("ru-RU")} ₽`;
}

function saryOpsTitle(task: OperationTask): string {
  const meta = task.metadata ?? {};
  const client = typeof meta.client === "string" ? meta.client.trim() : "";
  if (client) return client;
  const phone = typeof meta.phone === "string" ? meta.phone.trim() : "";
  if (phone) return phone;
  return task.title || "Сарафан";
}

function saryOpsMeta(task: OperationTask): string {
  const meta = task.metadata ?? {};
  const phone = typeof meta.phone === "string" ? meta.phone.trim() : "";
  const client = typeof meta.client === "string" ? meta.client.trim() : "";
  const amount = moneyRub(meta.amount) || "1 000 ₽";
  // Если в заголовке уже имя — в мета телефон; если заголовок телефон — имя не дублируем.
  const phoneBit = client && phone ? phone : null;
  return [amount, "Сарафан", phoneBit].filter(Boolean).join(" · ");
}

function deliveryOpsTitle(task: OperationTask): string {
  const meta = task.metadata ?? {};
  const client = typeof meta.client === "string" ? meta.client.trim() : "";
  if (client) return client;
  return positionSummary(task) || task.title || "Доставка";
}

function deliveryOpsMeta(task: OperationTask): string {
  const meta = task.metadata ?? {};
  const kind =
    typeof meta.dealKindLabel === "string" && meta.dealKindLabel.trim()
      ? meta.dealKindLabel.trim()
      : "Доставка";
  const pos = positionsFromTaskMeta(meta).length;
  return [task.store || null, kind, pos > 0 ? `${pos} поз.` : null].filter(Boolean).join(" · ");
}

function formatUntil(raw: unknown): string | null {
  if (typeof raw !== "string" || !raw.trim()) return null;
  const m = raw.trim().match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!m) return raw.trim();
  return `${m[3]}.${m[2]}.${m[1]}`;
}

/** Отложка: белым клиент/товар; серым — магазин · срок · вид. */
function reserveOpsTitle(task: OperationTask): string {
  const meta = task.metadata ?? {};
  const client = typeof meta.client === "string" ? meta.client.trim() : "";
  if (client) return client;
  return positionSummary(task) || task.title || "Отложка";
}

function reserveOpsMeta(task: OperationTask): string {
  const meta = task.metadata ?? {};
  const until = formatUntil(meta.reservedUntil);
  const consultant =
    typeof meta.consultant === "string" && meta.consultant.trim()
      ? meta.consultant.trim()
      : null;
  const kind =
    typeof meta.dealKindLabel === "string" && meta.dealKindLabel.trim()
      ? meta.dealKindLabel.trim()
      : task.kind === "unreserve" || task.kind === "reserve_call" || task.kind === "reserve"
        ? "Отложка"
        : null;
  const bits = [
    task.store || null,
    consultant,
    until
      ? task.kind === "reserve"
        ? `до ${until}`
        : `срок ${until}`
      : null,
    kind,
  ].filter(Boolean);
  return bits.join(" · ");
}

function dealKindLabelOf(task: OperationTask): string | null {
  const meta = task.metadata ?? {};
  if (typeof meta.dealKindLabel === "string" && meta.dealKindLabel.trim()) {
    return meta.dealKindLabel.trim();
  }
  const kind = typeof meta.dealKind === "string" ? meta.dealKind : "";
  if (kind === "deferred") return "Отложка";
  if (kind === "promise") return "Обещание";
  return null;
}

/**
 * Серые строки без шума:
 * маршрут → вид заявки → кто поставил / ответственный (если другой).
 */
function moveMetaLines(task: OperationTask): string[] {
  const meta = task.metadata ?? {};
  const route = routeFromTaskMeta(meta);
  const lines: string[] = [];
  if (route) lines.push(`${route.from} → ${route.to}`);

  const kind = dealKindLabelOf(task);
  if (kind) lines.push(kind);

  const roleKey =
    (typeof meta.sentByRole === "string" && meta.sentByRole) ||
    (typeof meta.authorRole === "string" && meta.authorRole) ||
    task.assigneeRole;
  const role = ROLE_SHORT[roleKey] ?? roleKey;

  if (task.kind === "movement_accept") {
    const sent =
      (typeof meta.sentBy === "string" && meta.sentBy.trim()) || task.createdBy?.trim() || "";
    if (sent) lines.push(`от ${role} (${sent})`);
    else if (role) lines.push(`от ${role}`);
    return lines;
  }

  const author = task.createdBy?.trim() || "";
  const responsible =
    typeof meta.assigneeName === "string" ? meta.assigneeName.trim() : "";

  if (author && responsible && author !== responsible) {
    lines.push(`поставил ${author} · ответственный ${responsible}`);
  } else if (author) {
    lines.push(`поставил ${author}`);
  } else if (responsible) {
    lines.push(`ответственный ${responsible}`);
  } else if (role) {
    lines.push(role);
  }
  return lines;
}

/** Одна задача в очереди — клик по строке открывает карточку задачи. */
function TaskRow({
  task,
  onComplete,
  onOpenDeal,
  onOpenTask,
  showQueue,
}: {
  task: OperationTask;
  onComplete: () => void;
  onOpenDeal: (dealNumber: number) => void;
  onOpenTask: () => void;
  showQueue?: boolean;
}) {
  const needsSetup = task.kind === "movement" && task.metadata?.needsSetup === true;
  const isMove = task.kind === "movement" || task.kind === "movement_accept";
  const isReserve = isReserveOps(task.kind);
  const isDelivery = isDeliveryOps(task.kind);
  const isSary = isSaryOps(task.kind);
  const needsCardScan = isMove || task.kind === "assemble_cdek";
  const compact = isMove || isReserve || isDelivery || isSary;
  const route = routeFromTaskMeta(task.metadata);
  const posCount = positionsFromTaskMeta(task.metadata).length;
  const queueShort = TASK_QUEUES.find((q) => q.role === task.assigneeRole)?.short;
  const who = assigneeLabel(task);

  // Перемещение / отложка / СДЭК: белым суть; серым мета (вид уже в заголовке секции).
  const compactTitle = isMove
    ? positionSummary(task) || task.title || "Без названия"
    : isReserve
      ? reserveOpsTitle(task)
      : isDelivery
        ? deliveryOpsTitle(task)
        : isSary
          ? saryOpsTitle(task)
          : task.title;
  const compactMeta = isMove
    ? moveMetaLines(task).join(" · ")
    : isReserve
      ? reserveOpsMeta(task)
      : isDelivery
        ? deliveryOpsMeta(task)
        : isSary
          ? saryOpsMeta(task)
          : "";

  const metaBits = [
    showQueue && queueShort ? queueShort : null,
    task.store || null,
    who,
    route ? `${route.from} → ${route.to}` : null,
    posCount > 0 ? `${posCount} поз.` : null,
  ].filter(Boolean);

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onOpenTask}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onOpenTask();
        }
      }}
      className="rounded-lg border border-ink-700/80 px-3 py-2.5 flex flex-wrap items-center gap-x-3 gap-y-2 cursor-pointer hover:border-ink-600 hover:bg-ink-900/40 transition-colors"
    >
      <div className="flex-1 min-w-[200px]">
        <div className="text-white font-medium text-[14px]">{compact ? compactTitle : task.title}</div>
        {compact ? (
          compactMeta && (
            <div className="text-[12px] text-mute mt-0.5 truncate">{compactMeta}</div>
          )
        ) : (
          metaBits.length > 0 && (
            <div className="text-[12px] text-mute mt-0.5 truncate">{metaBits.join(" · ")}</div>
          )
        )}
        {needsSetup && (
          <div className="text-[12px] text-amber-300/90 mt-0.5">Укажите откуда / куда / позиции в заявке</div>
        )}
      </div>
      {!compact && (
        <span className="text-[11px] text-mute tabular-nums shrink-0">
          {shortDate(task.createdAt)} {timeOf(task.createdAt)}
        </span>
      )}
      {task.dealNumber != null && (
        <Button
          variant="subtle"
          onClick={(e) => {
            e.stopPropagation();
            onOpenDeal(task.dealNumber!);
          }}
        >
          Заявка #{task.dealNumber}
        </Button>
      )}
      {/* Скан-задачи — только через карточку; остальное можно закрыть из списка. */}
      {task.status === "pending" && !needsSetup && !needsCardScan && (
        <Button
          variant="subtle"
          onClick={(e) => {
            e.stopPropagation();
            onComplete();
          }}
        >
          <Check size={15} /> {taskActionLabel(task.kind)}
        </Button>
      )}
    </div>
  );
}

export function TasksMovements() {
  const { activeStore, deals } = useStore();
  const initialHash = hashParts();
  const [view, setView] = useState<QueueView>(parseView(initialHash[1]));
  const [tasks, setTasks] = useState<OperationTask[]>(USE_MOCK ? MOCK_TASKS : []);
  const [status, setStatus] = useState(
    initialHash[2] === "done" || initialHash[2] === "all" ? initialHash[2] : "pending"
  );
  const [kindFilter, setKindFilter] = useState("");
  const [queueFilter, setQueueFilter] = useState<"" | TaskAssigneeRole>("");
  const [createMode, setCreateMode] = useState<CreateMode>(null);
  const [openTask, setOpenTask] = useState<OperationTask | null>(null);
  const [openDeal, setOpenDeal] = useState<Deal | null>(null);
  const [dealLoading, setDealLoading] = useState(false);
  const [dealError, setDealError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [createStore, setCreateStore] = useState(
    STORE_OPTIONS.includes(activeStore as (typeof STORE_OPTIONS)[number])
      ? activeStore
      : STORE_OPTIONS[0] || "На Бауманской"
  );

  async function openDealByNumber(dealNumber: number) {
    setDealError(null);
    setOpenDeal(null);
    const cached = deals.find((d) => d.number === dealNumber);
    if (cached) {
      setOpenDeal(cached);
      return;
    }
    if (USE_MOCK) {
      setDealError(`Заявка #${dealNumber} не найдена в списке`);
      return;
    }
    setDealLoading(true);
    try {
      const deal = await api.get<Deal | null>(`/deals/${dealNumber}`);
      if (!deal || typeof deal !== "object" || !("number" in deal)) {
        setDealError(`Заявка #${dealNumber} не найдена`);
        return;
      }
      setOpenDeal(deal);
    } catch (err) {
      setDealError(err instanceof Error ? err.message : `Заявка #${dealNumber} не найдена`);
    } finally {
      setDealLoading(false);
    }
  }

  const load = useCallback(async () => {
    if (USE_MOCK) return;
    const taskRows = await api.get<OperationTask[]>("/tasks").catch(() => null);
    if (!taskRows) return;
    setTasks(taskRows);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Табы очередей живут в хэше: #tasks/consultant/pending — ссылку можно послать.
  useEffect(() => {
    const next = `tasks/${view}/${status}`;
    if (window.location.hash.replace(/^#/, "") !== next) window.location.hash = next;
  }, [view, status]);

  // Смена очереди сбрасывает фильтр вида и доп. фильтр очереди.
  useEffect(() => {
    setKindFilter("");
    setQueueFilter("");
  }, [view]);

  const kindOptions = useMemo(() => {
    const notSary = (k: string) => k !== "sary_send";
    if (view === "all") {
      const present = new Set(tasks.map((t) => t.kind));
      return KIND_ORDER.filter((k) => notSary(k) && present.has(k)).map((k) => ({
        value: k,
        label: taskKindLabel(k),
      }));
    }
    const forRole = taskKindsForRole(view).filter(notSary);
    const present = new Set(
      tasks.filter((t) => t.assigneeRole === view && !isSaryOps(t.kind)).map((t) => t.kind)
    );
    // Показываем виды роли; сначала те, что реально есть в очереди.
    const ordered = [
      ...KIND_ORDER.filter((k) => forRole.includes(k as (typeof forRole)[number]) && present.has(k)),
      ...KIND_ORDER.filter((k) => forRole.includes(k as (typeof forRole)[number]) && !present.has(k)),
    ];
    return ordered.map((k) => ({ value: k, label: taskKindLabel(k) }));
  }, [view, tasks]);

  /** Задачи выбранной очереди (или все), с фильтром по виду. Сары — только в блоке ниже. */
  const groups = useMemo(() => {
    const rows = tasks.filter((task) => {
      if (isSaryOps(task.kind)) return false;
      if (view !== "all" && task.assigneeRole !== view) return false;
      if (view === "all" && queueFilter && task.assigneeRole !== queueFilter) return false;
      if (status !== "all" && task.status !== status) return false;
      if (kindFilter && task.kind !== kindFilter) return false;
      return true;
    });
    const byKind = new Map<string, OperationTask[]>();
    for (const task of rows) byKind.set(task.kind, [...(byKind.get(task.kind) ?? []), task]);
    return [...byKind.entries()].sort((a, b) => {
      const ai = KIND_ORDER.indexOf(a[0]);
      const bi = KIND_ORDER.indexOf(b[0]);
      return (ai < 0 ? 99 : ai) - (bi < 0 ? 99 : bi);
    });
  }, [tasks, status, view, kindFilter, queueFilter]);

  const queueCounts = useMemo(() => {
    const counts = new Map<string, number>();
    let allPending = 0;
    for (const task of tasks) {
      if (task.status !== "pending" || isSaryOps(task.kind)) continue;
      allPending += 1;
      counts.set(task.assigneeRole, (counts.get(task.assigneeRole) ?? 0) + 1);
    }
    counts.set("all", allPending);
    return counts;
  }, [tasks]);

  const kindCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const task of tasks) {
      if (isSaryOps(task.kind)) continue;
      if (view !== "all" && task.assigneeRole !== view) continue;
      if (view === "all" && queueFilter && task.assigneeRole !== queueFilter) continue;
      if (status !== "all" && task.status !== status) continue;
      counts.set(task.kind, (counts.get(task.kind) ?? 0) + 1);
    }
    return counts;
  }, [tasks, view, status, queueFilter]);

  const total = groups.reduce((sum, [, rows]) => sum + rows.length, 0);

  async function complete(id: string) {
    setActionError(null);
    const prev = tasks;
    setTasks((list) => list.map((task) => (task.id === id ? { ...task, status: "done" } : task)));
    if (USE_MOCK) {
      const done = prev.find((t) => t.id === id);
      if (done?.kind === "movement") {
        const meta = done.metadata ?? {};
        const toName = String(meta.to ?? "");
        const acceptStore =
          /бауман/i.test(toName)
            ? "На Бауманской"
            : /новокузнецк|пятниц/i.test(toName)
              ? "На Пятницкой"
              : toName || done.store || "";
        const pos = Array.isArray(meta.positions) ? meta.positions : [];
        const acceptNames = pos
          .map((p) =>
            typeof p === "object" && p && "name" in p
              ? String((p as { name?: string }).name || "").trim()
              : typeof p === "string"
                ? p.trim()
                : ""
          )
          .filter(Boolean);
        const acceptTitle =
          acceptNames.length > 0
            ? acceptNames.join(" · ")
            : taskTitle("movement_accept", done.dealNumber);
        setTasks((list) => [
          {
            id: `${id}:accept`,
            kind: "movement_accept",
            title: acceptTitle,
            store: acceptStore,
            assigneeRole: /центральн/i.test(toName) ? "logist" : "consultant",
            dealNumber: done.dealNumber,
            status: "pending",
            createdAt: new Date().toISOString(),
            createdBy: "система",
            metadata: { ...meta, sourceTaskId: id, acceptStore },
          },
          ...list,
        ]);
      }
      return;
    }
    try {
      await api.patch(`/tasks/${id}/complete`, {});
      await load();
    } catch (err) {
      setTasks(prev);
      setActionError(err instanceof Error ? err.message : "Не удалось закрыть задачу");
      await load();
    }
  }

  function onTaskCreated(task: CreatedTask) {
    setTasks((prev) => [
      {
        id: task.id,
        kind: task.kind,
        title: task.title,
        store: task.store,
        assigneeRole: task.assigneeRole,
        dealNumber: task.dealNumber,
        status: task.status,
        createdAt: task.createdAt,
        createdBy: task.createdBy,
        metadata: task.metadata,
      },
      ...prev,
    ]);
    setCreateMode(null);
  }

  function onMovementCreated() {
    setCreateMode(null);
    void load();
  }

  const viewLabel =
    view === "all" ? "Все очереди" : TASK_QUEUES.find((queue) => queue.role === view)?.label ?? "";

  return (
    <div className="max-w-5xl mx-auto">
      <div className="flex flex-wrap items-start justify-between gap-3 mb-5">
        <div>
          <h1 className="text-2xl font-extrabold text-white">Задачи</h1>
          <p className="text-mute text-sm mt-1">
            Консультант, логист и call-менеджер · перемещения, отложка, СДЭК, штрихкоды
          </p>
        </div>
        <Button
          onClick={() => {
            setCreateStore(
              STORE_OPTIONS.includes(activeStore as (typeof STORE_OPTIONS)[number])
                ? activeStore
                : STORE_OPTIONS[0] || "На Бауманской"
            );
            setCreateMode("chooser");
          }}
        >
          <Plus size={16} /> Новая задача
        </Button>
      </div>

      <div className="flex flex-wrap items-center gap-2 mb-4">
        <div className="inline-flex border border-ink-700 rounded-lg overflow-hidden flex-wrap">
          <button
            className={`px-4 py-2 text-sm ${view === "all" ? "bg-gold text-ink-950 font-semibold" : "text-mute hover:text-white"}`}
            onClick={() => setView("all")}
          >
            Все
            {(queueCounts.get("all") ?? 0) > 0 && (
              <span className="ml-2 opacity-80">{queueCounts.get("all")}</span>
            )}
          </button>
          {TASK_QUEUES.map((queue) => {
            const count = queueCounts.get(queue.role) ?? 0;
            return (
              <button
                key={queue.role}
                className={`px-4 py-2 text-sm ${view === queue.role ? "bg-gold text-ink-950 font-semibold" : "text-mute hover:text-white"}`}
                onClick={() => setView(queue.role)}
              >
                {queue.short}
                {count > 0 && <span className="ml-2 opacity-80">{count}</span>}
              </button>
            );
          })}
        </div>
      </div>

      <div className="card p-3 mb-3 flex flex-wrap items-center gap-2">
        <select className="input py-2 text-sm w-auto" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="pending">К выполнению</option>
          <option value="done">Выполненные</option>
          <option value="all">Все статусы</option>
        </select>
        {view === "all" && (
          <select
            className="input py-2 text-sm w-auto"
            value={queueFilter}
            onChange={(e) => setQueueFilter(e.target.value as "" | TaskAssigneeRole)}
          >
            <option value="">Все очереди</option>
            {TASK_QUEUES.map((queue) => (
              <option key={queue.role} value={queue.role}>
                {queue.short}
              </option>
            ))}
          </select>
        )}
        <select
          className="input py-2 text-sm w-auto min-w-[180px]"
          value={kindFilter}
          onChange={(e) => setKindFilter(e.target.value)}
        >
          <option value="">Все типы</option>
          {kindOptions.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
              {kindCounts.has(opt.value) ? ` (${kindCounts.get(opt.value)})` : ""}
            </option>
          ))}
        </select>
        <span className="text-[12px] text-mute">
          {viewLabel} · задач: {total}
        </span>
      </div>

      {actionError && (
        <div className="mb-3 rounded-lg border border-amber-400/40 bg-amber-400/10 px-3 py-2 text-[13px] text-amber-100">
          {actionError}
        </div>
      )}

      {/* Быстрые чипы типов — чтобы логист одним кликом взял перемещения. */}
      {kindOptions.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mb-4">
          <button
            type="button"
            onClick={() => setKindFilter("")}
            className={`px-3 py-1.5 rounded-lg text-[13px] font-medium ${
              !kindFilter
                ? "bg-gold/15 text-gold-soft border border-gold/40"
                : "text-mute hover:bg-ink-800 border border-transparent"
            }`}
          >
            Все типы
          </button>
          {kindOptions.map((opt) => {
            const count = kindCounts.get(opt.value) ?? 0;
            return (
              <button
                key={opt.value}
                type="button"
                onClick={() => setKindFilter(opt.value)}
                className={`px-3 py-1.5 rounded-lg text-[13px] font-medium ${
                  kindFilter === opt.value
                    ? "bg-gold/15 text-gold-soft border border-gold/40"
                    : "text-mute hover:bg-ink-800 border border-transparent"
                }`}
              >
                {opt.label}
                {count > 0 && <span className="ml-1.5 opacity-70">{count}</span>}
              </button>
            );
          })}
        </div>
      )}

      <div className="space-y-5">
        {groups.map(([kind, rows]) => (
            <section key={kind}>
              <div className="flex items-baseline gap-2 mb-1.5">
                <h2 className="text-white font-semibold text-[15px]">{taskKindLabel(kind)}</h2>
                <span className="text-[12px] text-mute">{rows.length}</span>
              </div>
              <div className="space-y-1.5">
                {rows.map((task) => (
                  <TaskRow
                    key={task.id}
                    task={task}
                    showQueue={view === "all"}
                    onOpenTask={() => setOpenTask(task)}
                    onOpenDeal={(n) => void openDealByNumber(n)}
                    onComplete={() => void complete(task.id)}
                  />
                ))}
              </div>
            </section>
        ))}
        {total === 0 && <div className="card py-10 text-center text-mute">Задач нет</div>}
      </div>

      {view === "crm" && (
        <section className="mt-8 border-t border-ink-700 pt-6">
          <div className="flex items-center gap-2 mb-1">
            <Gift className="text-gold" size={18} />
            <h2 className="text-lg font-bold text-white">Сары · реферальные выплаты</h2>
          </div>
          <p className="text-mute text-[13px] mb-4">
            Пачки по дню: отметьте галочками, отправьте переводы и приложите реальный скрин — без
            файла отметить отправленными нельзя.
          </p>
          <SaryScreen embedded />
        </section>
      )}

      <Modal open={createMode === "chooser"} onClose={() => setCreateMode(null)} title="Новая задача">
        <div className="space-y-2">
          <button
            type="button"
            className="w-full rounded-xl border border-ink-700 bg-ink-900/50 px-4 py-3.5 text-left hover:border-gold/40 hover:bg-ink-800/60 transition-colors"
            onClick={() => setCreateMode("movement")}
          >
            <div className="flex items-center gap-3">
              <ArrowRightLeft size={18} className="text-gold shrink-0" />
              <div>
                <div className="text-white font-semibold text-[14px]">Создать перемещение</div>
                <div className="text-[12px] text-mute mt-0.5">Без заявки · товар, откуда / куда</div>
              </div>
            </div>
          </button>
          <button
            type="button"
            className="w-full rounded-xl border border-ink-700 bg-ink-900/50 px-4 py-3.5 text-left hover:border-gold/40 hover:bg-ink-800/60 transition-colors"
            onClick={() => setCreateMode("task")}
          >
            <div className="flex items-center gap-3">
              <ListPlus size={18} className="text-gold shrink-0" />
              <div>
                <div className="text-white font-semibold text-[14px]">Ручная задача</div>
                <div className="text-[12px] text-mute mt-0.5">Свободный текст · очередь и магазин</div>
              </div>
            </div>
          </button>
        </div>
      </Modal>

      <Modal open={createMode === "task"} onClose={() => setCreateMode(null)} title="Ручная задача">
        <CreateTaskForm simple compact defaultStore={createStore} onCreated={onTaskCreated} />
      </Modal>

      <MovementModal
        open={createMode === "movement"}
        onClose={() => setCreateMode(null)}
        store={createStore}
        allowCatalogPick
        onCreated={onMovementCreated}
      />

      <Modal
        open={!!openTask}
        onClose={() => setOpenTask(null)}
        title="Задача"
        wide
      >
        {openTask && (
          <TaskDetail
            task={openTask}
            onClose={() => setOpenTask(null)}
            onComplete={
              openTask.status === "pending"
                ? () => void complete(openTask.id)
                : undefined
            }
            onOpenDeal={(n) => {
              setOpenTask(null);
              void openDealByNumber(n);
            }}
          />
        )}
      </Modal>

      <Modal
        open={!!openDeal || dealLoading || !!dealError}
        onClose={() => {
          setOpenDeal(null);
          setDealError(null);
        }}
        title={openDeal ? `Заявка №${openDeal.number}` : dealLoading ? "Загрузка…" : "Заявка"}
        xl
      >
        {dealLoading && !openDeal && (
          <div className="py-10 text-center text-mute text-sm">Загружаем заявку…</div>
        )}
        {dealError && !openDeal && (
          <div className="py-6 text-center text-amber-300/90 text-sm">{dealError}</div>
        )}
        {openDeal && (
          <DealWorkspace
            deal={openDeal}
            hideBack
            onClose={() => {
              setOpenDeal(null);
              setDealError(null);
            }}
          />
        )}
      </Modal>
    </div>
  );
}
