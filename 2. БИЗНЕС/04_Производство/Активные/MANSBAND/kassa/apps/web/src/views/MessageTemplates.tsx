import { Children, isValidElement, useEffect, useRef, useState, type ChangeEvent, type ReactNode, type SelectHTMLAttributes } from "react";
import { CheckCircle2, ChevronDown, SlidersHorizontal, X, AlertCircle } from "lucide-react";
import { Select } from "../components/Select";
import { DatePicker } from "../components/DatePicker";
import { Modal, Button, StageBadge } from "../components/ui";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";

type Channel = "telegram" | "whatsapp" | "max";
type Condition = { field: number; op: string; value: string | number | boolean | null };
type Trigger = { type: "date" | "stage"; field: number | null; offset: number; unit: "days" | "weeks" | "months"; time: string; delay_minutes: number; repeat: "each_entry" | "once" };
type Rule = { name: string; enabled: boolean; pipelines: number[]; statuses: number[]; match: "all" | "any"; conditions: Condition[]; trigger: Trigger; recipient: "contact" | "field"; phone_field: number | null; content: string; variants: Record<string, string>; store_field: number; window_start: number; window_end: number; late_policy: "skip" | "same_day" | "legacy_stage"; legacy_next_day: boolean };
type Routing = { order: Channel[]; previous_first: boolean; social_fallback: boolean };
type Manual = { remote_id?: number; name: string; content: string; source_hash?: string; original?: unknown; delete?: boolean };
type Payload = Rule | Routing | Manual;
type Doc = { id: string; kind: "rule" | "routing" | "manual"; version: number; draft: Payload; active: Payload | null; checked: boolean };
type Field = { id: number; name: string; type: string; enums?: { id: number; value: string }[] };
type Pipeline = { id: number; name: string; sort?: number; _embedded?: { statuses?: { id: number; name: string; sort?: number }[] } };
type Remote = { id: number; name: string; content: string; source_hash: string; type: string };
type Check = { sample_limit?: number; total?: number; samples?: { lead_id: number; text?: string; due_at?: string; channels?: string[]; reason?: string }[]; overlaps?: { id: string; name: string }[]; text?: string };
type Journal = { id: number; rule_id: string; lead_id: number; due_at: string; label: string; reason: string; attempts: { channel: string; state: string; reason: string }[] };
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
  const fields = catalog.fields;
  const rule = selected?.kind === "rule" ? selected.draft as Rule : null;
  const routing = selected?.kind === "routing" ? selected.draft as Routing : null;
  const manual = selected?.kind === "manual" ? selected.draft as Manual : null;
  const manualReadOnly = Boolean(manual?.remote_id && remote.find(x => x.id === manual.remote_id)?.type !== "amocrm");
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

  return <div className="space-y-5 text-white">
    <div className="flex flex-wrap items-center justify-between gap-3"><h1 className="text-2xl font-extrabold">Шаблоны сообщений</h1><button className={button} disabled={busy} onClick={() => void action(async () => { await refresh(); setJournal((await api.get<{ items: Journal[] }>(root + "/journal")).items); setRemote((await api.get<{ items: Remote[] }>(root + "/manual")).items); })}>Обновить</button></div>
    <div className="flex flex-wrap gap-2">{([ ["rule", "Автоматические"], ["manual", "Ручные"], ["routing", "Порядок отправки"] ] as const).map(([key, label]) => <button key={key} className={`${button} ${tab === key ? "bg-gold/20 text-gold-soft" : ""}`} onClick={() => switchTab(key)}>{label}</button>)}</div>
    {!editable && <p className="text-mute">Просмотр. Изменять настройки может только администратор.</p>}
    {tab !== "manual" && executor !== "live" && <div className="rounded-xl border border-amber-600 p-3 text-amber-200">Сохранённые версии пока не подключены к отправке. Можно подготовить и проверить изменения; для применения требуется подключить управление рассылкой.</div>}
    {(error || notice) && <div role={error ? "alert" : "status"} className={`card flex items-start gap-3 border p-4 shadow-lg ${error ? "border-red-400/40" : "border-gold/30"}`}>
      {error ? <AlertCircle size={20} className="mt-0.5 shrink-0 text-red-300" /> : <CheckCircle2 size={20} className="mt-0.5 shrink-0 text-gold" />}<p className="min-w-0 flex-1 text-sm">{error || notice}</p><button aria-label="Закрыть уведомление" onClick={() => { setError(""); setNotice(""); }} className="rounded-lg p-1 text-mute hover:bg-ink-700"><X size={16} /></button>
    </div>}
    <Modal open={Boolean(dialog)} onClose={() => closeDialog(false)} title="Подтверждение"><p className="mb-6 text-sm leading-relaxed text-mute-soft">{dialog?.message}</p><div className="flex justify-end gap-2"><Button variant="outline" onClick={() => closeDialog(false)}>Отмена</Button><Button onClick={() => closeDialog(true)}>{dialog?.confirm}</Button></div></Modal>
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
        {tab === "manual" && remote.filter(x => `${x.name} ${x.content}`.toLowerCase().includes(search.trim().toLowerCase())).sort((a, b) => a.name.localeCompare(b.name, "ru")).map(x => <button key={x.id} className={`${button} block w-full text-left`} onClick={() => { const d = docs.find(d => d.kind === "manual" && (d.draft as Manual).remote_id === x.id); open(d && JSON.stringify(d.active) !== JSON.stringify(d.draft) ? d : { id: d?.id ?? `manual-${x.id}`, kind: "manual", version: d?.version ?? 0, draft: { remote_id: x.id, name: x.name, content: x.content, source_hash: x.source_hash }, active: null, checked: false }); }}>{x.name}{x.type !== "amocrm" && <span className="block text-xs text-mute">Особый тип: только просмотр</span>}</button>)}
        {tab === "manual" && docs.filter(d => d.kind === "manual" && !(d.draft as Manual).remote_id && !d.active && (d.draft as Manual).name.toLowerCase().includes(search.trim().toLowerCase())).map(d => <button key={d.id} className={`${button} block w-full text-left`} onClick={() => open(d)}>{(d.draft as Manual).name} · черновик</button>)}
        {tab === "routing" && <p className="text-sm text-mute">Один порядок для всех автоматических сообщений. Изменения вступают в силу после проверки и публикации.</p>}
      </aside>
      <section className="min-w-0 space-y-4">
        {!selected && <p className="text-mute">Выбери шаблон или создай новый.</p>}
        {selected && <>
          <fieldset disabled={!editable || busy || manualReadOnly} className="min-w-0 space-y-4 disabled:opacity-80">
            {(rule || manual) && <label className="block space-y-1"><span>Название</span><input className={input} value={(rule || manual)?.name ?? ""} onChange={e => edit({ name: e.target.value })} /></label>}
            {rule && <>
              <div className="rounded-xl bg-ink-800 p-3 text-sm"><p className="mb-2 text-xs text-mute">Используется на этапах</p><div className="flex flex-wrap gap-2">{rule.statuses.length ? ruleStages(rule).map(s => <span key={`${s.pipeline.id}-${s.id}`} title={s.pipeline.name}><StageBadge stage={s.name} /></span>) : <span>{stageLabel(rule)}</span>}</div></div>
              <label className="flex gap-2"><input type="checkbox" checked={rule.enabled} onChange={e => edit({ enabled: e.target.checked })} />Включить после публикации</label>
              <div className="grid gap-3 sm:grid-cols-2"><label>Воронки<select multiple className={`${input} h-32`} value={rule.pipelines.map(String)} onChange={e => edit({ pipelines: Array.from(e.target.selectedOptions, x => Number(x.value)), statuses: [] })}>{catalog.pipelines.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label><label>Этапы<select multiple className={`${input} h-32`} value={rule.statuses.map(String)} onChange={e => edit({ statuses: Array.from(e.target.selectedOptions, x => Number(x.value)) })}>{stages.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</select><span className="text-xs text-mute">Для даты: пустой выбор означает все открытые этапы.</span></label></div>
              <div className="space-y-3 rounded-xl border border-ink-600 p-3"><h2 className="font-semibold">Условия</h2><TemplateSelect className={input} value={rule.match} onChange={e => edit({ match: e.target.value as "all" | "any" })}><option value="all">Все выполнены</option><option value="any">Хотя бы одно выполнено</option></TemplateSelect>
                {rule.conditions.map((c, i) => { const f = fields.find(f => f.id === c.field); const update = (patch: Partial<Condition>) => edit({ conditions: rule.conditions.map((v, n) => n === i ? { ...v, ...patch } : v) }); return <div key={i} className="grid gap-2 sm:grid-cols-[1fr_1fr_1fr_auto]">{fieldSelect(c.field, n => update({ field: n ?? 0, value: null, op: "eq" }))}<TemplateSelect className={input} value={c.op} onChange={e => update({ op: e.target.value })}>{[["eq", "Равно"], ["ne", "Не равно"], ["empty", "Пусто"], ["filled", "Заполнено"], ...(f?.enums?.length ? [] : ["numeric", "price", "date", "date_time"].includes(f?.type ?? "") ? [["gt", "Больше"], ["gte", "Не меньше"], ["lt", "Меньше"], ["lte", "Не больше"]] : [["contains", "Содержит"]])].map(([v, label]) => <option key={v} value={v}>{label}</option>)}</TemplateSelect>{!["empty", "filled"].includes(c.op) && (f?.enums?.length ? <TemplateSelect className={input} value={String(c.value ?? "")} onChange={e => update({ value: e.target.value ? Number(e.target.value) : null })}><option value="">Значение</option>{f.enums.map(v => <option key={v.id} value={v.id}>{v.value}</option>)}</TemplateSelect>  : f?.type === "checkbox" ? <TemplateSelect className={input} value={String(c.value ?? "")} onChange={e => update({ value: e.target.value === "" ? null : e.target.value === "true" })}><option value="">Выбери значение</option><option value="true">Да</option><option value="false">Нет</option></TemplateSelect> : ["date", "date_time"].includes(f?.type ?? "") ? <DatePicker className={input} value={c.value ? new Date(Number(c.value) * 1000 + 3 * 3600000).toISOString().slice(0, 10) : ""} onChange={value => update({ value: value ? Date.parse(value + "T00:00:00+03:00") / 1000 : null })} /> : <input type={["numeric", "price"].includes(f?.type ?? "") ? "number" : "text"} className={input} value={String(c.value ?? "")} aria-label="Значение условия" onChange={e => update({ value: ["numeric", "price", "date", "date_time"].includes(f?.type ?? "") ? e.target.value === "" ? null : Number(e.target.value) : f?.type === "checkbox" ? e.target.value === "true" : e.target.value })} />)}<button className={button} onClick={() => edit({ conditions: rule.conditions.filter((_, n) => n !== i) })}>Убрать</button></div>; })}
                <button className={button} onClick={() => edit({ conditions: [...rule.conditions, { field: fields[0]?.id ?? 0, op: "eq", value: null }] })}>Добавить условие</button></div>
              <div className="space-y-3 rounded-xl border border-ink-600 p-3"><h2 className="font-semibold">Когда отправлять</h2><TemplateSelect className={input} value={rule.trigger.type} onChange={e => edit({ trigger: { ...rule.trigger, type: e.target.value as "date" | "stage" } })}><option value="date">Относительно поля даты</option><option value="stage">После перехода на этап</option></TemplateSelect>
                {rule.trigger.type === "date" ? <><label className="block">Поле даты{fieldSelect(rule.trigger.field, n => edit({ trigger: { ...rule.trigger, field: n } }), fields.filter(f => ["date", "date_time"].includes(f.type)))}</label><div className="grid gap-2 sm:grid-cols-3"><label>Смещение<input type="number" className={input} value={rule.trigger.offset} onChange={e => edit({ trigger: { ...rule.trigger, offset: Number(e.target.value) } })} /></label><label>Единица<TemplateSelect className={input} value={rule.trigger.unit} onChange={e => edit({ trigger: { ...rule.trigger, unit: e.target.value as Trigger["unit"] } })}><option value="days">Дней</option><option value="weeks">Недель</option><option value="months">Календарных месяцев</option></TemplateSelect></label><label>Время МСК<input type="time" className={input} value={rule.trigger.time} onChange={e => edit({ trigger: { ...rule.trigger, time: e.target.value } })} /></label></div><p className="text-sm text-mute">0 — день в день, отрицательное число — до даты, положительное — после. Пропущенное время не догоняется.</p></> : <><label className="block">Задержка, минут<input type="number" min={0} className={input} value={rule.trigger.delay_minutes} onChange={e => edit({ trigger: { ...rule.trigger, delay_minutes: Number(e.target.value) } })} /></label><TemplateSelect className={input} value={rule.trigger.repeat} onChange={e => edit({ trigger: { ...rule.trigger, repeat: e.target.value as Trigger["repeat"] } })}><option value="each_entry">Каждый новый вход</option><option value="once">Один раз на сделку</option></TemplateSelect></>}
                <div className="grid grid-cols-2 gap-2"><label>Окно с, МСК<input className={input} type="number" min={0} max={23} value={rule.window_start} onChange={e => edit({ window_start: Number(e.target.value) })} /></label><label>Окно до, МСК<input className={input} type="number" min={1} max={24} value={rule.window_end} onChange={e => edit({ window_end: Number(e.target.value) })} /></label></div>
                {rule.legacy_next_day && <p className="text-xs text-amber-200">Сохранён прежний перенос события вне окна на следующий день.</p>}</div>
              <label className="block">Получатель<TemplateSelect className={input} value={rule.recipient} onChange={e => edit({ recipient: e.target.value as Rule["recipient"] })}><option value="contact">Основной контакт сделки</option><option value="field">Телефон из поля сделки</option></TemplateSelect></label>
              {rule.recipient === "field" && <label className="block">Поле телефона{fieldSelect(rule.phone_field, n => edit({ phone_field: n }))}<span className="text-xs text-mute">Контакт покупателя не используется как запасной получатель.</span></label>}
            </>}
            {(rule || manual) && <label className="block">Текст сообщения<textarea className={`${input} min-h-40`} value={(rule || manual)?.content ?? ""} onChange={e => edit({ content: e.target.value })} /></label>}
            {rule && <><label className="block">Вставить переменную<TemplateSelect className={input} value="" onChange={e => { if (e.target.value) edit({ content: rule.content + e.target.value }); }}><option value="">Выбери переменную</option><option value="{{lead.id}}">ID сделки</option><option value="{{lead.price}}">Бюджет сделки</option>{fields.map(f => <option key={f.id} value={`{{lead.${["date", "date_time"].includes(f.type) ? "date" : "field"}.${f.id}}}`}>{f.name}</option>)}</TemplateSelect></label>
              <div className="space-y-3"><h2 className="font-semibold">Варианты по магазину</h2>{Object.entries(rule.variants).map(([key, value]) => <label className="block" key={key}>{key === "default" ? "Магазин не определён" : fields.find(f => f.id === rule.store_field)?.enums?.find(e => String(e.id) === key)?.value ?? key}<textarea className={`${input} min-h-32`} value={value} onChange={e => edit({ variants: { ...rule.variants, [key]: e.target.value } })} /><button className={button} onClick={() => edit({ variants: Object.fromEntries(Object.entries(rule.variants).filter(([k]) => k !== key)) })}>Убрать вариант</button></label>)}<TemplateSelect className={input} value="" onChange={e => { if (e.target.value) edit({ variants: { ...rule.variants, [e.target.value]: rule.content } }); }}><option value="">Добавить вариант</option>{(fields.find(f => f.id === rule.store_field)?.enums ?? []).filter(e => !rule.variants[String(e.id)]).map(e => <option key={e.id} value={e.id}>{e.value}</option>)}{!("default" in rule.variants) && <option value="default">Магазин не определён</option>}</TemplateSelect></div>
              <p className="rounded-xl bg-ink-800 p-3 text-sm">Каналы: {activeRouting?.previous_first ? "прежний Telegram, WhatsApp или MAX → " : ""}{(activeRouting?.order ?? ["telegram", "whatsapp", "max"]).map(c => channelNames[c]).join(" → ")}{activeRouting?.social_fallback ? " → исходный Instagram или Авито" : ""}. <button className="underline" onClick={() => switchTab("routing")}>Изменить общий порядок</button></p>
            </>}
            {manual?.remote_id && <label className="flex gap-2 text-red-300"><input type="checkbox" checked={Boolean(manual.delete)} onChange={e => edit({ delete: e.target.checked })} />Подготовить удаление из amoCRM</label>}
            {routing && <><label className="flex gap-2"><input type="checkbox" checked={routing.previous_first} onChange={e => edit({ previous_first: e.target.checked })} />Начинать с прежнего Telegram, WhatsApp или MAX</label>{routing.order.map((channel, index) => <div key={channel} className="flex items-center gap-3 rounded-xl border border-ink-600 p-3"><span className="flex-1">{index + 1}. {channelNames[channel]}</span>{[-1, 1].map(delta => <button key={delta} className={button} disabled={index + delta < 0 || index + delta >= routing.order.length} onClick={() => { const order = [...routing.order]; [order[index], order[index + delta]] = [order[index + delta], order[index]]; edit({ order }); }}>{delta === -1 ? "Выше" : "Ниже"}</button>)}</div>)}<label className="flex gap-2"><input type="checkbox" checked={routing.social_fallback} onChange={e => edit({ social_fallback: e.target.checked })} />Использовать исходный Instagram или Авито после мессенджеров</label><p className="text-sm text-mute">Следующий канал — только после подтверждённого отказа. Таймаут и неизвестный результат останавливают цепочку.</p></>}
          </fieldset>
          {rule && <label className="block">ID сделки для предпросмотра<input className={input} type="number" min={1} value={leadId} onChange={e => { setLeadId(e.target.value); setChecked(null); }} placeholder="Пусто — проверить выборку" /></label>}
          <div className="flex flex-wrap gap-2">{editable && !manualReadOnly && <><button className={button} disabled={busy || !dirty && selected.version > 0} onClick={save}>Сохранить черновик</button><button className={button} disabled={busy || dirty || !selected.version} onClick={check}>Проверить без отправки</button><button className={`${button} bg-gold/20`} disabled={busy || dirty || !checked || Boolean(checked.overlaps?.length && !confirmOverlap)} onClick={publish}>Опубликовать</button></>}<button className={button} disabled={busy || !selected.version} onClick={() => void action(async () => setHistory((await api.get<{ items: Revision[] }>(root + "/history/" + selected.id)).items))}>История версий</button></div>
          {editable && rule && checked && <div className="space-y-2 rounded-xl border border-ink-600 p-3"><h2 className="font-semibold">Тестовая отправка</h2><p className="text-sm text-mute">Отдельное сообщение с пометкой ТЕСТ. Укажи ID сделки для заполнения переменных и свой тестовый адресат.</p><TemplateSelect className={input} value={testChannel} onChange={e => { setTestChannel(e.target.value as Channel); setTestOperation(crypto.randomUUID()); }}>{Object.entries(channelNames).map(([v, label]) => <option value={v} key={v}>{label}</option>)}</TemplateSelect><input className={input} aria-label="Тестовый получатель" placeholder="Телефон или Telegram username" value={testDestination} onChange={e => { setTestDestination(e.target.value); setTestOperation(crypto.randomUUID()); }} /><button className={button} disabled={busy || dirty || !leadId || !testDestination} onClick={() => void action(async () => { if (!await ask(`Отправить ТЕСТ через ${channelNames[testChannel]} получателю ${testDestination}?`, "\u041e\u0442\u043f\u0440\u0430\u0432\u0438\u0442\u044c \u0442\u0435\u0441\u0442")) return; const result = await api.post<{ job_id: number; state: string }>(root + "/test/" + selected.id, { version: selected.version, lead_id: Number(leadId), transport: testChannel, destination: testDestination, operation_id: testOperation, confirmed: true }); setNotice(`Тестовое задание ${result.job_id}: ${result.state}. Повтор не запускается автоматически.`); setJournal((await api.get<{ items: Journal[] }>(root + "/journal")).items); })}>Отправить тест</button></div>}
          {checked && <div className="space-y-3 rounded-xl border border-ink-600 p-3"><h2 className="font-semibold">Проверка без отправки</h2>{checked.total !== undefined && <p>Подходящих сделок: {checked.total}. Ниже показано до {checked.sample_limit ?? 10} примеров.</p>}{checked.text && <p className="whitespace-pre-wrap">{checked.text}</p>}{Boolean(checked.overlaps?.length) && <label className="block text-amber-200"><input type="checkbox" disabled={!editable} checked={confirmOverlap} onChange={e => setConfirmOverlap(e.target.checked)} /> Подтверждаю намеренное пересечение: {checked.overlaps?.map(x => x.name).join(", ")}</label>}{checked.samples?.map(s => <div className="border-t border-ink-600 pt-2" key={s.lead_id}><p>Сделка {s.lead_id} · {s.due_at ? new Date(s.due_at).toLocaleString("ru-RU", { timeZone: "Europe/Moscow" }) + " МСК" : "новый переход"}</p>{s.reason && <p className="text-amber-200">{s.reason}</p>}<p className="text-xs text-mute">{s.channels?.join(" → ")}</p><p className="whitespace-pre-wrap">{s.text}</p></div>)}</div>}
          {history.length > 0 && <div className="space-y-2"><h2 className="font-semibold">История</h2>{history.map((h, i) => <div key={i} className="rounded-xl border border-ink-600 p-3"><p>Версия {h.version} · {h.actor} · {h.action} · {new Date(h.at).toLocaleString("ru-RU", { timeZone: "Europe/Moscow" })}</p>{editable && <button className={button} onClick={() => { const payload = "document" in h.payload ? h.payload.document : h.payload; if (selected.kind === "manual") { setError("Для ручного шаблона открой актуальную версию amoCRM и перенеси прежний текст, чтобы сохранить проверку конфликтов."); return; } edit(payload as Payload); }}>Восстановить в черновик</button>}</div>)}</div>}
        </>}
      </section>
    </div>
    {tab === "rule" && <section className="overflow-hidden rounded-xl border border-ink-600">
      <button className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-ink-800" aria-expanded={journalOpen} aria-controls="template-journal" onClick={() => setJournalOpen(v => !v)}><ChevronDown size={18} className={`transition-transform ${journalOpen ? "rotate-180" : ""}`} /><span className="flex-1 text-sm font-semibold">Журнал отправок</span><span className="text-xs text-mute">{journal.length} записей</span></button>
      {journalOpen && <div id="template-journal" className="max-h-80 overflow-y-auto border-t border-ink-600 px-4">{!journal.length && <p className="py-3 text-sm text-mute">Пока нет заданий.</p>}{journal.map(j => <details key={j.id} className="group border-b border-ink-600 last:border-0"><summary className="flex cursor-pointer list-none flex-wrap items-center gap-x-3 gap-y-1 py-2 text-xs"><ChevronDown size={14} className="shrink-0 transition-transform group-open:rotate-180" /><time className="text-mute">{new Date(j.due_at).toLocaleString("ru-RU", { timeZone: "Europe/Moscow", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}</time><span className="min-w-0 flex-1">{(docs.find(d => d.id === j.rule_id)?.draft as Rule | undefined)?.name ?? j.rule_id}</span><span className="text-mute">Сделка {j.lead_id}</span><span className="rounded-md bg-ink-700 px-2 py-1">{j.label}</span></summary><div className="space-y-1 pb-3 pl-6 text-xs text-mute">{j.reason && <p>{j.reason}</p>}{!j.attempts.length && <p>Попыток отправки пока нет.</p>}{j.attempts.map((a, i) => <p key={i}>{channelNames[a.channel as Channel] ?? a.channel}: {a.state} {a.reason}</p>)}</div></details>)}</div>}
    </section>}
  </div>;
}
