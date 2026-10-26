import { useCallback, useEffect, useState } from "react";
import { ChevronDown, ChevronRight, ClipboardList } from "lucide-react";
import { TASK_FLOW, TASK_QUEUES, type Task } from "@kassa/shared";
import { api } from "../api/client";
import { Button, Modal } from "./ui";
import { CreateTaskForm, type CreatedTask } from "./CreateTaskForm";

const KIND_LABEL = new Map(TASK_FLOW.map((rule) => [rule.kind, rule.label]));
const ROLE_LABEL = new Map(TASK_QUEUES.map((queue) => [queue.role, queue.short]));

function moscowToday(): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Moscow" }).format(new Date());
}

function dueOf(task: Task): string {
  const due = task.metadata?.dueDate;
  return typeof due === "string" ? due.slice(0, 10) : "";
}

function isOverdue(task: Task, today: string): boolean {
  const due = dueOf(task);
  return task.status === "pending" && /^\d{4}-\d{2}-\d{2}$/.test(due) && due < today;
}

function assigneeOf(task: Task): string {
  const name = task.metadata?.assigneeName;
  if (typeof name === "string" && name.trim()) return name.trim();
  return ROLE_LABEL.get(task.assigneeRole) ?? task.assigneeRole;
}

function TaskLine({ task, today }: { task: Task; today: string }) {
  const due = dueOf(task);
  const overdue = isOverdue(task, today);
  const kind = KIND_LABEL.get(task.kind) ?? task.kind;
  return (
    <div className="rounded-lg border border-ink-700 bg-ink-900/60 px-3 py-2">
      <div className={`text-[14px] leading-snug ${overdue ? "text-red-300" : "text-white"}`}>{task.title}</div>
      <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[12px] text-mute">
        <span>{kind}</span>
        <span>{assigneeOf(task)}</span>
        {due && <span className={overdue ? "text-red-300" : ""}>до {due.split("-").reverse().join(".")}</span>}
        {task.status === "cancelled" && <span>отменена</span>}
        {task.status === "done" && task.completedAt && (
          <span>готово {task.completedAt.slice(0, 10).split("-").reverse().join(".")}</span>
        )}
      </div>
    </div>
  );
}

/** Кнопка «Задача» в заявке: открытые задачи, выполненные и создание. */
export function DealTasksButton({
  dealNumber,
  store,
  load = true,
  onCreated,
}: {
  dealNumber?: number;
  store: string;
  /** Не грузить список, пока заявки ещё нет на сервере. */
  load?: boolean;
  onCreated?: (task: CreatedTask) => void;
}) {
  const [open, setOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [doneOpen, setDoneOpen] = useState(false);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loaded, setLoaded] = useState(!load);
  const today = moscowToday();

  const reload = useCallback(async () => {
    if (!load || dealNumber == null) {
      setTasks([]);
      setLoaded(true);
      return;
    }
    try {
      const rows = await api.get<Task[]>(`/tasks?dealNumber=${dealNumber}`);
      setTasks(Array.isArray(rows) ? rows : []);
    } catch {
      setTasks([]);
    } finally {
      setLoaded(true);
    }
  }, [dealNumber, load]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const openTasks = tasks.filter((task) => task.status === "pending");
  const doneTasks = tasks.filter((task) => task.status !== "pending");
  const overdueCount = openTasks.filter((task) => isOverdue(task, today)).length;
  const none = loaded && tasks.length === 0;
  const badgeClass = !loaded
    ? "bg-ink-600 text-white"
    : overdueCount > 0
      ? "bg-red-500 text-white"
      : none
        ? "bg-amber-400 text-ink-950"
        : "bg-ink-600 text-white";

  return (
    <>
      <button
        type="button"
        onClick={() => {
          setCreating(false);
          setDoneOpen(false);
          setOpen(true);
          void reload();
        }}
        className="relative inline-flex items-center gap-1.5 rounded-lg border border-ink-700 bg-ink-900 px-3 py-2 text-[13px] font-medium text-white hover:border-gold/40"
      >
        <ClipboardList size={15} className="text-gold" /> Задача
        <span
          className={`absolute -right-1.5 -top-1.5 grid h-[18px] min-w-[18px] place-items-center rounded-full px-1 text-[10px] font-bold leading-none ${badgeClass}`}
        >
          {overdueCount}
        </span>
      </button>
      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title={
          <>
            Задачи
            {dealNumber != null && <span className="ml-2 text-[13px] font-normal text-mute">№{dealNumber}</span>}
          </>
        }
      >
        <div className="flex flex-col items-start gap-3">
          <div className="w-full">
            <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-mute">Открытые</div>
            {openTasks.length === 0 ? (
              <div className="text-[13px] text-mute">Открытых задач нет</div>
            ) : (
              <div className="space-y-1.5">
                {openTasks.map((task) => (
                  <TaskLine key={task.id} task={task} today={today} />
                ))}
              </div>
            )}
          </div>

          <button
            type="button"
            onClick={() => setDoneOpen((value) => !value)}
            className="inline-flex items-center gap-1 text-[13px] text-mute hover:text-white"
          >
            {doneOpen ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
            Выполненные{doneTasks.length > 0 ? ` · ${doneTasks.length}` : ""}
          </button>
          {doneOpen && (
            <div className="w-full">
              {doneTasks.length === 0 ? (
                <div className="text-[13px] text-mute">Выполненных задач нет</div>
              ) : (
                <div className="space-y-1.5">
                  {doneTasks.map((task) => (
                    <TaskLine key={task.id} task={task} today={today} />
                  ))}
                </div>
              )}
            </div>
          )}

          {creating ? (
            <div className="w-full">
              <CreateTaskForm
                defaultStore={store}
                dealNumber={dealNumber}
                lockDealNumber={dealNumber != null}
                simple
                compact
                onCreated={(task) => {
                  onCreated?.(task);
                  setCreating(false);
                  void reload();
                }}
              />
            </div>
          ) : (
            <Button type="button" onClick={() => setCreating(true)}>
              Создать задачу
            </Button>
          )}
        </div>
      </Modal>
    </>
  );
}
