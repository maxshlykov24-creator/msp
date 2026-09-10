import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowRightLeft, Check, Gift, ListPlus, Plus } from "lucide-react";
import {
  ITEM_LOCATIONS,
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
import { Button, Modal, StageBadge } from "../components/ui";
import {
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
import { Hint } from "../lib/hints";

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
      dealStage: "Ждет товар",
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

function isSaryOps(kind: string): boolean {
  return kind === "sary_send";
}

function shortPlace(name: string): string {
  const trimmed = name.trim();
  if (!trimmed || trimmed === "—") return "—";
  return trimmed.replace(/^На\s+/i, "").replace(/^Центральный склад$/i, "Центральный");
}

function warehouseKey(name: string): string {
  const n = name.toLowerCase().replace(/ё/g, "е");
  if (/бауман/.test(n)) return "bauman";
  if (/новокузнецк|пятниц/.test(n)) return "novo";
  if (/центральн/.test(n)) return "central";
  return n.trim();
}

function taskTouchesStore(task: OperationTask, store: string): boolean {
  if (!store) return true;
  const key = warehouseKey(store);
  const route = routeFromTaskMeta(task.metadata);
  return [task.store, route?.from, route?.to].some((value) => Boolean(value) && warehouseKey(value!) === key);
}

function dealStageOf(task: OperationTask): string | null {
  const stage = task.metadata?.dealStage;
  return typeof stage === "string" && stage.trim() ? stage.trim() : null;
}

function clientOf(task: OperationTask): string | null {
  const client = task.metadata?.client;
  return typeof client === "string" && client.trim() ? client.trim() : null;
}

function productCell(task: OperationTask): string {
  const list = positionsFromTaskMeta(task.metadata);
  const names = list.map((p) => p.name?.trim()).filter(Boolean);
  if (names.length === 1) return names[0]!;
  if (names.length > 1) return `${names[0]} · ещё ${names.length - 1}`;
  return clientOf(task) || "—";
}

/** Одна задача в очереди — клик по строке открывает карточку. */
function TaskRow({
  task,
  onComplete,
  onOpenTask,
  showQueue,
}: {
  task: OperationTask;
  onComplete: () => void;
  onOpenTask: () => void;
  showQueue?: boolean;
}) {
  const needsSetup = task.kind === "movement" && task.metadata?.needsSetup === true;
  const isMove = task.kind === "movement" || task.kind === "movement_accept";
  const needsCardScan = isMove || task.kind === "assemble_cdek";
  const route = routeFromTaskMeta(task.metadata);
  const stage = dealStageOf(task);
  const queueShort = TASK_QUEUES.find((q) => q.role === task.assigneeRole)?.short;

  return (
    <tr
      className="hover:bg-ink-800/40 cursor-pointer transition"
      onClick={onOpenTask}
    >
      <td className="px-3 py-3 align-top whitespace-nowrap">
        <div className="text-white font-medium text-[13px]">{taskKindLabel(task.kind)}</div>
        {showQueue && queueShort && <div className="text-[11px] text-mute mt-0.5">{queueShort}</div>}
      </td>
      <td className="px-3 py-3 align-top min-w-0">
        <div className="text-white text-[13px] leading-snug line-clamp-2" title={productCell(task)}>
          {productCell(task)}
        </div>
        {needsSetup && (
          <div className="text-[11px] text-amber-300/90 mt-0.5">Нужно указать маршрут и позиции</div>
        )}
      </td>
      <td className="px-3 py-3 align-top text-[13px] text-mute-soft whitespace-nowrap">
        {route ? shortPlace(route.from) : "—"}
      </td>
      <td className="px-3 py-3 align-top text-[13px] text-mute-soft whitespace-nowrap">
        {route ? shortPlace(route.to) : "—"}
      </td>
      <td className="px-3 py-3 align-top min-w-0">
        {stage ? <StageBadge stage={stage} className="inline-block align-top" /> : <span className="text-mute">—</span>}
      </td>
      <td className="px-3 py-3 align-top text-[12px] text-mute tabular-nums whitespace-nowrap">
        <div>{shortDate(task.createdAt)}</div>
        <div className="text-[11px] text-mute/80">{timeOf(task.createdAt)}</div>
      </td>
      <td className="px-3 py-3 align-top text-right">
        {task.status === "pending" && !needsSetup && !needsCardScan && (
          <Button
            variant="subtle"
            className="py-1.5 px-2.5 text-[12px]"
            onClick={(e) => {
              e.stopPropagation();
              onComplete();
            }}
          >
            <Check size={14} /> {taskActionLabel(task.kind)}
          </Button>
        )}
      </td>
    </tr>
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
  const [storeScope, setStoreScope] = useState(() =>
    STORE_OPTIONS.includes(activeStore as (typeof STORE_OPTIONS)[number]) ? activeStore : ""
  );
  const [fromFilter, setFromFilter] = useState("");
  const [toFilter, setToFilter] = useState("");
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

  const routeOptions = useMemo(() => {
    const names = new Set<string>(ITEM_LOCATIONS);
    for (const task of tasks) {
      const route = routeFromTaskMeta(task.metadata);
      if (route?.from && route.from !== "—") names.add(route.from);
      if (route?.to && route.to !== "—") names.add(route.to);
    }
    return [...names];
  }, [tasks]);

  /** Задачи выбранной очереди, магазина и маршрута. Сары — только в блоке ниже. */
  const visibleTasks = useMemo(() => {
    return tasks.filter((task) => {
      if (isSaryOps(task.kind)) return false;
      if (view !== "all" && task.assigneeRole !== view) return false;
      if (view === "all" && queueFilter && task.assigneeRole !== queueFilter) return false;
      if (status !== "all" && task.status !== status) return false;
      if (kindFilter && task.kind !== kindFilter) return false;
      if (storeScope && !taskTouchesStore(task, storeScope)) return false;
      const route = routeFromTaskMeta(task.metadata);
      if (fromFilter && warehouseKey(route?.from ?? "") !== warehouseKey(fromFilter)) return false;
      if (toFilter && warehouseKey(route?.to ?? "") !== warehouseKey(toFilter)) return false;
      return true;
    });
  }, [tasks, status, view, kindFilter, queueFilter, storeScope, fromFilter, toFilter]);

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
      if (storeScope && !taskTouchesStore(task, storeScope)) continue;
      counts.set(task.kind, (counts.get(task.kind) ?? 0) + 1);
    }
    return counts;
  }, [tasks, view, status, queueFilter, storeScope]);

  const total = visibleTasks.length;

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
    <div className="max-w-7xl mx-auto">
      <div className="flex flex-wrap items-start justify-between gap-3 mb-5">
        <div>
          <h1 className="text-2xl font-extrabold text-white">Задачи</h1>
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
      <Hint>
        <p>
          Очередь перемещений и прочих задач. По умолчанию свой магазин, фильтром можно открыть
          чужие. Перемещение в два шага: отправил → в пути → приёмка. Документ в МойСклад
          создаётся при приёмке, не при отправке.
        </p>
        <p>
          Задачи с Центрального склада уходят логисту. Колонка «этап» — этап заявки, не статус
          задачи.
        </p>
      </Hint>

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
          className="input py-2 text-sm w-auto min-w-[160px]"
          value={storeScope}
          onChange={(e) => setStoreScope(e.target.value)}
        >
          <option value="">Все магазины</option>
          {STORE_OPTIONS.map((store) => (
            <option key={store} value={store}>
              {store === activeStore ? `${shortPlace(store)} · этот` : shortPlace(store)}
            </option>
          ))}
        </select>
        <select
          className="input py-2 text-sm w-auto min-w-[150px]"
          value={fromFilter}
          onChange={(e) => setFromFilter(e.target.value)}
        >
          <option value="">Откуда · все</option>
          {routeOptions.map((name) => (
            <option key={`from-${name}`} value={name}>
              {shortPlace(name)}
            </option>
          ))}
        </select>
        <select
          className="input py-2 text-sm w-auto min-w-[150px]"
          value={toFilter}
          onChange={(e) => setToFilter(e.target.value)}
        >
          <option value="">Куда · все</option>
          {routeOptions.map((name) => (
            <option key={`to-${name}`} value={name}>
              {shortPlace(name)}
            </option>
          ))}
        </select>
        <span className="text-[12px] text-mute">
          {viewLabel} · {total}
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

      <div className="card p-0 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm min-w-[860px]">
            <thead className="text-[11px] uppercase tracking-wider text-mute border-b border-ink-800">
              <tr>
                <th className="text-left px-3 py-2.5 font-medium">Задача</th>
                <th className="text-left px-3 py-2.5 font-medium">Товар</th>
                <th className="text-left px-3 py-2.5 font-medium">Откуда</th>
                <th className="text-left px-3 py-2.5 font-medium">Куда</th>
                <th className="text-left px-3 py-2.5 font-medium">Этап заявки</th>
                <th className="text-left px-3 py-2.5 font-medium">Когда</th>
                <th className="px-3 py-2.5 font-medium" />
              </tr>
            </thead>
            <tbody className="divide-y divide-ink-800">
              {visibleTasks.map((task) => (
                <TaskRow
                  key={task.id}
                  task={task}
                  showQueue={view === "all"}
                  onOpenTask={() => setOpenTask(task)}
                  onComplete={() => void complete(task.id)}
                />
              ))}
            </tbody>
          </table>
        </div>
        {total === 0 && <div className="py-10 text-center text-mute">Задач нет</div>}
      </div>

      {view === "crm" && (
        <section className="mt-8 border-t border-ink-700 pt-6">
          <div className="flex items-center gap-2 mb-1">
            <Gift className="text-gold" size={18} />
            <h2 className="text-lg font-bold text-white">Сары · реферальные выплаты</h2>
          </div>
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
