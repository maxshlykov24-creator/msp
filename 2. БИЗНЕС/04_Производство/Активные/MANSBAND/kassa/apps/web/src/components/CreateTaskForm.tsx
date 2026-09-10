import { useEffect, useMemo, useState } from "react";
import { ClipboardList } from "lucide-react";
import { STORES, TASK_FLOW, TASK_QUEUES, taskTitle } from "@kassa/shared";
import { api, USE_MOCK } from "../api/client";
import { CONSULTANTS } from "../data/mock";
import { Button, Select, opts } from "./ui";

// Виды задач и подписи — из реестра формулы (@kassa/shared/taskFlow).
// Приёмку перемещения руками не создают: она рождается после отправки.
export const TASK_KINDS: Array<readonly [string, string]> = TASK_FLOW.filter(
  (rule) => rule.kind !== "movement_accept"
).map((rule) => [rule.kind, rule.label] as const);

export type TaskAssigneeRole = "consultant" | "logist" | "crm";

export interface CreatedTask {
  id: string;
  kind: string;
  title: string;
  store?: string;
  assigneeRole: TaskAssigneeRole;
  assigneeName?: string;
  dealNumber?: number;
  status: "pending" | "done";
  createdAt: string;
  createdBy?: string;
  metadata?: Record<string, unknown>;
}

const ROLE_LABEL: Record<TaskAssigneeRole, string> = Object.fromEntries(
  TASK_QUEUES.map((queue) => [queue.role, queue.short])
) as Record<TaskAssigneeRole, string>;

const STORE_OPTIONS = STORES.filter((s) => s !== "Онлайн-магазин");

function peopleForRole(role: TaskAssigneeRole): string[] {
  if (role === "consultant") {
    return CONSULTANTS.filter((c) => c.role === "consultant").map((c) => c.name);
  }
  if (role === "logist") {
    const supply = CONSULTANTS.filter((c) => c.role === "supply").map((c) => c.name);
    return supply.length ? supply : ["Илья"];
  }
  const crm = CONSULTANTS.filter((c) => c.role === "callmanager").map((c) => c.name);
  return crm.length ? crm : ["Call-менеджер"];
}

export function CreateTaskForm({
  defaultStore,
  dealNumber,
  lockDealNumber = false,
  includeMovement = false,
  /** Без выбора вида: свободный текст + очередь (категории задач рождаются сами). */
  simple = false,
  onCreated,
  compact = false,
}: {
  defaultStore: string;
  dealNumber?: number | null;
  lockDealNumber?: boolean;
  includeMovement?: boolean;
  simple?: boolean;
  onCreated?: (task: CreatedTask) => void;
  compact?: boolean;
}) {
  const kinds = useMemo(
    () => (includeMovement ? TASK_KINDS : TASK_KINDS.filter(([k]) => k !== "movement")),
    [includeMovement]
  );
  const [kind, setKind] = useState(simple ? "manual_check" : "");
  const [title, setTitle] = useState("");
  const [store, setStore] = useState(defaultStore || STORE_OPTIONS[0] || "");
  const [dealNo, setDealNo] = useState(dealNumber != null ? String(dealNumber) : "");
  const [assigneeRole, setAssigneeRole] = useState<TaskAssigneeRole>("consultant");
  const people = peopleForRole(assigneeRole);
  const [assigneeName, setAssigneeName] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (defaultStore) setStore(defaultStore);
  }, [defaultStore]);

  useEffect(() => {
    if (dealNumber != null) setDealNo(String(dealNumber));
  }, [dealNumber]);

  function onRoleChange(role: TaskAssigneeRole) {
    setAssigneeRole(role);
    setAssigneeName("");
  }

  async function submit() {
    const effectiveKind = simple ? "manual_check" : kind;
    if (!effectiveKind) {
      setError(simple ? "Напишите, что сделать" : "Выберите вид задачи");
      return;
    }
    if (simple && !title.trim()) {
      setError("Напишите, что сделать");
      return;
    }
    const customTitle = title.trim();
    // В simple без lockDealNumber номер заявки не передаём — привязку делает система.
    const number =
      !simple || lockDealNumber
        ? dealNo
          ? Number(dealNo)
          : undefined
        : dealNumber != null
          ? dealNumber
          : undefined;
    const finalTitle = customTitle || taskTitle(effectiveKind, number);
    if (!store) {
      setError("Выберите магазин");
      return;
    }
    if (!assigneeRole) {
      setError("Выберите очередь");
      return;
    }
    setSaving(true);
    setError(null);
    const idempotencyKey = crypto.randomUUID();
    const meta: Record<string, unknown> = {};
    if (assigneeName) meta.assigneeName = assigneeName;
    const local: CreatedTask = {
      id: idempotencyKey,
      kind: effectiveKind,
      title: finalTitle,
      store,
      assigneeRole,
      assigneeName: assigneeName || undefined,
      dealNumber: number,
      status: "pending",
      createdAt: new Date().toISOString(),
      metadata: meta,
    };
    try {
      if (!USE_MOCK) {
        const savedTask = await api.post<CreatedTask>("/tasks", {
          kind: effectiveKind,
          title: finalTitle,
          store,
          assigneeRole,
          dealNumber: number,
          idempotencyKey,
          metadata: meta,
        });
        local.id = savedTask.id;
        local.createdAt = savedTask.createdAt ?? local.createdAt;
        local.createdBy = savedTask.createdBy;
        local.metadata = { ...meta, ...(savedTask.metadata ?? {}) };
      } else {
        local.createdBy = "Вы";
      }
      onCreated?.(local);
      setSaved(true);
      setTitle("");
      setAssigneeName("");
      window.setTimeout(() => setSaved(false), 1600);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось создать задачу");
    } finally {
      setSaving(false);
    }
  }

  const showDealField = !simple || lockDealNumber;

  return (
    <div className={`space-y-3 ${compact ? "" : ""}`}>
      {!compact && (
        <div className="flex items-center gap-2 text-[13px] font-semibold text-white">
          <ClipboardList size={15} className="text-gold" /> Новая задача
        </div>
      )}
      {!simple && (
        <label className="block">
          <div className="field-label">Вид задачи</div>
          <Select
            value={kind}
            onChange={setKind}
            placeholder="Выберите вид"
            options={kinds.map(([value, label]) => ({ value, label }))}
          />
        </label>
      )}
      <label className="block">
        <div className="field-label">{simple ? "Что сделать" : "Описание"}</div>
        <input
          className="input"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder={simple ? "Например: уточнить размер у клиента" : "Необязательно — подставится название вида"}
        />
      </label>
      <div className={`grid gap-3 ${showDealField ? "sm:grid-cols-2" : ""}`}>
        <label className="block">
          <div className="field-label">Магазин</div>
          <Select value={store} onChange={setStore} options={opts(...STORE_OPTIONS)} />
        </label>
        {showDealField && (
          <label className="block">
            <div className="field-label">№ заявки</div>
            <input
              className={`input ${lockDealNumber ? "opacity-70" : ""}`}
              inputMode="numeric"
              readOnly={lockDealNumber}
              value={dealNo}
              onChange={(e) => setDealNo(e.target.value.replace(/\D/g, ""))}
              placeholder="—"
            />
          </label>
        )}
      </div>
      <div className="grid sm:grid-cols-2 gap-3">
        <label className="block">
          <div className="field-label">Очередь</div>
          <Select
            value={assigneeRole}
            onChange={(next) => onRoleChange(next as TaskAssigneeRole)}
            options={(Object.keys(ROLE_LABEL) as TaskAssigneeRole[]).map((role) => ({
              value: role,
              label: ROLE_LABEL[role],
            }))}
          />
        </label>
        <label className="block">
          <div className="field-label">Ответственный (необязательно)</div>
          <Select
            value={assigneeName}
            onChange={setAssigneeName}
            options={opts(["", "Вся очередь"], ...people)}
          />
        </label>
      </div>
      {error && <div className="text-[12px] text-amber-300/90">{error}</div>}
      <div className="flex justify-end">
        <Button disabled={saving || saved} onClick={() => void submit()}>
          {saved ? "Задача создана" : saving ? "Сохранение…" : "Создать задачу"}
        </Button>
      </div>
    </div>
  );
}

export function assigneeLabel(task: {
  assigneeRole?: string;
  metadata?: Record<string, unknown> | null;
}): string {
  const name = typeof task.metadata?.assigneeName === "string" ? task.metadata.assigneeName : "";
  const role = (task.assigneeRole as TaskAssigneeRole) || "consultant";
  const roleText = ROLE_LABEL[role] ?? role;
  return name ? `${name} · ${roleText}` : roleText;
}

/** Кто поставил задачу (конкретный пользователь), отдельно от очереди/ответственного. */
export function createdByLabel(task: { createdBy?: string | null }): string | null {
  const by = task.createdBy?.trim();
  return by ? by : null;
}
