import { useEffect, useMemo, useState } from "react";
import { History, Search, UserCog, UserPlus, Check, KeyRound, Copy, Download } from "lucide-react";
import { STORES } from "@kassa/shared";
import { api, USE_MOCK } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { shortDate, timeOf } from "../lib/format";
import { Button, Modal } from "../components/ui";
import { Hint } from "../lib/hints";

interface AuditEntry {
  id: string;
  at: string;
  actor: string;
  role?: string;
  action: string;
  entity?: string;
  entityId?: string;
  source?: "manual" | "auto";
}
interface RoleUser {
  id: string;
  name: string;
  login: string;
  role: string;
  store?: string;
  active?: boolean;
}

/** Временный пароль живёт только в памяти экрана: сервер отдаёт его один раз. */
interface TempPassword {
  login: string;
  name: string;
  role: string;
  store?: string;
  password: string;
}

const MOCK_AUDIT: AuditEntry[] = [
  { id: "a1", at: "2026-07-22T12:40:00", actor: "Матвей", role: "Консультант", action: "Создал продажу", entity: "Заявка", entityId: "#1072" },
  { id: "a2", at: "2026-07-22T11:15:00", actor: "Эдвин", role: "Финансы", action: "Отметил сдачу выданной картой", entity: "Очередь", entityId: "#1070" },
  { id: "a3", at: "2026-07-21T18:10:00", actor: "Женя", role: "РОП", action: "Изменил завершённую заявку: Успех → Провал", entity: "Заявка", entityId: "#1068" },
];
const MOCK_USERS: RoleUser[] = [
  { id: "u1", name: "Матвей", login: "matvey", role: "seller", store: "На Бауманской", active: true },
  { id: "u2", name: "Женя", login: "zhenya", role: "rop", active: true },
  { id: "u3", name: "Эдвин", login: "edwin", role: "finance", active: true },
  { id: "u4", name: "Максим", login: "admin", role: "admin", active: true },
];

const ROLE_LABEL: Record<string, string> = {
  admin: "Администратор",
  seller: "Консультант",
  consultant: "Консультант",
  rop: "РОП",
  finance: "Финансы",
  logist: "Логист",
  crm: "CRM",
};

const ROLE_OPTIONS: Array<[string, string]> = [
  ["consultant", "Консультант"],
  ["crm", "Колл-менеджер (CRM)"],
  ["logist", "Логист"],
  ["finance", "Финансы"],
  ["rop", "РОП"],
  ["admin", "Администратор"],
];

export function HistoryScreen() {
  const { user } = useAuth();
  const [audit, setAudit] = useState<AuditEntry[]>(USE_MOCK ? MOCK_AUDIT : []);
  const [q, setQ] = useState("");
  const [actorFilter, setActorFilter] = useState("all");
  const [actionFilter, setActionFilter] = useState("all");
  const [sourceFilter, setSourceFilter] = useState<"all" | "manual" | "auto">("all");
  const role = String(user?.role ?? "");
  const canAudit = USE_MOCK || role === "admin" || role === "rop";

  useEffect(() => {
    if (USE_MOCK || !canAudit) return;
    api
      .get<
        Array<{
          id: string;
          createdAt: string;
          actorName: string;
          actorRole: string;
          action: string;
          entityType: string;
          entityId: string;
          source?: "manual" | "auto";
        }>
      >("/audit?limit=300")
      .then((rows) =>
        setAudit(
          rows.map((row) => ({
            id: row.id,
            at: row.createdAt,
            actor: row.actorName,
            role: row.actorRole,
            action: row.action,
            entity: row.entityType,
            entityId: row.entityId,
            source: row.source,
          }))
        )
      )
      .catch(() => {});
  }, [canAudit]);

  const actors = useMemo(
    () => [...new Set(audit.map((entry) => entry.actor).filter(Boolean))].sort(),
    [audit]
  );
  const actions = useMemo(
    () => [...new Set(audit.map((entry) => entry.action).filter(Boolean))].sort(),
    [audit]
  );

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return audit.filter(
      (entry) =>
        (actorFilter === "all" || entry.actor === actorFilter) &&
        (actionFilter === "all" || entry.action === actionFilter) &&
        (sourceFilter === "all" || (entry.source ?? "manual") === sourceFilter) &&
        `${entry.actor} ${entry.role ?? ""} ${entry.action} ${entry.entity ?? ""} ${entry.entityId ?? ""}`
          .toLowerCase()
          .includes(needle)
    );
  }, [audit, q, actorFilter, actionFilter, sourceFilter]);

  return (
    <div className="max-w-5xl mx-auto">
      <div className="flex items-center gap-2 mb-1">
        <History className="text-gold" size={22} />
        <h1 className="text-2xl font-extrabold text-white">История</h1>
      </div>
      <Hint>
        Общий журнал действий в кассе: кто, что и по какой заявке. Доступен РОП и администратору.
      </Hint>

      {!canAudit ? (
        <div className="card text-mute text-sm">
          Общий аудит доступен РОП и администратору.
        </div>
      ) : (
        <>
          <div className="flex flex-wrap gap-2 mb-4">
            <div className="relative flex-1 min-w-[220px]">
              <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-mute" />
              <input
                className="input pl-9"
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="Сотрудник, действие, номер заявки…"
              />
            </div>
            <select
              className="input w-auto"
              value={sourceFilter}
              onChange={(e) => setSourceFilter(e.target.value as "all" | "manual" | "auto")}
            >
              <option value="all">Авто и ручное</option>
              <option value="manual">Только ручное</option>
              <option value="auto">Только авто</option>
            </select>
            <select className="input w-auto" value={actorFilter} onChange={(e) => setActorFilter(e.target.value)}>
              <option value="all">Все сотрудники</option>
              {actors.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
            <select className="input w-auto" value={actionFilter} onChange={(e) => setActionFilter(e.target.value)}>
              <option value="all">Все действия</option>
              {actions.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </div>
          <div className="card p-0 overflow-hidden divide-y divide-ink-800">
            {filtered.length === 0 && (
              <div className="p-6 text-center text-mute text-sm">По фильтру записей нет</div>
            )}
            {filtered.map((entry) => (
              <div key={entry.id} className="p-4 flex gap-3">
                <span
                  className={`mt-2 w-2 h-2 rounded-full shrink-0 ${
                    entry.source === "auto" ? "bg-white/40" : "bg-gold"
                  }`}
                />
                <div className="flex-1 min-w-0">
                  <div className="text-white">{entry.action}</div>
                  <div className="text-[12px] text-mute">
                    {entry.source === "auto" ? "Система" : entry.actor}
                    {entry.source !== "auto" && entry.role && ` · ${entry.role}`}
                    {entry.entity && ` · ${entry.entity} ${entry.entityId ?? ""}`}
                    {entry.source === "auto" && " · авто"}
                  </div>
                </div>
                <div className="text-[12px] text-mute whitespace-nowrap">
                  {shortDate(entry.at)} {timeOf(entry.at)}
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

export function RolesScreen() {
  const { user } = useAuth();
  const [users, setUsers] = useState<RoleUser[]>(USE_MOCK ? MOCK_USERS : []);
  const role = String(user?.role ?? "");
  const canManage = USE_MOCK || role === "admin";

  const [createOpen, setCreateOpen] = useState(false);
  const [newName, setNewName] = useState("");
  const [newLogin, setNewLogin] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [newRole, setNewRole] = useState("consultant");
  const [newStore, setNewStore] = useState("");
  const [createError, setCreateError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  // Ведомость доступов: пароли, выданные в этой сессии. После перезагрузки пропадут.
  const [temp, setTemp] = useState<TempPassword[]>([]);
  const [copied, setCopied] = useState(false);

  async function createUser() {
    setCreateError(null);
    if (newName.trim().length < 2) return setCreateError("Имя: минимум 2 символа");
    if (!/^[a-zA-Z0-9._-]{2,}$/.test(newLogin.trim()))
      return setCreateError("Логин: латиница, цифры, точка, дефис, минимум 2 символа");
    if (newPassword.length > 0 && newPassword.length < 8)
      return setCreateError("Пароль: минимум 8 символов или оставь пустым");
    setCreating(true);
    try {
      const payload = {
        name: newName.trim(),
        login: newLogin.trim(),
        role: newRole,
        ...(newStore ? { store: newStore } : {}),
        ...(newPassword ? { password: newPassword } : {}),
      };
      if (USE_MOCK) {
        addTemp({
          login: payload.login,
          name: payload.name,
          role: newRole,
          store: newStore || undefined,
          password: newPassword || "demo-пароль",
        });
      } else {
        const created = await api.post<RoleUser & { tempPassword?: string }>("/users", payload);
        setUsers((prev) => [...prev, { ...created, active: true }]);
        if (created.tempPassword)
          addTemp({
            login: created.login,
            name: created.name,
            role: created.role,
            store: created.store,
            password: created.tempPassword,
          });
      }
      setCreateOpen(false);
      setNewName("");
      setNewLogin("");
      setNewPassword("");
      setNewRole("consultant");
      setNewStore("");
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : "Не удалось создать пользователя");
    } finally {
      setCreating(false);
    }
  }

  function addTemp(row: TempPassword) {
    setTemp((prev) => [...prev.filter((r) => r.login !== row.login), row]);
  }

  async function resetPassword(row: RoleUser) {
    if (!confirm(`Сбросить пароль ${row.name}? Старый перестанет работать.`)) return;
    if (USE_MOCK) {
      addTemp({ login: row.login, name: row.name, role: row.role, store: row.store, password: "demo-пароль" });
      return;
    }
    try {
      const res = await api.post<RoleUser & { tempPassword?: string }>(`/users/${row.id}/reset-password`, {});
      if (res.tempPassword)
        addTemp({
          login: row.login,
          name: row.name,
          role: row.role,
          store: row.store,
          password: res.tempPassword,
        });
    } catch {
      /* сбой сброса — пароль остался прежним, ведомость не меняем */
    }
  }

  async function setStore(id: string, store: string) {
    const before = users;
    setUsers((prev) => prev.map((row) => (row.id === id ? { ...row, store: store || undefined } : row)));
    if (USE_MOCK) return;
    try {
      await api.patch(`/users/${id}/store`, { store });
    } catch {
      setUsers(before);
    }
  }

  /** Ведомость для Миши: он раздаёт доступы, поэтому формат — простой текст. */
  function accessSheetText(): string {
    return temp
      .map(
        (row) =>
          `${row.name} · ${ROLE_LABEL[row.role] ?? row.role}${row.store ? ` · ${row.store}` : ""}\n` +
          `логин: ${row.login}\nпароль: ${row.password}`
      )
      .join("\n\n");
  }

  async function copySheet() {
    try {
      await navigator.clipboard.writeText(accessSheetText());
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      /* буфер недоступен — остаётся выгрузка файлом */
    }
  }

  function downloadSheet() {
    const csv = [
      "Имя;Логин;Роль;Магазин;Временный пароль",
      ...temp.map((row) =>
        [row.name, row.login, ROLE_LABEL[row.role] ?? row.role, row.store ?? "", row.password].join(";")
      ),
    ].join("\n");
    // BOM нужен, чтобы Excel не ломал кириллицу.
    const blob = new Blob([`\uFEFF${csv}`], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `ведомость-доступов-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  useEffect(() => {
    if (USE_MOCK) return;
    if (canManage) {
      api.get<RoleUser[]>("/users").then(setUsers).catch(() => {});
    } else if (user) {
      setUsers([
        { id: user.id, name: user.name, login: user.login, role: user.role, store: user.store, active: true },
      ]);
    }
  }, [canManage, user]);

  async function setRole(id: string, nextRole: string) {
    const before = users;
    setUsers((prev) => prev.map((row) => (row.id === id ? { ...row, role: nextRole } : row)));
    if (!USE_MOCK) {
      try {
        await api.patch(`/users/${id}/role`, { role: nextRole });
      } catch {
        setUsers(before);
      }
    }
  }

  return (
    <div className="max-w-5xl mx-auto">
      <div className="flex items-center gap-2 mb-1">
        <UserCog className="text-gold" size={22} />
        <h1 className="text-2xl font-extrabold text-white">Роли</h1>
        {canManage && (
          <button
            type="button"
            onClick={() => setCreateOpen(true)}
            className="ml-auto inline-flex items-center gap-1.5 rounded-lg bg-white text-ink-950 font-bold px-3 py-2 text-sm hover:bg-white/90"
          >
            <UserPlus size={16} /> Новый пользователь
          </button>
        )}
      </div>
      <Hint>
        Доступы сотрудников к кассе. Менять роли и создавать пользователей может только
        администратор. Кнопка «Сбросить» выдаёт новый временный пароль. Ведомость доступов видна
        один раз: после перезагрузки экрана останется только сброс.
      </Hint>

      {!canManage ? (
        <div className="card text-mute text-sm">Управление ролями доступно только администратору.</div>
      ) : (
        <div className="card p-0 overflow-x-auto">
          <table className="w-full min-w-[600px] text-sm">
            <thead className="text-left text-mute uppercase text-[11px] border-b border-ink-700">
              <tr>
                <th className="px-4 py-3">Сотрудник</th>
                <th className="px-4">Логин</th>
                <th className="px-4">Роль</th>
                <th className="px-4">Магазин</th>
                <th className="px-4">Пароль</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-ink-800">
              {users.map((row) => (
                <tr key={row.id}>
                  <td className="px-4 py-3 text-white font-medium">{row.name}</td>
                  <td className="px-4 text-mute">{row.login}</td>
                  <td className="px-4">
                    <select
                      className="input py-1.5 text-sm"
                      value={row.role}
                      onChange={(e) => void setRole(row.id, e.target.value)}
                    >
                      {!ROLE_OPTIONS.some(([v]) => v === row.role) && (
                        <option value={row.role}>{ROLE_LABEL[row.role] ?? row.role}</option>
                      )}
                      {ROLE_OPTIONS.map(([value, label]) => (
                        <option value={value} key={value}>
                          {label}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td className="px-4">
                    <select
                      className="input py-1.5 text-sm"
                      value={row.store ?? ""}
                      onChange={(e) => void setStore(row.id, e.target.value)}
                    >
                      <option value="">—</option>
                      {STORES.map((store) => (
                        <option key={store} value={store}>
                          {store}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td className="px-4">
                    <button
                      type="button"
                      className="text-[12px] text-gold hover:underline whitespace-nowrap"
                      onClick={() => void resetPassword(row)}
                    >
                      Сбросить
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {canManage && temp.length > 0 && (
        <div className="card mt-4">
          <div className="flex items-center gap-2 mb-1">
            <KeyRound className="text-gold" size={18} />
            <h2 className="font-bold text-white">Ведомость доступов</h2>
            <div className="ml-auto flex gap-2">
              <button
                type="button"
                onClick={() => void copySheet()}
                className="inline-flex items-center gap-1.5 rounded-lg border border-ink-700 px-3 py-1.5 text-[13px] text-white hover:bg-white/5"
              >
                <Copy size={14} /> {copied ? "Скопировано" : "Копировать"}
              </button>
              <button
                type="button"
                onClick={downloadSheet}
                className="inline-flex items-center gap-1.5 rounded-lg border border-ink-700 px-3 py-1.5 text-[13px] text-white hover:bg-white/5"
              >
                <Download size={14} /> CSV
              </button>
            </div>
          </div>
          <p className="text-[12px] text-mute mb-3">
            Пароли видны только сейчас: после перезагрузки экрана останется лишь сброс. Отдай ведомость и
            закрой страницу.
          </p>
          <div className="divide-y divide-ink-800">
            {temp.map((row) => (
              <div key={row.login} className="py-2 flex flex-wrap items-baseline gap-x-3 gap-y-1">
                <span className="text-white font-medium">{row.name}</span>
                <span className="text-[12px] text-mute">
                  {ROLE_LABEL[row.role] ?? row.role}
                  {row.store && ` · ${row.store}`}
                </span>
                <span className="ml-auto font-mono text-[13px] text-white">{row.login}</span>
                <span className="font-mono text-[13px] text-gold">{row.password}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      <Modal open={createOpen} onClose={() => setCreateOpen(false)} title="Новый пользователь">
        <div className="space-y-4">
          <label className="block">
            <div className="field-label">Имя</div>
            <input className="input" value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="Как в кассе" />
          </label>
          <div className="grid sm:grid-cols-2 gap-3">
            <label>
              <div className="field-label">Логин</div>
              <input
                className="input"
                autoCapitalize="off"
                autoCorrect="off"
                value={newLogin}
                onChange={(e) => setNewLogin(e.target.value)}
                placeholder="латиница"
              />
            </label>
            <label>
              <div className="field-label">Временный пароль</div>
              <input
                className="input"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                placeholder="пусто — сгенерируем"
              />
            </label>
          </div>
          <div className="grid sm:grid-cols-2 gap-3">
            <label>
              <div className="field-label">Роль</div>
              <select className="input" value={newRole} onChange={(e) => setNewRole(e.target.value)}>
                {ROLE_OPTIONS.map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              <div className="field-label">Магазин</div>
              <select className="input" value={newStore} onChange={(e) => setNewStore(e.target.value)}>
                <option value="">Не привязан</option>
                {STORES.map((store) => (
                  <option key={store} value={store}>
                    {store}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="text-[12px] text-mute">
            Пароль попадёт в ведомость доступов на этом экране. При первом входе система попросит его сменить.
          </div>
          {createError && (
            <div className="text-[13px] text-red-300 bg-red-400/10 border border-red-400/30 rounded-lg px-3 py-2">
              {createError}
            </div>
          )}
          <div className="flex justify-end">
            <Button disabled={creating} onClick={() => void createUser()}>
              <Check size={15} /> {creating ? "Создаём…" : "Создать"}
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}

/** Старый объединённый экран — оставляем редирект-обёртку не нужен, экспорт для совместимости импортов. */
export function AuditRoles() {
  return <HistoryScreen />;
}
