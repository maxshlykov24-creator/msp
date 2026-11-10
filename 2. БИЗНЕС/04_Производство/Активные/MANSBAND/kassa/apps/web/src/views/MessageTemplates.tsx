import { Children, isValidElement, useCallback, useEffect, useRef, useState, type ChangeEvent, type ReactNode, type SelectHTMLAttributes } from "react";
import { CheckCircle2, ChevronDown, SlidersHorizontal, X, AlertCircle, ArrowLeft, ArrowUpRight, RefreshCw, Pause, Play, Clock3, Send, MessageCircle, Users, ShoppingBag, ChevronRight, Search, ShieldCheck, CalendarDays, Check } from "lucide-react";
import { Select } from "../components/Select";
import { DatePicker } from "../components/DatePicker";
import { Modal, Button, StageBadge } from "../components/ui";
import { api, getToken } from "../api/client";
import { useAuth } from "../auth/AuthContext";

type Channel = "telegram" | "whatsapp" | "max";
type Condition = { field: number; op: string; value: string | number | boolean | null };
type Trigger = { type: "date" | "stage"; field: number | null; offset: number; unit: "days" | "weeks" | "months"; time: string; delay_minutes: number; repeat: "each_entry" | "once" };
type Rule = { action?: "text" | "certificate"; certificate_pdf?: { intro?: string; instructions?: string; contacts?: Record<string,string> }; name: string; enabled: boolean; pipelines: number[]; statuses: number[]; match: "all" | "any"; conditions: Condition[]; trigger: Trigger; recipient: "contact" | "field"; phone_field: number | null; content: string; variants: Record<string, string>; store_field: number; window_start: number; window_end: number; late_policy: "skip" | "same_day" | "legacy_stage"; legacy_next_day: boolean };
type Routing = { order: Channel[]; previous_first: boolean; social_fallback: boolean };
type Manual = { remote_id?: number; name: string; content: string; source_hash?: string; original?: unknown; delete?: boolean };
type Payload = Rule | Routing | Manual;
type CrmState = { state: string; version: number; message: string; http_status?: number | null };
type Doc = { id: string; kind: "rule" | "routing" | "manual"; version: number; published_version?: number; draft: Payload; active: Payload | null; checked: boolean; crm?: CrmState | null };
type Field = { id: number; name: string; type: string; enums?: { id: number; value: string }[] };
type Pipeline = { id: number; name: string; sort?: number; _embedded?: { statuses?: { id: number; name: string; sort?: number }[] } };
type Remote = { id: number; name: string; content: string; source_hash: string; type: string; external_id?: string | null };
type Check = { sample_limit?: number; total?: number; samples?: { lead_id: number; text?: string; due_at?: string; channels?: string[]; reason?: string }[]; overlaps?: { id: string; name: string }[]; text?: string };
type Journal = { state?: string; gift_lead_id?: number; id: number; rule_id: string; lead_id: number; due_at: string; label: string; reason: string; attempts: { channel: string; state: string; reason: string }[] };
type Revision = { version: number; actor: string; action: string; at: string; payload: Payload | { document: Payload } };
const channelNames: Record<Channel, string> = { telegram: "Telegram", whatsapp: "WhatsApp", max: "MAX" };
const input = "w-full rounded-xl border border-ink-600 bg-ink-900 px-3 py-2 text-white disabled:opacity-60";
const button = "rounded-xl border border-ink-600 px-3 py-2 text-sm disabled:opacity-40 hover:bg-ink-800";
const root = "/message-templates";
const copy = <T,>(v: T): T => JSON.parse(JSON.stringify(v)) as T;
const newRule = (): Rule => ({ name: "Новый сценарий", enabled: false, pipelines: [9601214], statuses: [], match: "all", conditions: [], trigger: { type: "date", field: null, offset: 0, unit: "days", time: "10:00", delay_minutes: 0, repeat: "each_entry" }, recipient: "contact", phone_field: null, content: "", variants: {}, store_field: 3158921, window_start: 10, window_end: 20, late_policy: "skip", legacy_next_day: false });

function TemplateSelect({ value, onChange, children, disabled, "aria-label": ariaLabel }: SelectHTMLAttributes<HTMLSelectElement>) {
  const options = Children.toArray(children).flatMap(child => {
    if (!isValidElement<{ value?: string | number; children?: ReactNode; disabled?: boolean }>(child)) return [];
    return [{ value: String(child.props.value ?? ""), label: Children.toArray(child.props.children).join(""), disabled: child.props.disabled }];
  });
  return <Select className="w-full min-w-0" value={String(value ?? "")} options={options} disabled={disabled} ariaLabel={ariaLabel} onChange={next => onChange?.({ target: { value: next } } as ChangeEvent<HTMLSelectElement>)} />;
}

export function MessageTemplates() {
  const { user } = useAuth();
  const editable = user?.role === "admin";
  const [campaignOpen, setCampaignOpen] = useState(false);
  const [tab, setTab] = useState<"rule" | "manual" | "routing">("rule");
  const [docs, setDocs] = useState<Doc[]>([]);
  const [catalog, setCatalog] = useState<{ fields: Field[]; pipelines: Pipeline[] }>({ fields: [], pipelines: [] });
  const [remote, setRemote] = useState<Remote[]>([]);
  const [journal, setJournal] = useState<Journal[]>([]);
  const [executor, setExecutor] = useState("preview");
  const [selected, setSelected] = useState<Doc | null>(null);
  const [dirty, setDirty] = useState(false);
  const [checked, setChecked] = useState<Check | null>(null);
  const [confirmOverlap, setConfirmOverlap] = useState(false);
  const [leadId, setLeadId] = useState("");
  const [testDestination, setTestDestination] = useState("");
  const [testChannel, setTestChannel] = useState<Channel>("telegram");
  const [testOperation, setTestOperation] = useState(() => crypto.randomUUID());
  const [search, setSearch] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [pipelineFilter, setPipelineFilter] = useState("");
  const [stageFilter, setStageFilter] = useState("");
  const [stateFilter, setStateFilter] = useState("");
  const [triggerFilter, setTriggerFilter] = useState("");
  const [journalOpen, setJournalOpen] = useState(false);
  const [dialog, setDialog] = useState<{ message: string; confirm: string } | null>(null);
  const dialogResolve = useRef<((ok: boolean) => void) | null>(null);
  const ask = (message: string, confirm = "Продолжить") => new Promise<boolean>(resolve => {
    dialogResolve.current = resolve; setDialog({ message, confirm });
  });
  const closeDialog = (ok: boolean) => { dialogResolve.current?.(ok); dialogResolve.current = null; setDialog(null); };
  const [history, setHistory] = useState<Revision[]>([]);
  const action = async (fn: () => Promise<void>) => {
    setBusy(true); setError(""); setNotice("");
    try { await fn(); } catch (e) { setError(e instanceof Error ? e.message : "Ошибка запроса"); }
    finally { setBusy(false); }
  };
  const refresh = async () => {
    const result = await api.get<{ rules: Doc[]; executor: string }>(root);
    setDocs(result.rules); setExecutor(result.executor);
  };
  useEffect(() => { void action(async () => {
    await refresh();
    setCatalog(await api.get(root + "/catalog"));
    setRemote((await api.get<{ items: Remote[] }>(root + "/manual")).items);
    setJournal((await api.get<{ items: Journal[] }>(root + "/journal")).items);
  }); }, []);
  const open = async (doc: Doc, discardConfirmed = false) => {
    if (dirty && !discardConfirmed && !await ask("Есть несохранённые изменения. Перейти без сохранения?", "Перейти")) return;
    setSelected(copy(doc)); setDirty(false); setChecked(null); setConfirmOverlap(false); setHistory([]); setNotice(""); setTestOperation(crypto.randomUUID());
  };
  const edit = (patch: Partial<Rule> | Partial<Routing> | Partial<Manual>) => {
    if (!selected) return;
    setSelected({ ...selected, draft: { ...selected.draft, ...patch } as Payload });
    setDirty(true); setChecked(null); setConfirmOverlap(false);
  };
  const switchTab = async (next: typeof tab) => {
    if (dirty && !await ask("Перейти без сохранения изменений?", "Перейти")) return;
    setTab(next); setSelected(null); setDirty(false); setChecked(null); setHistory([]);
    if (next === "routing") { const d = docs.find(x => x.id === "routing"); if (d) void open(d, true); }
  };
  const save = () => void action(async () => {
    if (!selected) return;
    const d = await api.post<Doc>(root + "/draft", { id: selected.id, kind: selected.kind, version: selected.version, payload: selected.draft });
    setSelected(d); setDirty(false); setChecked(null); await refresh(); setNotice("Черновик сохранён. Действующая версия не изменилась.");
  });
  const check = () => void action(async () => {
    if (!selected) return;
    const result = await api.post<Check>(root + "/check/" + selected.id, { version: selected.version, ...(leadId ? { lead_id: Number(leadId) } : {}) });
    setChecked(result); setNotice("Проверка завершена без отправки сообщений.");
  });
  const publish = () => void action(async () => {
    if (!selected) return;
    const deleting = selected.kind === "manual" && (selected.draft as Manual).delete;
    if (deleting && !await ask("Удалить этот ручной шаблон из amoCRM? Предыдущая версия останется в истории.", "Удалить шаблон")) return;
    const d = await api.post<Doc>(root + "/publish/" + selected.id, { version: selected.version, overlaps_confirmed: confirmOverlap, delete_confirmed: Boolean(deleting) });
    setSelected(d); setChecked(null); await refresh();
    if (selected.kind === "manual") setRemote((await api.get<{ items: Remote[] }>(root + "/manual")).items);
    setNotice(executor === "live" || selected.kind === "manual" ? "Изменения опубликованы." : "Версия опубликована, но пока не подключена к отправке. Действующие сообщения не изменены.");
  });
  const saveManual = () => void action(async () => {
    if (!selected || selected.kind !== "manual") return;
    const current = selected.draft as Manual;
    let doc = selected;
    if (dirty || doc.version === 0 || current.delete) {
      doc = await api.post<Doc>(root + "/draft", { id: doc.id, kind: "manual", version: doc.version, payload: { ...current, delete: false } });
      setSelected(doc); setDirty(false);
    }
    const saved = doc.draft as Manual;
    const currentRemote = saved.remote_id ? remote.find(x => x.id === saved.remote_id) : undefined;
    if (!dirty && currentRemote && saved.name === currentRemote.name && saved.content === currentRemote.content) {
      await refresh();
      setNotice("Текст уже такой в amoCRM, запись не отправлялась.");
      return;
    }
    await api.post(root + "/check/" + doc.id, { version: doc.version });
    const published = await api.post<Doc>(root + "/publish/" + doc.id, { version: doc.version, delete_confirmed: false });
    setSelected(published); setChecked(null); await refresh();
    setRemote((await api.get<{ items: Remote[] }>(root + "/manual")).items);
    setNotice("Сохранено в amoCRM.");
  });
  const removeManual = () => void action(async () => {
    if (!selected || selected.kind !== "manual" || !(selected.draft as Manual).remote_id) return;
    if (!await ask("Удалить этот шаблон из amoCRM? В истории кассы текст останется.", "Удалить шаблон")) return;
    const doc = await api.post<Doc>(root + "/draft", { id: selected.id, kind: "manual", version: selected.version, payload: { ...(selected.draft as Manual), delete: true } });
    await api.post(root + "/check/" + doc.id, { version: doc.version });
    await api.post(root + "/publish/" + doc.id, { version: doc.version, delete_confirmed: true });
    setSelected(null); setDirty(false); await refresh();
    setRemote((await api.get<{ items: Remote[] }>(root + "/manual")).items);
    setNotice("Шаблон удалён из amoCRM.");
  });
  const fields = catalog.fields;
  const rule = selected?.kind === "rule" ? selected.draft as Rule : null;
  const isGift = rule?.action === "certificate";
  const routing = selected?.kind === "routing" ? selected.draft as Routing : null;
  const manual = selected?.kind === "manual" ? selected.draft as Manual : null;
  const manualReadOnly = Boolean(manual?.remote_id && remote.find(x => x.id === manual.remote_id)?.type !== "amocrm");
  const remoteNow = manual?.remote_id ? remote.find(x => x.id === manual.remote_id) : undefined;
  const draftMatchesRemote = Boolean(remoteNow && manual && !dirty && manual.name === remoteNow.name && manual.content === remoteNow.content && !manual.delete);
  const manualStatus = !manual ? "" : dirty ? "На экране есть правки, в CRM их ещё нет" : remoteNow && manual.source_hash && remoteNow.source_hash !== manual.source_hash ? "Шаблон изменили в amoCRM" : selected?.crm?.state === "rejected" && selected.crm.version === selected.version ? (selected.crm.message || "amoCRM не приняла текст") : selected?.crm?.state === "uncertain" ? "Прошлый запрос не получил ответ, повтор сам не отправляется" : manual.delete ? "Удаление подготовлено, в amoCRM шаблон ещё есть" : !manual.remote_id ? "В amoCRM этого шаблона ещё нет" : draftMatchesRemote ? "Сейчас так в amoCRM" : "Есть правки, в CRM их ещё нет";
  const saveManualDisabled = busy || (!dirty && draftMatchesRemote && !manual?.delete && selected?.crm?.state !== "rejected" && selected?.crm?.state !== "uncertain");
  const stepHint = !selected || selected.kind === "manual" ? "" : dirty || !selected.version ? "Сначала сохрани черновик." : !checked ? (selected.kind === "routing" ? "Порядок включится после проверки и публикации. Сначала проверь." : "Сначала проверь, кому уйдёт сообщение.") : "Проверка есть. Можно публиковать.";
  const discardManual = () => void action(async () => {
    if (!selected || !remoteNow) return;
    if (!await ask("Загрузить текст из amoCRM? Правки на этом экране пропадут.", "Загрузить")) return;
    const payload = { remote_id: remoteNow.id, name: remoteNow.name, content: remoteNow.content, source_hash: remoteNow.source_hash, delete: false };
    if (selected.version === 0) { setSelected({ ...selected, draft: payload }); setDirty(false); setNotice("На экране текст из amoCRM."); return; }
    const doc = await api.post<Doc>(root + "/draft", { id: selected.id, kind: "manual", version: selected.version, payload });
    setSelected(doc); setDirty(false); setChecked(null); await refresh();
    setNotice("На экране текст из amoCRM. В CRM ничего не записано.");
  });
  const activeRouting = docs.find(x => x.kind === "routing")?.active as Routing | undefined;
  const stages = catalog.pipelines.filter(p => rule?.pipelines.includes(p.id)).flatMap(p => p._embedded?.statuses ?? []);
  const fieldSelect = (value: number | null, onChange: (n: number | null) => void, subset = fields) => <TemplateSelect className={input} value={value ?? ""} onChange={e => onChange(e.target.value ? Number(e.target.value) : null)}><option value="">Выбери поле</option>{subset.map(f => <option key={f.id} value={f.id}>{f.name}</option>)}</TemplateSelect>;

  const orderedPipelines = [...catalog.pipelines].sort((a, b) => (a.sort ?? catalog.pipelines.indexOf(a)) - (b.sort ?? catalog.pipelines.indexOf(b)));
  const orderedStages = orderedPipelines.flatMap(p => [...(p._embedded?.statuses ?? [])].sort((a, b) => (a.sort ?? 0) - (b.sort ?? 0)).map(s => ({ ...s, pipeline: p })));
  const ruleStages = (r: Rule) => orderedStages.filter(s => r.pipelines.includes(s.pipeline.id) && (!r.statuses.length || r.statuses.includes(s.id)));
  const stageLabel = (r: Rule) => r.statuses.length ? ruleStages(r).map(s => `${s.pipeline.name} · ${s.name}`).join(", ") || "Этап отсутствует в CRM" : `${orderedPipelines.filter(p => r.pipelines.includes(p.id)).map(p => p.name).join(", ")} · все открытые этапы`;
  const stateOf = (d: Doc) => !d.active ? "draft" : !(d.active as Rule).enabled ? "off" : "on";
  const visibleRules = docs.filter(d => {
    if (d.kind !== "rule") return false;
    const r = d.draft as Rule;
    return `${r.name} ${stageLabel(r)}`.toLowerCase().includes(search.trim().toLowerCase()) && (!pipelineFilter || r.pipelines.includes(Number(pipelineFilter))) && (!stageFilter || ruleStages(r).some(s => String(s.id) === stageFilter)) && (!stateFilter || (stateFilter === "changed" ? JSON.stringify(d.active) !== JSON.stringify(d.draft) : stateOf(d) === stateFilter)) && (!triggerFilter || r.trigger.type === triggerFilter);
  }).sort((a, b) => {
    const rank = (d: Doc) => { const r = d.draft as Rule; if (!r.statuses.length) return Number.MAX_SAFE_INTEGER; const index = orderedStages.findIndex(s => r.pipelines.includes(s.pipeline.id) && (!r.statuses.length || r.statuses.includes(s.id))); return index < 0 ? Number.MAX_SAFE_INTEGER : index; };
    return rank(a) - rank(b) || (a.draft as Rule).name.localeCompare((b.draft as Rule).name, "ru");
  });
  const filtersActive = Boolean(pipelineFilter || stageFilter || stateFilter || triggerFilter || search);

  if (campaignOpen) return <CampaignPanel editable={editable} back={() => setCampaignOpen(false)} />;
  return <div className="space-y-5 text-white">
    <div className="flex flex-wrap items-center justify-between gap-3"><h1 className="text-2xl font-extrabold">Шаблоны сообщений</h1><button className={button} disabled={dirty} onClick={() => setCampaignOpen(true)}>Рассылка от 10.11</button><button className={button} disabled={busy} onClick={() => void action(async () => { await refresh(); setJournal((await api.get<{ items: Journal[] }>(root + "/journal")).items); setRemote((await api.get<{ items: Remote[] }>(root + "/manual")).items); })}>Обновить</button></div>
    <div className="flex flex-wrap gap-2">{([ ["rule", "Автоматические"], ["manual", "Ручные"], ["routing", "Порядок отправки"] ] as const).map(([key, label]) => <button key={key} className={`${button} ${tab === key ? "bg-gold/20 text-gold-soft" : ""}`} onClick={() => switchTab(key)}>{label}</button>)}</div>
    {!editable && <p className="text-mute">Просмотр. Изменять настройки может только администратор.</p>}
    {tab !== "manual" && executor !== "live" && <div className="rounded-xl border border-amber-600 p-3 text-amber-200">Сохранённые версии пока не подключены к отправке. Можно подготовить и проверить изменения; для применения требуется подключить управление рассылкой.</div>}
    {(error || notice) && <div role={error ? "alert" : "status"} className={`card flex items-start gap-3 border p-4 shadow-lg ${error ? "border-red-400/40" : "border-gold/30"}`}>
      {error ? <AlertCircle size={20} className="mt-0.5 shrink-0 text-red-300" /> : <CheckCircle2 size={20} className="mt-0.5 shrink-0 text-gold" />}<p className="min-w-0 flex-1 text-sm">{error || notice}</p><button aria-label="Закрыть уведомление" onClick={() => { setError(""); setNotice(""); }} className="rounded-lg p-1 text-mute hover:bg-ink-700"><X size={16} /></button>
    </div>}
    <Modal open={Boolean(dialog)} onClose={() => closeDialog(false)} title="Подтверждение"><p className="mb-6 text-sm leading-relaxed text-mute-soft">{dialog?.message}</p><div className="flex justify-end gap-2"><Button variant="outline" onClick={() => closeDialog(false)}>Отмена</Button><Button onClick={() => closeDialog(true)}>{dialog?.confirm}</Button></div></Modal>
    {tab === "rule" && journal.some(j=>j.gift_lead_id && ["partial","unknown","blocked"].includes(j.state??"")) && <div role="alert" className="card flex flex-wrap items-center gap-3 border border-amber-500/40 p-4"><AlertCircle size={20}/><p className="flex-1 text-sm">Есть сертификаты с блокировкой или незавершённой доставкой. Проверь журнал перед повторной отправкой.</p><button className={button} onClick={()=>setJournalOpen(true)}>Показать журнал</button></div>}
    {busy && <p role="status" className="text-mute">Обработка…</p>}
    <div className="grid gap-5 lg:grid-cols-[300px_minmax(0,1fr)]">
      <aside className="space-y-3">
        {tab !== "routing" && <input className={input} aria-label="Поиск шаблонов" placeholder="Поиск шаблонов" value={search} onChange={e => setSearch(e.target.value)} />}
        {editable && tab !== "routing" && <button className={button} disabled={busy} onClick={() => open({ id: crypto.randomUUID(), kind: tab, version: 0, draft: tab === "rule" ? newRule() : { name: "Новый шаблон", content: "" }, active: null, checked: false })}>Добавить шаблон</button>}
        {tab === "rule" && <>
          <div className="space-y-2 rounded-xl border border-ink-600 p-3"><div className="flex items-center gap-2 text-xs font-semibold text-mute"><SlidersHorizontal size={14} />Фильтры</div>
            <Select ariaLabel="Фильтр по воронке" value={pipelineFilter} onChange={v => { setPipelineFilter(v); setStageFilter(""); }} options={[{ value: "", label: "Все воронки" }, ...orderedPipelines.map(p => ({ value: String(p.id), label: p.name }))]} />
            <Select ariaLabel="Фильтр по этапу" value={stageFilter} onChange={setStageFilter} options={[{ value: "", label: "Все этапы" }, ...orderedStages.filter(s => !pipelineFilter || String(s.pipeline.id) === pipelineFilter).map(s => ({ value: String(s.id), label: s.name, hint: s.pipeline.name }))]} />
            <Select ariaLabel="Фильтр по состоянию" value={stateFilter} onChange={setStateFilter} options={[{ value: "", label: "Все состояния" }, { value: "on", label: "Включены" }, { value: "off", label: "Выключены" }, { value: "draft", label: "Не опубликованы" }, { value: "changed", label: "Есть изменения в черновике" }]} />
            <Select ariaLabel="Фильтр по запуску" value={triggerFilter} onChange={setTriggerFilter} options={[{ value: "", label: "Любой запуск" }, { value: "stage", label: "После перехода на этап" }, { value: "date", label: "По дате" }]} />
            {filtersActive && <button className="text-xs text-mute underline hover:text-white" onClick={() => { setPipelineFilter(""); setStageFilter(""); setStateFilter(""); setTriggerFilter(""); setSearch(""); }}>Сбросить фильтры</button>}
          </div>
          <p className="text-xs text-mute">Найдено: {visibleRules.length} · по порядку этапов CRM</p>
          {visibleRules.map(d => <button key={d.id} className={`block w-full rounded-xl border p-3 text-left transition-colors hover:bg-ink-800 ${selected?.id === d.id ? "border-gold bg-gold/5" : "border-ink-600"}`} onClick={() => void open(d)}><span className="font-semibold">{(d.draft as Rule).name}</span><span className="mt-2 block text-xs text-mute-soft">{stageLabel(d.draft as Rule)}</span><span className="mt-2 flex flex-wrap items-center gap-2 text-xs text-mute"><span className="rounded-md bg-ink-700 px-2 py-0.5">{(d.draft as Rule).trigger.type === "date" ? "По дате" : "При переходе"}</span>{!d.active ? "Черновик" : !(d.active as Rule).enabled ? "Выключен" : executor !== "live" ? "Версия не подключена к отправке" : "Работает"}{d.active && JSON.stringify(d.active) !== JSON.stringify(d.draft) ? " · есть черновик" : ""}</span></button>)}
          {!visibleRules.length && <p className="py-4 text-sm text-mute">Нет шаблонов по выбранным фильтрам.</p>}
        </>}
        {tab === "manual" && remote.filter(x => `${x.name} ${x.content}`.toLowerCase().includes(search.trim().toLowerCase())).sort((a, b) => a.name.localeCompare(b.name, "ru")).map(x => { const d = docs.find(item => item.kind === "manual" && (item.draft as Manual).remote_id === x.id); const draft = d?.draft as Manual | undefined; const note = d?.crm?.state === "rejected" ? "amoCRM не приняла" : d?.crm?.state === "uncertain" ? "нет ответа от amoCRM" : draft?.delete ? "удаление не сохранено" : draft && (draft.name !== x.name || draft.content !== x.content) ? "есть несохранённое" : ""; return <button key={x.id} className={`${button} block w-full text-left`} onClick={() => { open(d && (draft?.delete || draft?.name !== x.name || draft?.content !== x.content || d.crm?.state === "rejected" || d.crm?.state === "uncertain") ? d : { id: d?.id ?? `manual-${x.id}`, kind: "manual", version: d?.version ?? 0, published_version: d?.published_version ?? 0, draft: { remote_id: x.id, name: x.name, content: x.content, source_hash: x.source_hash, delete: false }, active: d?.active ?? null, checked: false, crm: d?.crm ?? null }); }}>{x.name}{x.type !== "amocrm" && <span className="block text-xs text-mute">Особый тип: только просмотр</span>}{note && <span className="block text-xs text-amber-200">{note}</span>}</button>; })}
        {tab === "manual" && docs.filter(d => d.kind === "manual" && !(d.draft as Manual).remote_id && !d.active && (d.draft as Manual).name.toLowerCase().includes(search.trim().toLowerCase())).map(d => <button key={d.id} className={`${button} block w-full text-left`} onClick={() => open(d)}>{(d.draft as Manual).name} · черновик</button>)}
        {tab === "routing" && <p className="text-sm text-mute">Один порядок для всех автоматических сообщений. Изменения вступают в силу после проверки и публикации.</p>}
      </aside>
      <section className="min-w-0 space-y-4">
        {!selected && <p className="text-mute">Выбери шаблон или создай новый.</p>}
        {selected && <>
          <fieldset disabled={!editable || busy || manualReadOnly} className="min-w-0 space-y-4 disabled:opacity-80">
            {(rule || manual) && <label className="block space-y-1"><span>Название</span><input className={input} value={(rule || manual)?.name ?? ""} onChange={e => edit({ name: e.target.value })} /></label>}
            {manual && <p className={`text-sm ${manualStatus.startsWith("Сейчас") ? "text-mute" : "text-amber-200"}`}>{manualStatus}</p>}
            {manual && remoteNow && !remoteNow.external_id && <p className="text-sm text-amber-200">Этот шаблон создан в amoCRM. Касса может менять только шаблоны, созданные здесь. Этот текст меняется в amoCRM.</p>}
            {manual && remoteNow && (dirty || manual.delete || manual.source_hash !== remoteNow.source_hash || manual.name !== remoteNow.name || manual.content !== remoteNow.content) && <button type="button" className={button} disabled={busy} onClick={discardManual}>Загрузить текущий из amoCRM</button>}
            {rule && <>
              <div className="rounded-xl bg-ink-800 p-3 text-sm"><p className="mb-2 text-xs text-mute">Используется на этапах</p><div className="flex flex-wrap gap-2">{rule.statuses.length ? ruleStages(rule).map(s => <span key={`${s.pipeline.id}-${s.id}`} title={s.pipeline.name}><StageBadge stage={s.name} /></span>) : <span>{stageLabel(rule)}</span>}</div></div>
              <label className="flex gap-2"><input type="checkbox" checked={rule.enabled} onChange={e => edit({ enabled: e.target.checked })} />Включить после публикации</label>
              {!isGift && <><div className="grid gap-3 sm:grid-cols-2"><label>Воронки<select multiple className={`${input} h-32`} value={rule.pipelines.map(String)} onChange={e => edit({ pipelines: Array.from(e.target.selectedOptions, x => Number(x.value)), statuses: [] })}>{catalog.pipelines.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label><label>Этапы<select multiple className={`${input} h-32`} value={rule.statuses.map(String)} onChange={e => edit({ statuses: Array.from(e.target.selectedOptions, x => Number(x.value)) })}>{stages.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</select><span className="text-xs text-mute">Для даты: пустой выбор означает все открытые этапы.</span></label></div>
              <div className="space-y-3 rounded-xl border border-ink-600 p-3"><h2 className="font-semibold">Условия</h2><TemplateSelect className={input} value={rule.match} onChange={e => edit({ match: e.target.value as "all" | "any" })}><option value="all">Все выполнены</option><option value="any">Хотя бы одно выполнено</option></TemplateSelect>
                {rule.conditions.map((c, i) => { const f = fields.find(f => f.id === c.field); const update = (patch: Partial<Condition>) => edit({ conditions: rule.conditions.map((v, n) => n === i ? { ...v, ...patch } : v) }); return <div key={i} className="grid gap-2 sm:grid-cols-[1fr_1fr_1fr_auto]">{fieldSelect(c.field, n => update({ field: n ?? 0, value: null, op: "eq" }))}<TemplateSelect className={input} value={c.op} onChange={e => update({ op: e.target.value })}>{[["eq", "Равно"], ["ne", "Не равно"], ["empty", "Пусто"], ["filled", "Заполнено"], ...(f?.enums?.length ? [] : ["numeric", "price", "date", "date_time"].includes(f?.type ?? "") ? [["gt", "Больше"], ["gte", "Не меньше"], ["lt", "Меньше"], ["lte", "Не больше"]] : [["contains", "Содержит"]])].map(([v, label]) => <option key={v} value={v}>{label}</option>)}</TemplateSelect>{!["empty", "filled"].includes(c.op) && (f?.enums?.length ? <TemplateSelect className={input} value={String(c.value ?? "")} onChange={e => update({ value: e.target.value ? Number(e.target.value) : null })}><option value="">Значение</option>{f.enums.map(v => <option key={v.id} value={v.id}>{v.value}</option>)}</TemplateSelect>  : f?.type === "checkbox" ? <TemplateSelect className={input} value={String(c.value ?? "")} onChange={e => update({ value: e.target.value === "" ? null : e.target.value === "true" })}><option value="">Выбери значение</option><option value="true">Да</option><option value="false">Нет</option></TemplateSelect> : ["date", "date_time"].includes(f?.type ?? "") ? <DatePicker className={input} value={c.value ? new Date(Number(c.value) * 1000 + 3 * 3600000).toISOString().slice(0, 10) : ""} onChange={value => update({ value: value ? Date.parse(value + "T00:00:00+03:00") / 1000 : null })} /> : <input type={["numeric", "price"].includes(f?.type ?? "") ? "number" : "text"} className={input} value={String(c.value ?? "")} aria-label="Значение условия" onChange={e => update({ value: ["numeric", "price", "date", "date_time"].includes(f?.type ?? "") ? e.target.value === "" ? null : Number(e.target.value) : f?.type === "checkbox" ? e.target.value === "true" : e.target.value })} />)}<button className={button} onClick={() => edit({ conditions: rule.conditions.filter((_, n) => n !== i) })}>Убрать</button></div>; })}
                <button className={button} onClick={() => edit({ conditions: [...rule.conditions, { field: fields[0]?.id ?? 0, op: "eq", value: null }] })}>Добавить условие</button></div>
              <div className="space-y-3 rounded-xl border border-ink-600 p-3"><h2 className="font-semibold">Когда отправлять</h2><TemplateSelect className={input} value={rule.trigger.type} onChange={e => edit({ trigger: { ...rule.trigger, type: e.target.value as "date" | "stage" } })}><option value="date">Относительно поля даты</option><option value="stage">После перехода на этап</option></TemplateSelect>
                {rule.trigger.type === "date" ? <><label className="block">Поле даты{fieldSelect(rule.trigger.field, n => edit({ trigger: { ...rule.trigger, field: n } }), fields.filter(f => ["date", "date_time"].includes(f.type)))}</label><div className="grid gap-2 sm:grid-cols-3"><label>Смещение<input type="number" className={input} value={rule.trigger.offset} onChange={e => edit({ trigger: { ...rule.trigger, offset: Number(e.target.value) } })} /></label><label>Единица<TemplateSelect className={input} value={rule.trigger.unit} onChange={e => edit({ trigger: { ...rule.trigger, unit: e.target.value as Trigger["unit"] } })}><option value="days">Дней</option><option value="weeks">Недель</option><option value="months">Календарных месяцев</option></TemplateSelect></label><label>Время МСК<input type="time" className={input} value={rule.trigger.time} onChange={e => edit({ trigger: { ...rule.trigger, time: e.target.value } })} /></label></div><p className="text-sm text-mute">0 — день в день, отрицательное число — до даты, положительное — после. Пропущенное время не догоняется.</p></> : <><label className="block">Задержка, минут<input type="number" min={0} className={input} value={rule.trigger.delay_minutes} onChange={e => edit({ trigger: { ...rule.trigger, delay_minutes: Number(e.target.value) } })} /></label><TemplateSelect className={input} value={rule.trigger.repeat} onChange={e => edit({ trigger: { ...rule.trigger, repeat: e.target.value as Trigger["repeat"] } })}><option value="each_entry">Каждый новый вход</option><option value="once">Один раз на сделку</option></TemplateSelect></>}
                <div className="grid grid-cols-2 gap-2"><label>Окно с, МСК<input className={input} type="number" min={0} max={23} value={rule.window_start} onChange={e => edit({ window_start: Number(e.target.value) })} /></label><label>Окно до, МСК<input className={input} type="number" min={1} max={24} value={rule.window_end} onChange={e => edit({ window_end: Number(e.target.value) })} /></label></div>
                {rule.legacy_next_day && <p className="text-xs text-amber-200">Сохранён прежний перенос события вне окна на следующий день.</p>}</div>
              <label className="block">Получатель<TemplateSelect className={input} value={rule.recipient} onChange={e => edit({ recipient: e.target.value as Rule["recipient"] })}><option value="contact">Основной контакт сделки</option><option value="field">Телефон из поля сделки</option></TemplateSelect></label>
              {rule.recipient === "field" && <label className="block">Поле телефона{fieldSelect(rule.phone_field, n => edit({ phone_field: n }))}<span className="text-xs text-mute">Контакт покупателя не используется как запасной получатель.</span></label>}</>}
              {isGift && <div className="space-y-3 rounded-xl border border-ink-600 p-4"><p className="text-sm text-mute">Только электронный сертификат после оплаты. Номер закрепляет касса. Текст и PDF отправляются одному получателю по полю Номер получателя, без подмены покупателем.</p><label className="block">Время в дату начала, МСК<input type="time" className={input} value={rule.trigger.time} onChange={e=>edit({trigger:{...rule.trigger,time:e.target.value}})} /></label><label className="block">Вступление внутри PDF<textarea className={input} value={rule.certificate_pdf?.intro??""} onChange={e=>edit({certificate_pdf:{...rule.certificate_pdf,intro:e.target.value}})} /></label><label className="block">Инструкция внутри PDF<textarea className={`${input} min-h-24`} value={rule.certificate_pdf?.instructions??""} onChange={e=>edit({certificate_pdf:{...rule.certificate_pdf,instructions:e.target.value}})} /></label>{Object.entries(rule.certificate_pdf?.contacts??{}).map(([key,value])=><label key={key} className="block text-sm">{{telegram:"Telegram",whatsapp:"WhatsApp",max:"MAX",pyat:"Яндекс.Карты · Пятницкая",baum:"Яндекс.Карты · Бауманская",site:"Сайт"}[key]??key}<input className={input} value={value} type="url" onChange={e=>edit({certificate_pdf:{...rule.certificate_pdf,contacts:{...rule.certificate_pdf?.contacts,[key]:e.target.value}}})} /></label>)}<p className="text-xs text-mute">Имя поздравителя — имя основного контакта покупателя. Поздравление в PDF — поле Поздравление от купившего. Пустой блок поздравления скрывается.</p></div>}
            </>}
            {(rule || manual) && <label className="block">Текст сообщения<textarea className={`${input} min-h-40`} value={(rule || manual)?.content ?? ""} onChange={e => edit({ content: e.target.value })} /></label>}
            {rule && !isGift && <><label className="block">Вставить переменную<TemplateSelect className={input} value="" onChange={e => { if (e.target.value) edit({ content: rule.content + e.target.value }); }}><option value="">Выбери переменную</option><option value="{{lead.id}}">ID сделки</option><option value="{{lead.price}}">Бюджет сделки</option>{fields.map(f => <option key={f.id} value={`{{lead.${["date", "date_time"].includes(f.type) ? "date" : "field"}.${f.id}}}`}>{f.name}</option>)}</TemplateSelect></label>
              <div className="space-y-3"><h2 className="font-semibold">Варианты по магазину</h2>{Object.entries(rule.variants).map(([key, value]) => <label className="block" key={key}>{key === "default" ? "Магазин не определён" : fields.find(f => f.id === rule.store_field)?.enums?.find(e => String(e.id) === key)?.value ?? key}<textarea className={`${input} min-h-32`} value={value} onChange={e => edit({ variants: { ...rule.variants, [key]: e.target.value } })} /><button className={button} onClick={() => edit({ variants: Object.fromEntries(Object.entries(rule.variants).filter(([k]) => k !== key)) })}>Убрать вариант</button></label>)}<TemplateSelect className={input} value="" onChange={e => { if (e.target.value) edit({ variants: { ...rule.variants, [e.target.value]: rule.content } }); }}><option value="">Добавить вариант</option>{(fields.find(f => f.id === rule.store_field)?.enums ?? []).filter(e => !rule.variants[String(e.id)]).map(e => <option key={e.id} value={e.id}>{e.value}</option>)}{!("default" in rule.variants) && <option value="default">Магазин не определён</option>}</TemplateSelect></div>
              <p className="rounded-xl bg-ink-800 p-3 text-sm">Каналы: {activeRouting?.previous_first ? "прежний Telegram, WhatsApp или MAX → " : ""}{(activeRouting?.order ?? ["telegram", "whatsapp", "max"]).map(c => channelNames[c]).join(" → ")}{activeRouting?.social_fallback ? " → исходный Instagram или Авито" : ""}. <button className="underline" onClick={() => switchTab("routing")}>Изменить общий порядок</button></p>
            </>}
            {routing && <><label className="flex gap-2"><input type="checkbox" checked={routing.previous_first} onChange={e => edit({ previous_first: e.target.checked })} />Начинать с прежнего Telegram, WhatsApp или MAX</label>{routing.order.map((channel, index) => <div key={channel} className="flex items-center gap-3 rounded-xl border border-ink-600 p-3"><span className="flex-1">{index + 1}. {channelNames[channel]}</span>{[-1, 1].map(delta => <button key={delta} className={button} disabled={index + delta < 0 || index + delta >= routing.order.length} onClick={() => { const order = [...routing.order]; [order[index], order[index + delta]] = [order[index + delta], order[index]]; edit({ order }); }}>{delta === -1 ? "Выше" : "Ниже"}</button>)}</div>)}<label className="flex gap-2"><input type="checkbox" checked={routing.social_fallback} onChange={e => edit({ social_fallback: e.target.checked })} />Использовать исходный Instagram или Авито после мессенджеров</label><p className="text-sm text-mute">Следующий канал — только после подтверждённого отказа. Таймаут и неизвестный результат останавливают цепочку.</p></>}
          </fieldset>
          {rule && <label className="block">ID сделки для предпросмотра (для PDF пусто — тестовый образец)<input className={input} type="number" min={1} value={leadId} onChange={e => { setLeadId(e.target.value); setChecked(null); }} placeholder="Пусто — проверить выборку" /></label>}
          {isGift && <><p className="text-sm text-mute">Переменная имени покупателя: <code>{"{{buyer.name}}"}</code>. Общий порядок мессенджеров берётся из вкладки Порядок отправки.</p><button className={button} disabled={busy || dirty || !selected.version} onClick={()=>void action(async()=>{
            const res=await fetch(`/api/message-templates/gift/preview/${Number(leadId)||0}`,{headers:{Authorization:`Bearer ${getToken()}`}});
            if(!res.ok){const error=await res.json();throw new Error(error.message??"Не удалось подготовить PDF");}
            const url=URL.createObjectURL(await res.blob());const a=document.createElement("a");a.href=url;a.download="Mansband-preview.pdf";a.click();setTimeout(()=>URL.revokeObjectURL(url),60000);setNotice("Предпросмотр PDF подготовлен без выпуска номера и отправки.");
          })}>Скачать предпросмотр PDF</button></>}
          {stepHint && <p className="text-sm text-mute">{stepHint}</p>}
          <div className="flex flex-wrap gap-2">{editable && !manualReadOnly && (manual ? <><button className={`${button} bg-gold/20`} disabled={saveManualDisabled} onClick={saveManual}>Сохранить в amoCRM</button>{manual.remote_id && <button className={`${button} text-red-300`} disabled={busy} onClick={removeManual}>Удалить из amoCRM</button>}</> : <><button className={button} disabled={busy || !dirty && selected.version > 0} onClick={save}>Сохранить черновик</button><button className={button} disabled={busy || dirty || !selected.version} onClick={check}>Проверить без отправки</button><button className={`${button} bg-gold/20`} disabled={busy || dirty || !checked || Boolean(checked.overlaps?.length && !confirmOverlap)} onClick={publish}>Опубликовать</button></>)}<button className={button} disabled={busy || !selected.version} onClick={() => void action(async () => setHistory((await api.get<{ items: Revision[] }>(root + "/history/" + selected.id)).items))}>История версий</button></div>
          {manual && error && <p role="alert" className="text-sm text-red-300">{error}</p>}
          {editable && rule && checked && <div className="space-y-2 rounded-xl border border-ink-600 p-3"><h2 className="font-semibold">Тестовая отправка</h2><p className="text-sm text-mute">Отдельное сообщение с пометкой ТЕСТ. Укажи ID сделки для заполнения переменных и свой тестовый адресат.</p><TemplateSelect className={input} value={testChannel} onChange={e => { setTestChannel(e.target.value as Channel); setTestOperation(crypto.randomUUID()); }}>{Object.entries(channelNames).map(([v, label]) => <option value={v} key={v}>{label}</option>)}</TemplateSelect><input className={input} aria-label="Тестовый получатель" placeholder="Телефон или Telegram username" value={testDestination} onChange={e => { setTestDestination(e.target.value); setTestOperation(crypto.randomUUID()); }} /><button className={button} disabled={busy || dirty || (!isGift && !leadId) || !testDestination} onClick={() => void action(async () => { if (!await ask(`Отправить ТЕСТ через ${channelNames[testChannel]} получателю ${testDestination}?`, "\u041e\u0442\u043f\u0440\u0430\u0432\u0438\u0442\u044c \u0442\u0435\u0441\u0442")) return; const result = await api.post<{ job_id: number; state: string }>((isGift ? root + "/gift/test" : root + "/test/" + selected.id), { version: selected.version, lead_id: Number(leadId), transport: testChannel, destination: testDestination, operation_id: testOperation, confirmed: true }); setNotice(`Тестовое задание ${result.job_id}: ${result.state}. Повтор не запускается автоматически.`); setJournal((await api.get<{ items: Journal[] }>(root + "/journal")).items); })}>Отправить тест</button></div>}
          {checked && <div className="space-y-3 rounded-xl border border-ink-600 p-3"><h2 className="font-semibold">Проверка без отправки</h2>{checked.total !== undefined && <p>Подходящих сделок: {checked.total}. Ниже показано до {checked.sample_limit ?? 10} примеров.</p>}{checked.text && <p className="whitespace-pre-wrap">{checked.text}</p>}{Boolean(checked.overlaps?.length) && <label className="block text-amber-200"><input type="checkbox" disabled={!editable} checked={confirmOverlap} onChange={e => setConfirmOverlap(e.target.checked)} /> Подтверждаю намеренное пересечение: {checked.overlaps?.map(x => x.name).join(", ")}</label>}{checked.samples?.map(s => <div className="border-t border-ink-600 pt-2" key={s.lead_id}><p>Сделка {s.lead_id} · {s.due_at ? new Date(s.due_at).toLocaleString("ru-RU", { timeZone: "Europe/Moscow" }) + " МСК" : "новый переход"}</p>{s.reason && <p className="text-amber-200">{s.reason}</p>}<p className="text-xs text-mute">{s.channels?.join(" → ")}</p><p className="whitespace-pre-wrap">{s.text}</p></div>)}</div>}
          {history.length > 0 && <div className="space-y-2"><h2 className="font-semibold">История</h2>{history.map((h, i) => <div key={i} className="rounded-xl border border-ink-600 p-3"><p>Версия {h.version} · {h.actor} · {h.action} · {new Date(h.at).toLocaleString("ru-RU", { timeZone: "Europe/Moscow" })}</p>{editable && <button className={button} onClick={() => { const payload = "document" in h.payload ? h.payload.document : h.payload; if (selected.kind === "manual") { setError("Для ручного шаблона открой актуальную версию amoCRM и перенеси прежний текст, чтобы сохранить проверку конфликтов."); return; } edit(payload as Payload); }}>Восстановить в черновик</button>}</div>)}</div>}
        </>}
      </section>
    </div>
    {tab === "rule" && <section className="overflow-hidden rounded-xl border border-ink-600">
      <button className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-ink-800" aria-expanded={journalOpen} aria-controls="template-journal" onClick={() => setJournalOpen(v => !v)}><ChevronDown size={18} className={`transition-transform ${journalOpen ? "rotate-180" : ""}`} /><span className="flex-1 text-sm font-semibold">Журнал отправок</span><span className="text-xs text-mute">{journal.length} записей</span></button>
      {journalOpen && <div id="template-journal" className="max-h-80 overflow-y-auto border-t border-ink-600 px-4">{!journal.length && <p className="py-3 text-sm text-mute">Пока нет заданий.</p>}{journal.map(j => <details key={j.id} className="group border-b border-ink-600 last:border-0"><summary className="flex cursor-pointer list-none flex-wrap items-center gap-x-3 gap-y-1 py-2 text-xs"><ChevronDown size={14} className="shrink-0 transition-transform group-open:rotate-180" /><time className="text-mute">{new Date(j.due_at).toLocaleString("ru-RU", { timeZone: "Europe/Moscow", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}</time><span className="min-w-0 flex-1">{(docs.find(d => d.id === j.rule_id)?.draft as Rule | undefined)?.name ?? j.rule_id}</span><span className="text-mute">Сделка {j.lead_id}</span><span className="rounded-md bg-ink-700 px-2 py-1">{j.label}</span></summary><div className="space-y-1 pb-3 pl-6 text-xs text-mute">{j.reason && <p>{j.reason}</p>}{editable && j.state==="partial" && j.gift_lead_id && j.gift_lead_id>0 && <button className={button} disabled={busy} onClick={()=>void action(async()=>{
              if(!await ask("Дослать только PDF в уже выбранный мессенджер? Текст повторно не отправится.","Дослать PDF"))return;
              await api.post(root+"/gift/retry-pdf/"+j.gift_lead_id,{confirmed:true});setNotice("Досылка PDF поставлена в очередь.");setJournal((await api.get<{items:Journal[]}>(root+"/journal")).items);
            })}>Дослать PDF</button>}{!j.attempts.length && <p>Попыток отправки пока нет.</p>}{j.attempts.map((a, i) => <p key={i}>{channelNames[a.channel as Channel] ?? a.channel}: {a.state} {a.reason}</p>)}</div></details>)}</div>}
    </section>}
  </div>;
}

type CampaignItem = {
  id: number; name: string; phone_tail: string; segment: number; state: string; reason: string;
  lead_id: number; contact_id: number; matched_contact_ids: number[]; classification: string;
  can_exclude: boolean; can_classify: boolean; chat_history: string; sent_at: string | null; replied_at: string | null;
  purchases: {id: string; linked: boolean; net_kopecks: number; document_id: string}[];
};
type CampaignSegment = {segment: number; total: number; sent: number; processed: number; attention: number; existing: number; checked: number};
type CampaignQueue = {
  mode: string; segment: number | null; day_budget: number; today_started: number; purchase_sync_fresh: boolean;
  server_time: string; start_day: string | null; next_window: string | null; offer_expired: boolean; halt_reason: string | null;
  segments: CampaignSegment[]; total: number; page_size: number; channel_usage: Record<string,number>; channel_budgets: Record<string,number>; items: CampaignItem[];
};
type CampaignTotals = {recipients:number; text_sent:number; complete:number; partial:number; replied:number; responses:Record<string,number>; observed_purchases:number; linked_purchases:number; buyers:number; net_kopecks:number; attention:number; excluded:number; reply_rate:number|null; reply_activity_today:number};
type CampaignStats = {total:CampaignTotals; today:CampaignTotals};
const campaignCategories:Record<string,string>={interest:"Заинтересовался",refusal:"Отказался",unsubscribe:"Отписался",review:"Нужно уточнить"};
const campaignStates:Record<string,string>={queued:"В очереди",prepared:"Готов к отправке",complete:"Текст и фото отправлены",attention:"Нужна проверка",excluded:"Исключён",duplicate:"Дубликат",unreachable:"Недоступен в мессенджерах",pending:"Ждём подтверждения",inflight:"Отправляется",photo_ready:"Текст отправлен, ожидает фото",reply_handoff:"Передан менеджеру",crm_inflight:"Проверяем создание сделки"};
const campaignReasons:Record<string,string>={
  ambiguous_crm_contacts:"На этот номер найдено несколько контактов в CRM. Перед отправкой нужно проверить, какая карточка принадлежит клиенту.",
  shared_crm_or_messenger_identity:"Этот клиент уже учтён в другой записи. Повторная отправка исключена.",
  contact_phone_changed:"Телефон в CRM изменился. Нужно проверить, что это тот же клиент.",
  interrupted_or_unknown_send:"Результат отправки неизвестен. Сначала проверь доставку в мессенджере; повторять сообщение нельзя.",
  network_result_unknown:"Связь прервалась после запроса. Сообщение могло уйти. Нужна проверка доставки.",
  provider_confirmation_missing:"Мессенджер ещё не подтвердил отправку. Автоматический повтор остановлен.",
  provider_delivery_error:"Мессенджер сообщил об ошибке доставки. Нужна техническая проверка.",
  channel_not_active:"Канал отправки недоступен. Отправка остановлена до технической проверки.",
  asynchronous_provider_restriction:"Мессенджер сообщил об ограничении. Очередь остановлена.",
  photo_delay_exceeded:"Фото не удалось отправить вовремя. Текст повторно не отправляется.",
  photo_next_day_forbidden:"Фото осталось с прошлого дня. Автоматическая досылка остановлена.",
  crm_creation_requires_reconciliation:"Нужно проверить, создалась ли сделка в CRM. Повторное создание остановлено.",
  crm_contact_creation_requires_reconciliation:"Нужно проверить, создался ли контакт в CRM. Повторное создание остановлено.",
  campaign_stage_changed:"Менеджер изменил этап сделки. Автоматическая отправка остановлена.",
  no_verified_destination:"Не найден подтверждённый адресат в мессенджерах.",
  all_recipients_absent:"Клиент не найден в доступных мессенджерах.",
  operator_excluded:"Получатель исключён вручную.", offer_expired:"Срок предложения закончился. Для продолжения нужны новые утверждённые шаблоны.",
  inactive_shift_owner:"Ответственный по графику недоступен в CRM. Нужно проверить смену.",
};
const campaignReason = (reason:string) => campaignReasons[reason] || (reason ? "Автоматическая отправка приостановлена. Нужна техническая проверка этой записи." : "");
const count = (n:number|undefined) => n === undefined ? "—" : n.toLocaleString("ru-RU");
const campaignDate = (value:string|null|undefined, time=true) => value ? new Date(value).toLocaleString("ru-RU",{timeZone:"Europe/Moscow",day:"numeric",month:"long",...(time?{hour:"2-digit",minute:"2-digit"}: {})}) : "—";
const campaignFilters = [{value:"",label:"Все получатели"},{value:"queued",label:"В очереди"},{value:"sent",label:"Получили сообщение"},{value:"delivered",label:"Получили текст и фото"},{value:"replies",label:"Ответили"},{value:"interest",label:"Заинтересовались"},{value:"refusal",label:"Отказались"},{value:"unsubscribe",label:"Отписались"},{value:"buyers",label:"Купили после рассылки"},{value:"excluded",label:"Исключены"}];

function CampaignPanel({editable,back}:{editable:boolean;back:()=>void}) {
  const [queue,setQueue]=useState<CampaignQueue|null>(null),[stats,setStats]=useState<CampaignStats|null>(null);
  const [view,setView]=useState<"overview"|"recipients"|"attention">("overview");
  const [filter,setFilter]=useState(""),[segment,setSegment]=useState("0"),[page,setPage]=useState(1);
  const [search,setSearch]=useState(""),[query,setQuery]=useState("");
  const [loading,setLoading]=useState(true),[busy,setBusy]=useState(false),[error,setError]=useState("");
  const [notice,setNotice]=useState(""),[modalError,setModalError]=useState("");
  const [reason,setReason]=useState(""),[selected,setSelected]=useState<CampaignItem|null>(null),[category,setCategory]=useState("review");
  const [command,setCommand]=useState<"pause"|"resume"|null>(null),[recipientAction,setRecipientAction]=useState("classification");
  const generation=useRef(0);
  useEffect(()=>{const timer=window.setTimeout(()=>{setQuery(search.trim());setPage(1);},350);return()=>window.clearTimeout(timer);},[search]);
  const load=useCallback(async(silent=false)=>{
    const request=++generation.current;if(!silent)setLoading(true);
    try {
      const params=new URLSearchParams({state:view==="attention"?"attention":filter,page:String(page),segment,search:query});
      const [q,s]=await Promise.all([api.get<CampaignQueue>(root+"/campaign/queue?"+params),api.get<CampaignStats>(root+"/campaign/stats")]);
      if(request===generation.current){setQueue(q);setStats(s);setError("");}
    } catch(e){if(request===generation.current)setError(e instanceof Error?e.message:"Не удалось получить данные");}
    finally{if(request===generation.current)setLoading(false);}
  },[view,filter,page,segment,query]);
  useEffect(()=>{void load();const timer=window.setInterval(()=>{if(!document.hidden)void load(true);},60000);return()=>{generation.current++;window.clearInterval(timer);};},[load]);
  const close=()=>{if(busy)return;setSelected(null);setCommand(null);setModalError("");setReason("");};
  useEffect(()=>{if(!selected&&!command)return;const escape=(e:KeyboardEvent)=>{if(e.key==="Escape"&&!busy){setSelected(null);setCommand(null);setModalError("");}};window.addEventListener("keydown",escape);return()=>window.removeEventListener("keydown",escape);},[selected,command,busy]);
  const navigate=(target:typeof view,nextFilter="",nextSegment="0")=>{setView(target);setFilter(nextFilter);setSegment(nextSegment);setPage(1);setSearch("");setQuery("");setNotice("");};
  const openRecipient=(j:CampaignItem)=>{setSelected(j);setCategory(j.classification||"review");setRecipientAction(j.can_classify?"classification":j.can_exclude?"exclude":"unsubscribe");setReason("");setModalError("");};
  const submit=async()=>{
    if(!reason.trim()||busy)return;
    setBusy(true);setModalError("");
    try {
      if(command)await api.post(root+"/campaign/control",{action:command,reason});
      else if(selected&&(recipientAction==="classification"||recipientAction==="unsubscribe"))await api.post(root+`/campaign/recipients/${selected.id}/classification`,{category:recipientAction==="unsubscribe"?"unsubscribe":category,reason});
      else if(selected)await api.post(root+"/campaign/control",{action:"exclude",reason,recipient_id:selected.id});
      setNotice(command==="pause"?"Рассылка поставлена на паузу.":command==="resume"?"Рассылка продолжится по расписанию.":"Изменение сохранено в журнале.");
      setSelected(null);setCommand(null);setReason("");await load();
    }catch(e){setModalError(e instanceof Error?e.message:"Не удалось сохранить изменение");}finally{setBusy(false);}
  };
  const t=stats?.total,day=stats?.today;
  const active=queue?.segments.find(s=>s.segment===queue.segment);
  const future=!!(queue?.start_day&&queue.start_day>new Date(queue.server_time).toLocaleDateString("sv-SE",{timeZone:"Europe/Moscow"}));
  const status=!queue?"Получаем состояние":queue.offer_expired?"Срок предложения завершён":queue.mode==="attention"?"Остановлена: нужна проверка":!queue.segment?"Очередь завершена":queue.mode!=="production"?"На паузе":future?"Запланирована":"Работает по расписанию";
  const pct=t&&t.recipients?Math.min(100,t.complete/t.recipients*100):0;
  const link=(id:number,kind="leads")=>`https://mansband.amocrm.ru/${kind}/detail/${id}`;
  const primary="inline-flex items-center justify-center gap-2 rounded-xl bg-white px-4 py-2.5 text-sm font-semibold text-ink-950 transition hover:opacity-85 disabled:opacity-40";
  const secondary="inline-flex items-center justify-center gap-2 rounded-xl border border-ink-600 px-3 py-2.5 text-sm transition hover:bg-ink-800 disabled:opacity-40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2";
  return <div className="mx-auto max-w-[1400px] space-y-6 pb-10 text-white">
    <header className="flex flex-wrap items-center justify-between gap-4">
      <div><button className="mb-3 inline-flex items-center gap-1.5 text-xs text-mute hover:text-white" onClick={back}><ArrowLeft size={14}/>Шаблоны сообщений</button><h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">Новая коллекция</h1><p className="mt-1 text-sm text-mute">Рассылка от 10.11 · MANSBAND</p></div>
      <div className="flex items-center gap-2"><button className={secondary} disabled={loading} onClick={()=>void load()} aria-label="Обновить данные рассылки"><RefreshCw size={16} className={loading?"animate-spin":""}/><span className="hidden sm:inline">Обновить</span></button>{editable&&queue&&queue.segment&&!queue.offer_expired&&<button className={secondary} disabled={!!error||queue.mode==="attention"} onClick={()=>{setCommand(queue.mode==="production"?"pause":"resume");setReason("");setModalError("");}}>{queue.mode==="production"?<Pause size={16}/>:<Play size={16}/>} {queue.mode==="production"?"Приостановить":"Продолжить"}</button>}</div>
    </header>
    {error&&<div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-500/40 bg-amber-500/10 p-4 text-sm"><div><p className="font-semibold">Не удалось обновить данные</p><p className="mt-1 text-mute">{queue?"Ниже последний полученный снимок. Актуальное состояние пока неизвестно.":"Состояние рассылки неизвестно. Это не означает, что она остановлена."}</p></div><button className={secondary} disabled={loading} onClick={()=>void load()}>Повторить</button></div>}
    {notice&&<p role="status" className="flex items-center gap-2 rounded-xl border border-ink-600 p-3 text-sm"><CheckCircle2 size={16}/>{notice}</p>}
    <nav aria-label="Разделы рассылки" className="flex gap-1 overflow-x-auto border-b border-ink-600">
      {([{id:"overview",label:"Обзор"},{id:"recipients",label:"Получатели"},{id:"attention",label:"Требуют внимания"}] as const).map(tab=><button key={tab.id} aria-current={view===tab.id?"page":undefined} onClick={()=>navigate(tab.id)} className={`flex shrink-0 items-center gap-2 border-b-2 px-4 pb-3 pt-2 text-sm transition ${view===tab.id?"border-white font-semibold text-white":"border-transparent text-mute hover:text-white"}`}>{tab.label}{tab.id==="attention"&&!!t?.attention&&<span className="rounded-md bg-ink-800 px-1.5 py-0.5 text-xs tabular-nums">{count(t.attention)}</span>}</button>)}
    </nav>
    {!queue&&!error&&<div className="space-y-4" role="status" aria-label="Загрузка рассылки"><div className="h-40 animate-pulse rounded-2xl bg-ink-850"/><div className="grid grid-cols-2 gap-4 md:grid-cols-4">{[1,2,3,4].map(n=><div key={n} className="h-28 animate-pulse rounded-2xl bg-ink-850"/>)}</div></div>}
    {queue&&t&&view==="overview"&&<>
      <section className="grid overflow-hidden rounded-2xl border border-ink-600 bg-ink-900 lg:grid-cols-[1.5fr_1fr]" aria-label="Состояние и расписание">
        <div className="p-5 sm:p-6"><div className="flex items-center gap-2 text-xs font-medium"><span className={`h-2 w-2 rounded-full ${error?"bg-ink-500":queue.mode==="attention"?"bg-amber-500":queue.mode==="production"?"bg-emerald-500":"bg-mute"}`}/>{error?"Данные не обновлены":status}</div><h2 className="mt-4 text-2xl font-semibold tracking-tight">{queue.offer_expired?"Нужно обновить предложение":queue.mode==="attention"?"Отправка остановлена":queue.mode!=="production"?"Рассылка приостановлена":future?`Старт ${campaignDate(queue.next_window)}`:queue.segment?`Сегмент ${queue.segment}${queue.segment===1?" · VIP":""}`:"Все сегменты обработаны"}</h2><p className="mt-2 max-w-xl text-sm leading-relaxed text-mute">{queue.offer_expired?"Текущие шаблоны действовали до 31 декабря 2026. Для продолжения нужны утверждённые тексты.":queue.mode==="attention"?campaignReason(queue.halt_reason||"channel_not_active"):queue.mode!=="production"?"Очередь сохранена. После продолжения отправка пойдёт в разрешённое время.":future?`В первый день до ${queue.day_budget} клиентов. Начинаем с тех, кто уже общался с нами.`:"Клиенты получают текст и фото в одном мессенджере. Следующий сегмент начнётся после завершения текущего."}</p><div className="mt-5 flex flex-wrap gap-x-5 gap-y-2 text-xs text-mute"><span className="flex items-center gap-1.5"><Clock3 size={14}/>10:00–11:00 · 17:00–20:00 МСК</span><span className="flex items-center gap-1.5"><ShieldCheck size={14}/>Без повторных отправок</span></div></div>
        <div className="border-t border-ink-600 bg-ink-850/50 p-5 sm:p-6 lg:border-l lg:border-t-0"><div className="flex items-center justify-between text-sm"><span className="text-mute">{future?"Первый день":"Сегодня"}</span><span className="text-xs text-mute">{future?campaignDate(queue.start_day+"T12:00:00+03:00",false):campaignDate(queue.server_time,false)}</span></div><div className="mt-4 flex items-baseline gap-2"><span className="text-4xl font-semibold tracking-tight tabular-nums">{future?count(queue.day_budget):count(queue.today_started)}</span><span className="text-sm text-mute">{future?"клиентов максимум":`из ${count(queue.day_budget)} максимум`}</span></div><div className="mt-4 h-1.5 overflow-hidden rounded-full bg-ink-700"><div className="h-full rounded-full bg-white transition-all" style={{width:future?"0%":`${Math.min(100,queue.today_started/Math.max(1,queue.day_budget)*100)}%`}}/></div><p className="mt-3 text-xs leading-relaxed text-mute">{future?"Постепенная отправка в течение дня, с разными интервалами.":`${count(day?.complete)} получили текст и фото · ${count(t.reply_activity_today)} ответили сегодня`}</p>{!future&&queue.next_window&&queue.mode==="production"&&<p className="mt-3 flex items-center gap-1.5 text-xs text-mute"><CalendarDays size={14}/>Окно отправки: {campaignDate(queue.next_window)}</p>}</div>
      </section>
      <section className="grid grid-cols-2 gap-3 lg:grid-cols-4" aria-label="Результаты рассылки">
        {[
          {label:"Получили рассылку",value:count(t.complete),sub:`из ${count(t.recipients)} клиентов`,icon:Send,filter:"delivered"},
          {label:"Ответили",value:count(t.replied),sub:t.reply_rate===null?"После первых отправок":`${t.reply_rate}% от получивших текст`,icon:MessageCircle,filter:"replies"},
          {label:"Заинтересовались",value:count(t.responses.interest||0),sub:"По ответам клиентов",icon:Users,filter:"interest"},
          {label:"Купили после рассылки",value:count(t.buyers),sub:`${count(t.net_kopecks/100)} ₽ с учётом возвратов`,icon:ShoppingBag,filter:"buyers"},
        ].map(metric=><button key={metric.label} onClick={()=>navigate("recipients",metric.filter)} className="group rounded-2xl border border-ink-600 p-4 text-left transition hover:bg-ink-850 sm:p-5"><div className="flex items-center justify-between gap-2 text-mute"><metric.icon size={18}/><ArrowUpRight size={15} className="opacity-0 transition group-hover:opacity-100"/></div><div className="mt-4 text-3xl font-semibold tracking-tight tabular-nums">{metric.value}</div><div className="mt-1 text-sm font-medium">{metric.label}</div><div className="mt-2 text-xs leading-relaxed text-mute">{metric.sub}</div></button>)}
      </section>
      <div className="grid items-start gap-5 lg:grid-cols-[1.5fr_1fr]">
        <section className="rounded-2xl border border-ink-600"><div className="p-5 pb-4"><div className="flex items-center justify-between gap-3"><h2 className="font-semibold">Ход рассылки</h2><span className="text-xs text-mute">Сегменты 1 → 6</span></div><div className="mt-4 h-1.5 overflow-hidden rounded-full bg-ink-700"><div className="h-full bg-white" style={{width:`${pct}%`}}/></div><p className="mt-2 text-xs text-mute">{count(t.complete)} из {count(t.recipients)} получили текст и фото</p></div><div className="px-2 pb-2">{queue.segments.map(s=><button key={s.segment} onClick={()=>navigate("recipients","",String(s.segment))} className={`flex w-full items-center gap-3 rounded-xl px-3 py-3 text-left transition hover:bg-ink-800 ${s.segment===queue.segment?"bg-ink-850":""}`}><span className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-xs ${s.segment===queue.segment?"bg-white font-semibold text-ink-950":"border border-ink-600 text-mute"}`}>{s.processed===s.total?<Check size={14}/>:s.segment}</span><span className="min-w-0 flex-1"><span className="block text-sm font-medium">{s.segment===1?"VIP-клиенты":`Сегмент ${s.segment}`}</span><span className="mt-0.5 block text-xs text-mute">{s.processed===s.total?"Обработан":s.segment===queue.segment?(future?"Первый к отправке":"Текущий сегмент"):"После предыдущего сегмента"}</span></span><span className="text-right"><span className="block text-sm tabular-nums">{count(s.sent)} <span className="text-mute">/ {count(s.total)}</span></span><span className="text-[11px] text-mute">получили текст</span></span><ChevronRight size={14} className="text-mute"/></button>)}</div></section>
        <div className="space-y-5">
          {!!t.attention&&<section className="rounded-2xl border border-ink-600 p-5"><div className="flex items-center gap-2"><AlertCircle size={18} className="text-mute"/><h2 className="font-semibold">Нужна проверка</h2><span className="ml-auto rounded-md bg-ink-800 px-2 py-0.5 text-xs">{count(t.attention)}</span></div><p className="mt-3 text-sm leading-relaxed text-mute">Этим клиентам автоматическая отправка приостановлена. Открой запись, чтобы увидеть причину и контакты в CRM.</p><button className={secondary+" mt-4 w-full"} onClick={()=>navigate("attention")}>Посмотреть записи<ArrowUpRight size={15}/></button></section>}
          <section className="rounded-2xl border border-ink-600 p-5"><h2 className="font-semibold">Ответы клиентов</h2>{!t.replied?<div className="py-5 text-sm leading-relaxed text-mute">Пока ответов нет. Здесь появятся результаты, когда клиенты ответят на рассылку.</div>:<div className="mt-3 space-y-1">{Object.entries(campaignCategories).map(([key,label])=><button key={key} onClick={()=>navigate("recipients",key)} className="flex w-full items-center justify-between rounded-lg py-2 text-sm hover:bg-ink-850"><span className="text-mute">{label}</span><span className="tabular-nums">{count(t.responses[key]||0)}</span></button>)}</div>}</section>
          {active&&<div className="px-1 text-xs leading-relaxed text-mute"><span className="font-medium text-white">История переписки · сегмент {active.segment}</span><p className="mt-1">{count(active.existing)} с подтверждённой перепиской{active.checked<active.total?` · проверено ${count(active.checked)} из ${count(active.total)}`:""}. Статистика обновляется при подготовке сегмента.</p></div>}
        </div>
      </div>
      <details className="rounded-2xl border border-ink-600"><summary className="flex cursor-pointer items-center gap-2 p-4 text-sm font-medium"><ChevronDown size={15}/>Условия отправки и учёт покупок</summary><div className="grid gap-5 border-t border-ink-600 p-5 text-sm leading-relaxed text-mute md:grid-cols-2"><div><h3 className="mb-2 font-medium text-white">Отправка</h3><p>Один клиент, один мессенджер, текст и фото. Пропущенные отправки не догоняются ускорением. При ограничении канала очередь останавливается.</p><p className="mt-2">Текущие шаблоны действуют до 31 декабря 2026. Ежедневный отчёт в Telegram в 20:15 МСК.</p><div className="mt-3 flex flex-wrap gap-3">{Object.entries(queue.channel_budgets).map(([key,budget])=><span key={key} className="text-xs">{channelNames[key as Channel]}: {count(queue.channel_usage[key]||0)} / {budget} сообщений сегодня</span>)}</div></div><div><h3 className="mb-2 font-medium text-white">Покупки</h3><p>Учитываются оплаченные проведённые документы кассы и возвраты. Продажи вне кассы требуют сверки.</p><p className="mt-2">{count(t.observed_purchases)} покупок после сообщения; {count(t.linked_purchases)} связаны с той же сделкой рассылки. Покупка после сообщения сама по себе не доказывает, что клиент купил благодаря рассылке.</p><p className="mt-2 font-medium">{queue.purchase_sync_fresh?"Сверка покупок актуальна.":"Сверка покупок задерживается. Данные могут быть неполными."}</p></div></div></details>
      {!queue.purchase_sync_fresh&&<p role="status" className="flex items-start gap-2 text-sm text-mute"><AlertCircle size={17} className="shrink-0"/>Сверка покупок задерживается. Показатели покупок могут быть неполными.</p>}
    </>}
    {queue&&view!=="overview"&&<section className="overflow-hidden rounded-2xl border border-ink-600" aria-label="Список получателей">
      <div className="space-y-4 p-5"><div className="flex flex-wrap items-baseline justify-between gap-2"><h2 className="font-semibold">{view==="attention"?"Проверка перед отправкой":"Получатели рассылки"}</h2><span className="text-xs text-mute">{loading?"Обновляем список…":`Найдено: ${count(queue.total)}`}</span></div>{view==="attention"&&<p className="text-sm leading-relaxed text-mute">Открой клиента, проверь причину и карточки CRM. Не отправляй сообщение повторно, если результат предыдущей попытки неизвестен.</p>}<div className="flex flex-wrap gap-3"><label className="relative min-w-[180px] flex-1"><Search size={16} className="absolute left-3 top-3 text-mute"/><input className={input+" pl-9"} aria-label="Поиск получателя" placeholder="Имя, телефон или номер записи" value={search} onChange={e=>setSearch(e.target.value)} maxLength={100}/></label><Select ariaLabel="Сегмент получателей" value={segment} onChange={v=>{setSegment(v);setPage(1);}} options={[{value:"0",label:"Все сегменты"},...queue.segments.map(s=>({value:String(s.segment),label:s.segment===1?"1 · VIP":`Сегмент ${s.segment}`}))]}/>{view==="recipients"&&<Select ariaLabel="Статус получателей" value={filter} onChange={v=>{setFilter(v);setPage(1);}} options={campaignFilters}/>}</div></div>
      {loading?<div className="flex h-48 items-center justify-center gap-2 text-sm text-mute" role="status"><RefreshCw size={17} className="animate-spin"/>Загружаем получателей…</div>:error?<div className="p-8 text-sm text-mute">Список не обновлён. Повтори запрос, чтобы увидеть актуальные записи.</div>:!queue.items.length?<div className="flex flex-col items-center px-6 py-14 text-center"><CheckCircle2 size={28} className="text-mute"/><h3 className="mt-4 font-medium">{query?"Ничего не найдено":view==="attention"?"Нет записей для проверки":"Здесь пока нет клиентов"}</h3><p className="mt-2 max-w-md text-sm text-mute">{query?"Попробуй другое имя, телефон или сбрось фильтры.":view==="attention"?"В выбранном сегменте нет записей, требующих внимания.":"Список заполнится после отправок и ответов клиентов."}</p><button className={secondary+" mt-5"} onClick={()=>navigate("recipients")}>Все получатели</button></div>:<div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead className="border-y border-ink-600 bg-ink-850 text-xs font-medium text-mute"><tr><th className="px-5 py-3 font-medium">Клиент</th><th className="hidden px-4 py-3 font-medium sm:table-cell">Сегмент</th><th className="px-4 py-3 font-medium">{view==="attention"?"Что проверить":"Состояние"}</th><th className="hidden px-4 py-3 font-medium lg:table-cell">Ответ</th><th className="px-5 py-3"><span className="sr-only">Открыть</span></th></tr></thead><tbody className="divide-y divide-ink-600">{queue.items.map(j=><tr key={j.id} className="hover:bg-ink-850/60"><td className="max-w-[220px] px-5 py-4"><button className="text-left font-medium hover:underline" onClick={()=>openRecipient(j)}>{j.name||`Клиент №${j.id}`}</button><div className="mt-1 text-xs text-mute">№{j.id}{j.phone_tail?` · •••• ${j.phone_tail}`:""}</div></td><td className="hidden px-4 py-4 text-mute sm:table-cell">{j.segment===1?"1 · VIP":j.segment}</td><td className="max-w-md px-4 py-4"><span className={`inline-flex items-center gap-1.5 text-xs ${j.state==="attention"?"font-medium":"text-mute"}`}>{j.state==="complete"?<CheckCircle2 size={13}/>:j.state==="attention"?<AlertCircle size={13}/>:<span className="h-1.5 w-1.5 rounded-full bg-mute"/>}{view==="attention"&&j.reason==="ambiguous_crm_contacts"?"Несколько контактов в CRM":campaignStates[j.state]||"Проверяем состояние"}</span>{view==="attention"&&<p className="mt-1 max-w-sm text-xs leading-relaxed text-mute">{j.reason==="ambiguous_crm_contacts"?"Нужно определить карточку клиента перед отправкой.":campaignReason(j.reason)}</p>}</td><td className="hidden px-4 py-4 text-xs text-mute lg:table-cell">{campaignCategories[j.classification]||(j.sent_at?"Пока нет ответа":"—")}{!!j.purchases.length&&<span className="mt-1 block text-white">Покупки: {count(j.purchases.reduce((sum,p)=>sum+p.net_kopecks,0)/100)} ₽</span>}</td><td className="px-5 py-4 text-right"><button className="inline-flex items-center gap-1 text-xs hover:underline" onClick={()=>openRecipient(j)}>Открыть<ChevronRight size={14}/></button></td></tr>)}</tbody></table></div>}
      <div className="flex items-center justify-between gap-3 border-t border-ink-600 px-5 py-4"><span className="text-xs text-mute">{queue.total?`Страница ${page} из ${Math.ceil(queue.total/queue.page_size)}`:"Нет записей"}</span><div className="flex gap-2"><button className={secondary} disabled={loading||page===1} onClick={()=>setPage(p=>p-1)}>Назад</button><button className={secondary} disabled={loading||page*queue.page_size>=queue.total} onClick={()=>setPage(p=>p+1)}>Дальше</button></div></div>
    </section>}
    {queue&&<footer className="flex flex-wrap items-center justify-between gap-2 px-1 text-xs text-mute"><span>Тестовые отправки исключены из всех показателей</span><span>{error?"Последний снимок":"Обновлено"} {new Date(queue.server_time).toLocaleTimeString("ru-RU",{timeZone:"Europe/Moscow",hour:"2-digit",minute:"2-digit"})} МСК · обновление раз в минуту</span></footer>}
    <Modal open={!!selected||!!command} onClose={close} title={selected?(selected.name||`Клиент №${selected.id}`):command==="pause"?"Приостановить рассылку?":"Продолжить рассылку?"}>
      <div className="space-y-5" role="dialog" aria-label={selected?"Карточка получателя":"Управление рассылкой"}>
        {selected?<>
          <p className="text-xs text-mute">Запись №{selected.id} · Сегмент {selected.segment}{selected.segment===1?" · VIP":""}{selected.phone_tail?` · •••• ${selected.phone_tail}`:""}</p>
          <div className="rounded-xl bg-ink-850 p-4"><p className="text-sm font-semibold">{campaignStates[selected.state]||"Проверяем состояние"}</p>{selected.reason&&<p className="mt-2 text-sm leading-relaxed text-mute">{campaignReason(selected.reason)}</p>}</div>
          <dl className="grid grid-cols-2 gap-x-4 gap-y-3 text-sm"><dt className="text-mute">Ответ клиента</dt><dd>{campaignCategories[selected.classification]||"Пока нет ответа"}</dd><dt className="text-mute">Сообщение отправлено</dt><dd>{campaignDate(selected.sent_at)}</dd><dt className="text-mute">Ответ получен</dt><dd>{campaignDate(selected.replied_at)}</dd></dl>
          <div className="flex flex-wrap gap-2">{selected.lead_id>0&&<a className={secondary} href={link(selected.lead_id)} target="_blank" rel="noreferrer">Сделка в CRM<ArrowUpRight size={14}/></a>}{selected.contact_id>0&&<a className={secondary} href={link(selected.contact_id,"contacts")} target="_blank" rel="noreferrer">Контакт в CRM<ArrowUpRight size={14}/></a>}{selected.matched_contact_ids.filter(id=>id!==selected.contact_id).map((id,i)=><a key={id} className={secondary} href={link(id,"contacts")} target="_blank" rel="noreferrer">Карточка {i+1}<ArrowUpRight size={14}/></a>)}{!selected.contact_id&&!selected.matched_contact_ids.length&&<p className="text-xs text-mute">Контакт и сделка будут созданы перед отправкой.</p>}</div>
          {!!selected.purchases.length&&<div className="space-y-2 border-t border-ink-600 pt-4"><h3 className="text-sm font-medium">Покупки</h3>{selected.purchases.map(p=><a key={p.id} className="flex justify-between gap-2 text-sm hover:underline" href={`https://online.moysklad.ru/app/#demand/edit?id=${p.document_id}`} target="_blank" rel="noreferrer"><span>{p.linked?"Связана со сделкой":"После рассылки"}</span><span>{count(p.net_kopecks/100)} ₽ ↗</span></a>)}</div>}
          {editable&&(selected.can_classify||selected.can_exclude||selected.classification!=="unsubscribe")&&<div className="space-y-3 border-t border-ink-600 pt-4"><h3 className="text-sm font-medium">Действие</h3><Select className="w-full" ariaLabel="Действие с получателем" value={recipientAction} onChange={v=>{setRecipientAction(v);setModalError("");}} options={[...(selected.can_classify?[{value:"classification",label:"Уточнить результат ответа"}]:[]),...(selected.can_exclude?[{value:"exclude",label:"Исключить из этой рассылки"}]:[]),...(selected.classification!=="unsubscribe"?[{value:"unsubscribe",label:"Клиент попросил больше не писать"}]:[])]}/>{recipientAction==="classification"?<Select className="w-full" ariaLabel="Результат ответа" value={category} onChange={setCategory} options={Object.entries(campaignCategories).map(([value,label])=>({value,label,disabled:selected.classification==="unsubscribe"&&value!=="unsubscribe"}))}/>:<p className="text-xs leading-relaxed text-mute">{recipientAction==="unsubscribe"?"Фиксируй отписку только по просьбе клиента. Дальнейшие автоматические сообщения кампании будут запрещены во всех каналах. Отменить отписку здесь нельзя.":"Клиент не получит эту рассылку. Его контакт и сделки в CRM сохранятся."}</p>}</div>}
          {editable&&!selected.can_classify&&!selected.can_exclude&&<p className="text-sm leading-relaxed text-mute">Есть попытка отправки. Сначала нужна техническая сверка результата. Повторная отправка с этой страницы недоступна.</p>}
        </>:<p className="text-sm leading-relaxed text-mute">{command==="pause"?"Новые автоматические отправки остановятся. Уже принятые мессенджером сообщения могут быть доставлены. Очередь и результаты сохранятся.":"Отправка продолжится по установленному расписанию и дневным ограничениям. Пропущенные сообщения не будут отправлены все сразу."}</p>}
        {editable&&(!!command||!!selected&&(selected.can_classify||selected.can_exclude||selected.classification!=="unsubscribe"))&&<><label className="block text-sm font-medium">Причина изменения<textarea className={input+" mt-2 min-h-[88px] resize-y font-normal"} autoFocus={!!command} value={reason} maxLength={500} onChange={e=>setReason(e.target.value)} placeholder="Коротко объясни причину для журнала"/></label>{modalError&&<p role="alert" className="text-sm text-red-500">{modalError}</p>}<div className="flex flex-wrap justify-end gap-2"><button className={secondary} onClick={close} disabled={busy}>Отмена</button><button className={primary} disabled={busy||!reason.trim()} onClick={()=>void submit()}>{busy?"Сохраняем…":command==="pause"?"Приостановить":command==="resume"?"Продолжить по плану":recipientAction==="exclude"?"Исключить клиента":recipientAction==="unsubscribe"?"Зафиксировать отписку":"Сохранить результат"}</button></div></>}
      </div>
    </Modal>
  </div>;
}
