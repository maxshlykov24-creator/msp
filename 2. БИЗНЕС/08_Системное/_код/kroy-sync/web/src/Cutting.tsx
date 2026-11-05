import { Choice, Select, Icon, ActionMenu, confirmAction } from './UI';
import React, { useEffect, useState, useRef } from 'react';
import './Cutting.css';
import {compareSizes,compareVariants,productCategory,photoCandidates} from './catalogData';
import {ItemPhoto as Photo} from './ItemPhoto';
import {ProductFilters,StockSummary,useProductFilters} from './ProductFilters';
import {modelStock} from './catalogData';
type Row = Record<string, any>;
type Api = (path: string, body?: any) => Promise<any>;
const states: Record<string, string> = { draft: 'Черновик', approved: 'Выдано', partial: 'Частично выполнено', complete: 'Завершено', cancelled: 'Отменено' };
const n = (v: any) => v == null ? '—' : Number(v).toLocaleString('ru-RU');
const today = () => new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/Moscow', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
const createdDay = (r: Row) => r.created_at ? new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/Moscow', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(r.created_at)) : '';
const shift = (days: number) => { const d = new Date(today() + 'T12:00:00Z'); d.setUTCDate(d.getUTCDate() + days); return d.toISOString().slice(0, 10); };
const defaults = () => ({ start: shift(-14), end: shift(-1), horizon: 28, target_date: shift(28), include_uncertain: [] as string[] });
const errorText = (e: unknown) => e instanceof Error ? e.message : String(e);
const unitLabel = (i?: Row) => i?.unit === 'roll' ? 'рул.' : i?.unit === 'meter' ? 'м' : 'шт.';
const dateText = (value?: string) => value ? new Date(value + 'T12:00:00Z').toLocaleDateString('ru-RU') : 'Не указан';
function CutSection({ title, description, action, children, className = '' }: {
    title: string;
    description?: string;
    action?: React.ReactNode;
    children: React.ReactNode;
    className?: string;
}) {
    return <section className={'panel cut-panel ' + className}><div className="cut-section-heading"><div><h3>{title}</h3>{description && <p>{description}</p>}</div>{action && <div className="cut-section-action">{action}</div>}</div><div className="cut-section-body">{children}</div></section>;
}
function ProductCell({ item }: {
    item: Row;
}) { return <span className="cut-product-cell"><Photo item={item}/><span><strong>{item.model || item.name}</strong><span className="cut-product-caption">{item.color || (!item.model ? item.name : 'Цвет не указан')}{item.size && <span className="cut-size">{item.size}</span>}</span></span></span>; }
function CutNotice({ children, error = false }: {
    children: React.ReactNode;
    error?: boolean;
}) { return <div className={'cut-notice ' + (error ? 'is-error' : '')} role={error ? 'alert' : undefined}><Icon name="attention" size={18}/><div>{children}</div></div>; }
export function Cutting({ api, items, rows, locations, permissions, refresh }: {
    api: Api;
    items: Row[];
    rows: Row[];
    locations: string[];
    permissions: string[];
    refresh: () => Promise<any>;
}) {
    const [users, setUsers] = useState<Row[]>([]), [opened, setOpened] = useState<Row | null>(null), [editing, setEditing] = useState(false), [query, setQuery] = useState(''), [status, setStatus] = useState('active'), [person, setPerson] = useState(''), [model, setModel] = useState(''), [from, setFrom] = useState(''), [to, setTo] = useState(''), [filtersOpen, setFiltersOpen] = useState(false);
    useEffect(() => { api('cutting/assignees').then(setUsers).catch(() => { }); }, []);
    const plan = permissions.includes('cut_plan'), cut = permissions.includes('cut'), adjust = permissions.includes('adjust');
    const models = [...new Set(items.filter(i => i.kind === 'garment').map(i => i.model))];
    const get = (id: string) => items.find(i => i.id === id) || { name: 'Неизвестная позиция' };
    const filtered = rows.filter(r => (status === 'all' || status === 'active' && ['draft', 'approved', 'partial'].includes(r.status) || r.status === status) && (!person || r.assignee === person) && (!model || r.products.some((l: Row) => get(l.item_id).model === model)) && (!from || createdDay(r) >= from) && (!to || createdDay(r) <= to) && (!query || [r.title, r.number, r.legacy_number, r.id].join(' ').toLowerCase().includes(query.toLowerCase()))).sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''));
    if (editing)
        return <CutEditor api={api} items={items} locations={locations} users={users} existing={opened} close={() => setEditing(false)} saved={async () => { await refresh(); setEditing(false); setOpened(null); }}/>;
    if (opened)
        return <CutDocument api={api} task={rows.find(r => r.id === opened.id) || opened} items={items} users={users} canPlan={plan} canCut={cut} canAdjust={adjust} refresh={refresh} close={() => setOpened(null)} edit={() => setEditing(true)}/>;
    const filterCount = Number(!!person) + Number(!!model) + Number(!!from || !!to);
    const reset = () => { setStatus('active'); setPerson(''); setModel(''); setFrom(''); setTo(''); setQuery(''); };
    const dateLabel = (v: string) => v ? new Date(v + 'T12:00:00Z').toLocaleDateString('ru-RU') : 'Не указан';
    const progress = (r: Row) => { const total = r.products.reduce((sum: number, l: Row) => sum + Number(l.quantity), 0), left = r.remaining_products.reduce((sum: number, l: Row) => sum + Number(l.quantity), 0); return { total, done: total - left }; };
    const overdue = (r: Row) => r.due_on && r.due_on < today() && ['approved', 'partial'].includes(r.status);
    const chips = [...(status !== 'active' ? [{ text: status === 'all' ? 'Все статусы' : states[status], clear: () => setStatus('active') }] : []), ...(person ? [{ text: users.find(u => u.id === person)?.name || 'Исполнитель', clear: () => setPerson('') }] : []), ...(model ? [{ text: model, clear: () => setModel('') }] : []), ...(from || to ? [{ text: (from ? dateLabel(from) : 'Начало') + ' — ' + (to ? dateLabel(to) : 'Сегодня'), clear: () => { setFrom(''); setTo(''); } }] : [])];
    return <div className="cutting">
  <div className="cut-toolbar cut-heading"><div><h2>Задания на крой</h2><p className="muted">Планируй выпуск и следи за выполнением</p></div>{plan && <button onClick={() => { setOpened(null); setEditing(true); }}>+ Задание</button>}</div>
  <div className="cut-search-row"><div className="search-field"><Icon name="search" size={18}/><input aria-label="Найти задание" placeholder="Номер или название задания" value={query} onChange={e => setQuery(e.target.value)}/>{query && <button aria-label="Очистить поиск заданий" onClick={() => setQuery('')}><Icon name="close" size={15}/></button>}</div><div className="cut-status-filter"><Select label="Статус задания" value={status} onChange={setStatus} options={[{ value: 'active', label: 'Активные задания' }, { value: 'all', label: 'Все статусы' }, ...Object.entries(states).map(([value, label]) => ({ value, label }))]}/></div><button className={'secondary filter-toggle ' + (filtersOpen || filterCount ? 'active' : '')} aria-expanded={filtersOpen} aria-controls="cut-filters" onClick={() => setFiltersOpen(!filtersOpen)}><Icon name="filter" size={17}/><span>Фильтры</span>{filterCount > 0 && <span className="filter-count">{filterCount}</span>}<Icon name="down" size={15}/></button></div>
  {filtersOpen && <div id="cut-filters" className="cut-filter-panel"><div className="cut-filter-title"><strong>Фильтры заданий</strong><button className="text-button" aria-label="Скрыть фильтры" onClick={() => setFiltersOpen(false)}><Icon name="close" size={16}/></button></div><div className="cut-filter-grid"><label>Исполнитель<Select label="Исполнитель" value={person} onChange={setPerson} options={[{ value: '', label: 'Все исполнители' }, ...users.map(u => ({ value: u.id, label: u.name }))]}/></label><label>Модель<Select label="Модель в заданиях" value={model} onChange={setModel} options={[{ value: '', label: 'Все модели' }, ...models.map(m => ({ value: m, label: m }))]}/></label><label>Создано с<input aria-label="Создано с" type="date" max={to || undefined} value={from} onChange={e => setFrom(e.target.value)}/></label><label>По дату<input aria-label="По дату" type="date" min={from || undefined} value={to} onChange={e => setTo(e.target.value)}/></label></div></div>}
  <div className="cut-list-meta"><span>Показано {filtered.length} из {rows.length}</span>{(chips.length > 0 || query) && <button className="text-button" onClick={reset}>Сбросить фильтры</button>}</div>
  {chips.length > 0 && <div className="filter-chips">{chips.map(c => <button key={c.text} onClick={c.clear} aria-label={'Убрать фильтр: ' + c.text}>{c.text}<Icon name="close" size={13}/></button>)}</div>}
  {filtered.length > 0 ? <><div className="panel table-wrap cut-register"><table><thead><tr><th>Задание</th><th>Статус</th><th>Срок кроя</th><th>Исполнитель</th><th>Выполнено</th><th aria-label="Открыть"/></tr></thead><tbody>{filtered.map(r => { const { total, done } = progress(r); return <tr className="product-row" key={r.id} tabIndex={0} aria-label={'Открыть задание: ' + r.title} onClick={() => setOpened(r)} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            setOpened(r);
        } }}><td className="cut-title-cell"><strong>{r.title}</strong><small>{r.number || r.id.slice(0, 8)}<span className="meta-dot">·</span>{r.created_at ? new Date(r.created_at).toLocaleDateString('ru-RU', { timeZone: 'Europe/Moscow' }) : 'Дата не указана'}</small></td><td><span className={'badge cut-status status-' + r.status}>{states[r.status]}</span></td><td>{dateLabel(r.due_on)}{overdue(r) && <small className="cut-warning">Просрочено</small>}</td><td>{users.find(u => u.id === r.assignee)?.name || 'Не назначен'}</td><td><div className="cut-progress"><span className="cut-progress-count"><strong>{n(done)}</strong><span>из {n(total)}</span></span><progress aria-label="Выполнение задания" max={total || 1} value={done}/></div></td><td><Icon name="chevron" size={16}/></td></tr>; })}</tbody></table></div>
  <div className="cut-mobile-list">{filtered.map(r => { const { total, done } = progress(r); return <button className="cut-task-card" key={r.id} onClick={() => setOpened(r)}><span className="cut-card-top"><small>{r.number || r.id.slice(0, 8)}</small><span className={'badge cut-status status-' + r.status}>{states[r.status]}</span></span><strong>{r.title}</strong><span className="cut-card-meta"><span>Срок: {dateLabel(r.due_on)}</span>{overdue(r) && <span className="cut-warning">Просрочено</span>}</span><span className="cut-card-meta">{users.find(u => u.id === r.assignee)?.name || 'Исполнитель не назначен'}</span><span className="cut-card-bottom"><span>{n(done)} из {n(total)} выполнено</span><Icon name="chevron" size={16}/></span><progress aria-label="Выполнение задания" max={total || 1} value={done}/></button>; })}</div></> : <div className="cut-empty"><span className="cut-empty-icon"><Icon name="tasks" size={26}/></span><h3>{rows.length ? 'Задания не найдены' : 'Здесь будут задания на крой'}</h3><p>{rows.length ? 'Попробуй изменить условия или очистить поиск.' : 'Создай задание: выбери товары, проверь потребность и подготовь раскладки.'}</p>{rows.length ? <button className="secondary" onClick={reset}>Сбросить фильтры</button> : plan && <button className="secondary" onClick={() => { setOpened(null); setEditing(true); }}>Создать первое задание</button>}</div>}
 </div>;
}
function CutEditor({ api, items, locations, users, existing, close, saved }: {
    api: Api;
    items: Row[];
    locations: string[];
    users: Row[];
    existing: Row | null;
    close: () => void;
    saved: () => Promise<void>;
}) {
    const old = existing?.cutting || {};
    const [syncMessage, setSyncMessage] = useState('');
    const [draftId] = useState(() => crypto.randomUUID()), [salesStatus, setSalesStatus] = useState<Row>({});
    useEffect(() => { let active=true;const refresh=()=>api('cutting/sales-status').then(data=>{if(active)setSalesStatus(data)}).catch(()=>{});refresh();const timer=setInterval(refresh,60000);return()=>{active=false;clearInterval(timer)}; }, []);
    const [step, setStep] = useState(0), [title, setTitle] = useState(existing?.title || ''), [location, setLocation] = useState(existing?.location || locations[0] || ''), [selected, setSelected] = useState<string[]>(old.selected || existing?.products.map((l: Row) => l.item_id) || []), [qty, setQty] = useState<Record<string, string>>(old.quantities || Object.fromEntries((existing?.products || []).map((l: Row) => [l.item_id, String(l.quantity)]))), [params, setParams] = useState<Row>(old.parameters || defaults()), [calc, setCalc] = useState<Row | null>(old.calculation || null), [previous, setPrevious] = useState<Row | null>(null), [reason, setReason] = useState(old.manual_reason || ''), [due, setDue] = useState(old.due_on || existing?.due_on || ''), [available, setAvailable] = useState(old.available_on || existing?.available_on || ''), [assignee, setAssignee] = useState(old.assignee || existing?.assignee || ''), [groups, setGroups] = useState<Row[]>(old.groups || []), [confirmed, setConfirmed] = useState(false), [search, setSearch] = useState(''), [error, setError] = useState(''), [busy, setBusy] = useState(false), [norms, setNorms] = useState<Record<string, number>>({}), [pending, setPending] = useState<Row | null>(null), [dirty, setDirty] = useState(false);
    useEffect(() => { window.scrollTo({ top: 0, behavior: 'instant' }); }, [step]);
    const garments = items.filter(i => i.kind === 'garment').sort(compareVariants), materials = items.filter(i => i.kind === 'material');
    useEffect(() => { api('norms').then(r => setNorms(r.values)).catch(() => { }); }, []);
    useEffect(() => { const f = (e: BeforeUnloadEvent) => { if (dirty) {
        e.preventDefault();
        e.returnValue = '';
    } }; window.addEventListener('beforeunload', f); return () => window.removeEventListener('beforeunload', f); }, [dirty]);
    useEffect(() => { const guard = (e: Event) => { if(dirty){e.preventDefault();confirmAction('Выйти из задания?','Есть несохранённые изменения.','Выйти без сохранения').then(ok=>{if(ok){setDirty(false);(e as CustomEvent).detail?.retry?.()}})} }; window.addEventListener('yuji:before-navigation', guard); return () => window.removeEventListener('yuji:before-navigation', guard); }, [dirty]);
    const byId = (id: string) => items.find(i => i.id === id)!;
    const orderedIds=(ids:string[])=>[...ids].sort((a,b)=>compareVariants(byId(a),byId(b)));
    const productFilters=useProductFilters(garments,location);
    const [comment,setComment]=useState(old.comment||existing?.comment||'');
    const [manualQty,setManualQty]=useState<string[]>(old.manual_quantities||Object.entries(old.quantities||Object.fromEntries((existing?.products||[]).map((l:Row)=>[l.item_id,l.quantity]))).filter(([,q])=>String(q).trim()!=='').map(([id])=>id));
    const models = [...new Set(productFilters.shown.map(i=>i.model))];
    function changed() { setDirty(true); setConfirmed(false); setPending(null); }
    function toggle(id: string) { changed(); setSelected(selected.includes(id) ? selected.filter(x => x !== id) : [...selected, id]); setCalc(null); setGroups([]); }
    async function calculate(refresh = false) { setBusy(true); setError(''); try {
        if (refresh) {
        setSyncMessage('Сверяем продажи с площадками…');
        let job = await api('cutting/sales-refresh', { start: params.start, end: params.end });
        const limit = Date.now() + 150000;
        while (!['done', 'failed'].includes(job.status)) {
            if (Date.now() > limit)
                throw Error('Сверка ещё выполняется. Нажми обновление чуть позже, повторный запрос не создаст дубль.');
            await new Promise(resolve => setTimeout(resolve, 1000));
            job = await api('cutting/sales-refresh/' + job.id);
        }
        if (job.status === 'failed')
            throw Error(job.message || 'Сверка прервана');
        setSalesStatus(await api('cutting/sales-status'));
        }
        setSyncMessage('Рассчитываем потребность…');
        const result = await api('cutting/demand', { ...params, location, item_ids: selected, exclude_task: existing?.id });
        api('cutting/sales-status').then(setSalesStatus).catch(()=>{});
        setPrevious(calc);
        setCalc(result);
        setQty(q => Object.fromEntries(selected.map(id => [id, manualQty.includes(id) ? (q[id] ?? '') : String(result.rows.find((r: Row) => r.item_id === id)?.recommendation ?? '')])));
        changed();
    }
    catch (e) {
        setError(errorText(e));
    }
    finally {
        setBusy(false);
        setSyncMessage('');
    } }
    function applyRecommendations() { if (!calc)
        return; changed(); setManualQty(ids=>ids.filter(id=>calc.rows.find((r:Row)=>r.item_id===id)?.recommendation==null)); setQty(Object.fromEntries(calc.rows.slice().sort((a:Row,b:Row)=>compareVariants(byId(a.item_id),byId(b.item_id))).map((r: Row) => [r.item_id, r.recommendation == null ? (qty[r.item_id] || '') : String(r.recommendation)]))); setGroups([]); }
    function addGroup() { changed(); setGroups([...groups, { id: crypto.randomUUID(), model: byId(selected[0])?.model || '', ratio: '', material: '', quantity: '', products: {}, norm: '', preliminary: true }]); }
    function updateGroup(index: number, key: string, value: any) { changed(); setGroups(groups.map((g, i) => i === index ? { ...g, [key]: value } : g)); }
    const productTotals = groups.reduce((out: Record<string, number>, g) => { Object.entries(g.products || {}).forEach(([id, q]) => { out[id] = (out[id] || 0) + Number(q); }); return out; }, {});
    const materialTotals = groups.reduce((out: Record<string, number>, g) => { if (g.material)
        out[g.material] = (out[g.material] || 0) + Number(g.quantity); return out; }, {});
    async function save(issue: boolean) {
        setBusy(true);
        setError('');
        try {
            const products = Object.entries(groups.length ? productTotals : qty).filter(([id, q]) => selected.includes(id) && Number(q) > 0).map(([item_id, quantity]) => ({ item_id, quantity: String(quantity) }));
            const materialLines = Object.entries(materialTotals).filter(([, q]) => q > 0).map(([item_id, quantity]) => ({ item_id, quantity: String(quantity) }));
            if (issue && (!groups.length || groups.some(g => !g.material || !g.ratio.trim() || !(Number(g.quantity) > 0) || !Object.values(g.products).some(q => Number(q) > 0))))
                throw Error('Заполни раскладки, ткань, расход и выпуск каждого кроя');
            if (issue && products.some(l => { const need = Number(qty[l.item_id]); return !(need > 0) || Number(l.quantity) < need * .8 || Number(l.quantity) > need * 1.2; }))
                throw Error('Выпуск выходит за допуск ±20%. Скорректируй потребность или раскладку');
            const payload = { new_id: draftId, ...(existing ? { id: existing.id, version: existing.version } : {}), title: title || 'Задание на крой', location, products, materials: materialLines, cutting: { schema: 1, comment, selected, quantities: qty, manual_quantities: manualQty, parameters: params, calculation: calc, groups, manual_reason: reason, due_on: due, available_on: available, assignee, technology_confirmed: confirmed, excluded: selected.filter(id => !(productTotals[id] > 0)) } };
            const request = pending || { operation_id: crypto.randomUUID(), command: issue ? 'task.issue' : 'task.save', payload, reason: issue ? 'Выдача проверенного задания' : 'Сохранение черновика' };
            setPending(request);
            await api('commands', request);
            setDirty(false);
            await saved();
        }
        catch (e) {
            setError(errorText(e));
        }
        finally {
            setBusy(false);
        }
    }
    const closeEditor = async () => { if (!dirty || await confirmAction('Закрыть задание?','Есть несохранённые изменения.','Закрыть без сохранения'))
        close(); };
    const hasNeed = selected.some(id => Number(qty[id]) > 0);
    const completeGroups = groups.length > 0 && groups.every(g => g.material && g.ratio.trim() && Number(g.quantity) > 0 && Object.values(g.products).some(q => Number(q) > 0));
    const outsideTolerance = Object.entries(productTotals).some(([id, value]) => value > 0 && (!(Number(qty[id]) > 0) || value < Number(qty[id]) * .8 || value > Number(qty[id]) * 1.2));
    const canIssue = completeGroups && !outsideTolerance && !!due && !!assignee && (!available || available >= due);
    return <div className="cutting cut-editor">
  <div className="cut-workspace-toolbar"><button className="secondary" disabled={busy} onClick={closeEditor}>← Задания</button><span className="cut-context">{existing?.number || 'Новое задание'}</span><button className="secondary" disabled={busy || !!pending} onClick={() => save(false)}>Сохранить черновик</button></div>
  <nav className="cut-steps" aria-label="Подготовка задания">{['Товары и потребность', 'Раскладки', 'Проверка и выдача'].map((label, index) => <button className={step === index ? 'is-current' : ''} key={label} aria-current={step === index ? 'step' : undefined} disabled={index > 0 && !calc || busy} onClick={() => setStep(index)}><span>{String(index + 1).padStart(2, '0')}</span><strong>{label}</strong>{index < step && <Icon name="check" size={16}/>}</button>)}</nav>
  {error && <CutNotice error>{error}{pending && <div className="cut-button-row"><button disabled={busy} onClick={() => save(pending.command === 'task.issue')}>Повторить запрос</button><button className="secondary" onClick={() => { setPending(null); setError(''); }}>Вернуться к редактированию</button></div>}</CutNotice>}
  <fieldset disabled={busy || !!pending} className="cut-fields">

   {step === 0 && <>
    <CutSection title="Товары" description="Выбери модели, цвета и размеры для задания." action={<span className="cut-counter">Выбрано {selected.length}</span>}>
     <ProductFilters filters={productFilters} categoryLabel="Категории товаров для кроя" searchLabel="Найти товар для кроя"/>
     <div className="cut-picker">{models.map(model => { const variants = productFilters.shown.filter(i => i.model === model), all = variants.every(i => selected.includes(i.id)), count = variants.filter(i => selected.includes(i.id)).length; return <details key={model}><summary><Photo item={variants.find(i=>photoCandidates(i).length)||variants[0]}/><strong>{model}</strong><StockSummary {...modelStock(variants,location)} className="cut-picker-stock"/><span className={'cut-model-count ' + (count ? 'has-selection' : '')}>{count} / {variants.length}</span><Icon name="down" size={16}/></summary><div className="cut-picker-content"><button className="text-button" onClick={() => { changed(); setSelected([...selected.filter(id => !variants.some(i => i.id === id)), ...(all ? [] : variants.map(i => i.id))]); setCalc(null); setGroups([]); }}>{all ? 'Снять выбор' : 'Выбрать все варианты'}</button><div className="cut-variant-options">{variants.map(i => <label className="cut-check" key={i.id}><input aria-label={(i.color || i.name) + ' · ' + (i.size || 'Без размера')} type="checkbox" checked={selected.includes(i.id)} onChange={() => toggle(i.id)}/><span>{i.color || i.name}<span className="cut-size">{i.size || 'Без размера'}</span></span></label>)}</div></div></details>; })}{!models.length && <div className="cut-inline-empty">Модели не найдены. Попробуй другое название.</div>}</div>
    </CutSection>
    <CutSection title="Продажи и потребность" description="Проверь продажи площадок и доступный запас, затем задай количество к крою.">
     {!['ready', 'ready_cached'].includes(salesStatus.platforms?.ozon?.state) && salesStatus.platforms?.ozon && <CutNotice>Ozon: {salesStatus.platforms.ozon.reason || 'данные ещё загружаются'}. При неполных данных количество можно указать вручную.</CutNotice>}
     {['waiting', 'unavailable'].includes(salesStatus.platforms?.wb?.state) && <CutNotice>ВБ: {salesStatus.platforms.wb.reason}{salesStatus.platforms.wb.next_retry_at ? ' · повтор не раньше ' + new Date(salesStatus.platforms.wb.next_retry_at).toLocaleString('ru-RU', { timeZone: 'Europe/Moscow' }) : ''}</CutNotice>}
     {['ready','ready_cached'].includes(salesStatus.platforms?.wb?.state) && <p className="cut-hint">Wildberries: оперативные данные продаж. Итоговый финансовый отчёт ещё не сверён.</p>}
     <div className="cut-form-grid cut-period-grid"><label>Продажи с<input type="date" value={params.start} onChange={e => { setParams({ ...params, start: e.target.value }); setCalc(null); changed(); }}/></label><label>По<input type="date" max={shift(-1)} value={params.end} onChange={e => { setParams({ ...params, end: e.target.value }); setCalc(null); changed(); }}/></label><label>Запас на дней<input type="number" min="1" max="365" value={params.horizon} onChange={e => { setParams({ ...params, horizon: Number(e.target.value) }); setCalc(null); changed(); }}/></label><label>Покрытие к дате<input type="date" value={params.target_date} onChange={e => { setParams({ ...params, target_date: e.target.value }); setCalc(null); changed(); }}/></label></div>
     <div className="cut-button-row"><button disabled={!selected.length} onClick={() => calculate(!!calc)}>{busy ? syncMessage || 'Сверяем…' : calc ? 'Обновить данные' : 'Рассчитать потребность'}</button>{!selected.length && <span className="cut-hint">Сначала выбери товары выше.</span>}</div>
     {calc && <div className="cut-result-stack"><div className="cut-result-heading"><span className="cut-hint">Обновлено {new Date(calc.calculated_at).toLocaleString('ru-RU', { timeZone: 'Europe/Moscow' })}</span><button className="secondary" onClick={applyRecommendations}>Применить рекомендации</button></div>
      {previous && previous.fingerprint !== calc.fingerprint && <CutNotice>Исходные данные изменились. Твои количества сохранены — проверь новые рекомендации.</CutNotice>}
      <div className="table-wrap"><table className="cut-data-table cut-demand-table"><thead><tr><th>Товар</th><th>Продажи − возвраты</th><th>Доступно у нас</th><th>Ожидается</th><th>Рекомендация</th><th>К крою</th></tr></thead><tbody>{calc.rows.slice().sort((a:Row,b:Row)=>compareVariants(byId(a.item_id),byId(b.item_id))).map((r: Row) => { const item = byId(r.item_id); return <tr key={item.id}><td><ProductCell item={item}/></td><td data-label="Продажи − возвраты"><details className="cut-disclosure"><summary><strong>{n(r.net)}</strong><Icon name="down" size={13}/></summary><div className="cut-breakdown">{Object.entries(r.platforms).map(([key, v]: [
                string,
                any
            ]) => <div key={key} title={v.known ? 'Загружено ' + new Date(v.at).toLocaleString('ru-RU', { timeZone: 'Europe/Moscow' }) + ' · ' + (v.source || '') : undefined}><span>{key === 'wb' ? 'ВБ' : 'Ozon'}</span><strong>{v.known ? `${v.sales} − ${v.returns} = ${v.net ?? v.sales - v.returns}` : '—'}</strong>{key==='ozon'&&(v.catalog_matches||[]).map((card:Row)=><small key={card.product_id}><a href={'https://www.ozon.ru/product/'+(card.skus?.[0]||card.product_id)+'/'} target="_blank" rel="noreferrer">{card.archived?'Карточка в архиве':'Карточка Ozon'}</a>{card.offer_id&&<span> · {card.offer_id}</span>}</small>)}{(!v.known || v.listed === false) && <small className={!v.known ? 'cut-warning' : ''}>{v.known ? 'Не найдено в подключённом каталоге' : v.reason || 'Нет сверенных данных'}</small>}</div>)}</div></details></td><td data-label="Доступно у нас">{r.stock_assumed_zero ? <span title="Остаток не внесён. Для рекомендации принят за ноль; складской остаток не изменён.">0<small>Принято за 0</small></span> : n(r.free)}</td><td data-label="Ожидается">{r.coverage.length ? <details className="cut-disclosure"><summary><strong>{n(r.future)}</strong><Icon name="down" size={13}/></summary><div className="cut-breakdown">{r.coverage.map((c: Row) => <div key={c.id}><strong>{c.title}</strong><span>{n(c.quantity)} · {c.date ? dateText(c.date) : 'Без даты'}{c.included ? ' · учтено' : ''}</span>{c.uncertain && <label className="cut-check"><input type="checkbox" checked={params.include_uncertain.includes(c.id)} onChange={e => { setParams({ ...params, include_uncertain: e.target.checked ? [...params.include_uncertain, c.id] : params.include_uncertain.filter((id: string) => id !== c.id) }); setCalc(null); changed(); }}/><span>Учесть после обновления</span></label>}</div>)}</div></details> : n(r.future)}</td><td data-label="Рекомендация"><strong>{n(r.recommendation)}</strong>{previous && previous.rows.find((x: Row) => x.item_id === item.id)?.recommendation !== r.recommendation && <small>Было {n(previous.rows.find((x: Row) => x.item_id === item.id)?.recommendation)}</small>}</td><td data-label="К крою"><input aria-label={'К крою ' + item.name} className="cut-qty" type="number" min="0" step="1" value={qty[item.id] || ''} onChange={e => { setManualQty(ids=>[...new Set([...ids,item.id])]); setQty({ ...qty, [item.id]: e.target.value }); setGroups([]); changed(); }}/></td></tr>; })}</tbody></table></div>
      <label>Основание ручного расчёта или поправки<textarea value={reason} onChange={e => { setReason(e.target.value); changed(); }} placeholder="Укажи причину изменения. Обязательно, если данных о продажах недостаточно."/></label>
     </div>}
    </CutSection>
   </>}
   {step === 1 && <CutSection title="Раскладки задания" description="Объединяй цвета одной модели с одинаковым распределением размеров.">
    <LayoutBuilder api={api} selected={orderedIds(selected).map(byId).filter(i => Number(qty[i.id]) > 0)} qty={qty} materials={materials} norms={norms} location={location} add={g => { changed(); setGroups([...groups, ...g]); }}/>
    {groups.map((g, index) => <section className="cut-layout" key={g.id}><div className="cut-layout-heading"><div><span className="cut-eyebrow">Крой {String(index + 1).padStart(2, '0')}</span><h4>{g.model}</h4></div><button className="text-button" onClick={() => { changed(); setGroups(groups.filter((_, i) => i !== index)); }}>Убрать крой</button></div><div className="cut-form-grid"><label>Модель<Choice label="Модель" value={g.model} onChange={e => { changed(); setGroups(groups.map((x, i) => i === index ? { ...x, model: e.target.value, products: {} } : x)); }}>{[...new Set(selected.map(id => byId(id).model))].map(m => <option key={m}>{m}</option>)}</Choice></label><label>Соотношение размеров<input placeholder="Например, 50% S · 50% M" value={g.ratio} onChange={e => updateGroup(index, 'ratio', e.target.value)}/></label><label>Ткань<Choice label="Ткань" value={g.material} onChange={e => updateGroup(index, 'material', e.target.value)}><option value="">Выбери материал</option>{materials.map(m => <option key={m.id} value={m.id}>{m.name} · {unitLabel(m)}</option>)}</Choice></label><label>Расход{g.material ? ' · ' + unitLabel(byId(g.material)) : ''}<input aria-label="Расход" type="number" min="0" step={byId(g.material)?.unit === 'roll' ? '1' : '0.001'} value={g.quantity} onChange={e => updateGroup(index, 'quantity', e.target.value)}/></label></div><div className="cut-subsection"><h4>Плановый выпуск</h4><div className="cut-quantity-grid">{orderedIds(selected).map(byId).filter(i => i.model === g.model && Number(qty[i.id]) > 0).map(i => <label key={i.id}><span>{i.color}<span className="cut-size">{i.size}</span></span><input aria-label={i.color + ' · ' + i.size} type="number" min="0" step="1" value={g.products[i.id] || ''} onChange={e => updateGroup(index, 'products', { ...g.products, [i.id]: e.target.value })}/></label>)}</div></div></section>)}
    {!groups.length && <div className="cut-inline-empty">Подбери раскладки автоматически или добавь ручной крой.</div>}
    <div className="cut-button-row"><button className="secondary" onClick={addGroup}>+ Ручной крой</button></div>
   </CutSection>}
   {step === 2 && <>
    <CutSection title="Исполнение задания" description="Назначь сотрудника и сроки. Дата доступности не снимает заморозку автоматически."><div className="cut-form-grid cut-form-grid--three"><label>Исполнитель<Choice label="Исполнитель" value={assignee} onChange={e => { setAssignee(e.target.value); changed(); }}><option value="">Выбери сотрудника</option>{users.map(u => <option key={u.id} value={u.id}>{u.name}</option>)}</Choice></label><label>Срок кроя<input type="date" value={due} onChange={e => { setDue(e.target.value); changed(); }}/></label><label>Ожидается к продаже<input type="date" min={due || undefined} value={available} onChange={e => { setAvailable(e.target.value); changed(); }}/></label></div></CutSection>
    <CutSection title="Проверка выпуска" description="Сравни план с потребностью. Допуск по каждому размеру — ±20%."><div className="table-wrap"><table className="cut-data-table"><thead><tr><th>Товар</th><th>Потребность</th><th>План выпуска</th><th>Отклонение</th></tr></thead><tbody>{orderedIds(selected).filter(id => Number(qty[id]) > 0).map(id => { const q = Number(qty[id]), actual = productTotals[id] || 0, dev = (actual / q - 1) * 100; return <tr key={id}><td><ProductCell item={byId(id)}/></td><td data-label="Потребность">{n(q)}</td><td data-label="План выпуска"><strong>{n(actual)}</strong></td><td data-label="Отклонение"><span className={actual && Math.abs(dev) > 20 ? 'cut-missing' : ''}>{actual ? (dev > 0 ? '+' : '') + dev.toFixed(1) + '%' : 'Не включён'}</span></td></tr>; })}</tbody></table></div>{outsideTolerance && <CutNotice>Есть отклонения больше ±20%. Скорректируй потребность или раскладки перед выдачей.</CutNotice>}</CutSection>
    <CutSection title="Ткань к резерву"><div className="cut-material-list">{Object.entries(materialTotals).map(([id, q]) => <div key={id}><span>{byId(id).name}</span><strong>{n(q)} <small>{unitLabel(byId(id))}</small></strong></div>)}</div><label className="cut-check cut-confirmation"><input type="checkbox" checked={confirmed} onChange={e => { setConfirmed(e.target.checked); setDirty(true); setPending(null); }}/><span>Проверены количества, совместимость ткани и применимость раскладок</span></label></CutSection>
   </>}
   <CutSection title="Комментарий"><label><span className="sr-only">Комментарий к заданию</span><textarea aria-label="Комментарий к заданию" maxLength={4000} placeholder="Детали и пожелания к заданию" value={comment} onChange={e=>{setComment(e.target.value);changed()}}/></label></CutSection>
  </fieldset>
  <div className="cut-footer"><span className="cut-hint">{step === 0 && !hasNeed && calc ? 'Укажи количество хотя бы для одного товара.' : step === 1 && !completeGroups ? 'Заполни ткань, расход и план выпуска каждого кроя.' : step === 2 && !canIssue ? 'Проверь исполнителя, сроки и отклонения.' : ''}</span><div className="cut-button-row">{step > 0 && <button className="secondary" disabled={busy} onClick={() => setStep(step - 1)}>Назад</button>}{step < 2 ? <button disabled={busy || !calc || step === 0 && !hasNeed || step === 1 && !completeGroups} onClick={() => setStep(step + 1)}>Далее →</button> : <button disabled={busy || !confirmed || !canIssue || !!pending} onClick={() => save(true)}>{busy ? 'Сохраняю…' : 'Выдать задание'}</button>}</div></div>
 </div>;
}
function LayoutBuilder({ api, selected, qty, materials, norms, location, add }: {
    api: Api;
    selected: Row[];
    qty: Record<string, string>;
    materials: Row[];
    norms: Record<string, number>;
    location: string;
    add: (groups: Row[]) => void;
}) {
    const models = [...new Set(selected.map(i => i.model))];
    const [model, setModel] = useState(models[0] || ''), [norm, setNorm] = useState(''), [bindings, setBindings] = useState<Record<string, string>>({}), [ratios, setRatios] = useState(''), [result, setResult] = useState<Row | null>(null), [error, setError] = useState(''), [busy, setBusy] = useState(false);
    const variants = selected.filter(i => i.model === model), colors = [...new Set(variants.map(i => i.color))], sizes = [...new Set(variants.map(i => i.size))].sort(compareSizes);
    const inputSignature = JSON.stringify({ ids: selected.map(i => i.id), qty, model, norm, bindings, ratios });
    useEffect(() => { setResult(null); }, [inputSignature]);
    async function run() {
        setBusy(true);
        setError('');
        setResult(null);
        try {
            const patterns = ratios.split('\n').filter(x => x.trim()).map(line => { const values = line.split(':').map(Number); if (values.length !== sizes.length || values.some(v => !Number.isInteger(v) || v < 0))
                throw Error('Укажи целые доли через двоеточие для каждого размера'); return Object.fromEntries(sizes.map((s, i) => [s, values[i]])); });
            const needs = Object.fromEntries(colors.map(c => [c, Object.fromEntries(sizes.map(s => [s, variants.filter(i => i.color === c && i.size === s).reduce((sum, i) => sum + Number(qty[i.id]), 0)]))]));
            const available = Object.fromEntries(colors.map(c => { const m = materials.find(i => i.id === bindings[c]), b = m?.balances.find((b: Row) => b.location === location); if (!m || m.unit !== 'roll' || !b?.known)
                throw Error('Для подбора нужны известные остатки целых рулонов по каждому цвету'); return [c, Math.min(30, Number(b.free))]; }));
            if (new Set(Object.values(bindings)).size !== colors.length)
                throw Error('Для разных цветов выбери разные материалы');
            const r = await api('planning/grouped-layout', { model: norm, needs_by_color: needs, ratios: patterns, available_by_color: available });
            setResult({ ...r, ratios: patterns });
        }
        catch (e) {
            setError(errorText(e));
        }
        finally {
            setBusy(false);
        }
    }
    function accept() { const groups: Row[] = []; for (const [color, row] of Object.entries(result!.result) as [
        string,
        any
    ][]) {
        for (const pattern of [...new Set(row.patterns)] as number[]) {
            const rolls = row.patterns.filter((p: number) => p === pattern).length;
            const output = result!.pattern_outputs[pattern];
            groups.push({ id: crypto.randomUUID(), model, color, material: bindings[color], quantity: String(rolls), ratio: Object.entries(result!.ratios[pattern]).filter(([, v]) => Number(v) > 0).map(([s, v]) => s + ': ' + v).join(' · '), norm, preliminary: true, products: Object.fromEntries(variants.filter(i => i.color === color).map(i => [i.id, String((output[i.size] || 0) * rolls)])) });
        }
    } add(groups); setResult(null); }
    const rollMaterials = materials.filter(m => m.unit === 'roll');
    return <details className="cut-auto-layout"><summary><span className="cut-auto-icon"><Icon name="planning" size={19}/></span><span><strong>Подобрать раскладки</strong><small>{rollMaterials.length ? 'По потребности, доступной ткани и нормам' : 'Нужна ткань с учётом в рулонах'}</small></span><Icon name="down" size={17}/></summary><div className="cut-auto-body">{!rollMaterials.length ? <CutNotice>Для автоподбора нужна ткань с учётом в целых рулонах. Сейчас таких материалов нет. Добавь ручной крой ниже.</CutNotice> : <>
  <div className="cut-form-grid"><label>Модель<Choice label="Модель для подбора" value={model} onChange={e => { setModel(e.target.value); setBindings({}); setResult(null); setRatios(''); }}>{models.map(m => <option key={m}>{m}</option>)}</Choice></label><label>Норма выхода с рулона<Choice label="Норма Жени" value={norm} onChange={e => { setNorm(e.target.value); setResult(null); }}><option value="">Сопоставь модель с нормой</option>{Object.entries(norms).map(([m, q]) => <option key={m} value={m}>{m} · {q} шт.</option>)}</Choice></label></div>
  <div className="cut-subsection"><h4>Ткань по цветам</h4><div className="cut-form-grid">{colors.map(c => <label key={c}>{c || 'Цвет не указан'}<Choice label={'Ткань · ' + (c || 'Цвет не указан')} value={bindings[c] || ''} onChange={e => { setBindings({ ...bindings, [c]: e.target.value }); setResult(null); }}><option value="">Выбери рулоны</option>{rollMaterials.map(m => <option key={m.id} value={m.id}>{m.name}</option>)}</Choice></label>)}</div></div>
  <label>Допустимые соотношения: {sizes.join(' : ')}<textarea value={ratios} onChange={e => { setRatios(e.target.value); setResult(null); }} placeholder="Каждая раскладка с новой строки, например 1:1:0"/><span className="cut-hint">Укажи только допустимые для ткани соотношения. Норма даёт предварительный выход.</span></label>
  {error && <CutNotice error>{error}</CutNotice>}
  <div className="cut-button-row"><button className="secondary" disabled={busy || !norm || !ratios} onClick={run}>{busy ? 'Подбираю…' : 'Подобрать'}</button></div>
  {result && <div className="cut-auto-result"><CutNotice>{result.message}</CutNotice>{result.result && <><div className="cut-output-grid">{Object.entries(result.result).map(([color, row]: [
        string,
        any
    ]) => <div key={color}><span className="cut-eyebrow">{color}</span><strong>{row.rolls} <small>рул.</small></strong><p>{Object.entries(row.output).sort(([a],[b])=>compareSizes(a,b)).map(([size, q]) => size + ' — ' + q + ' шт.').join(' · ')}</p></div>)}</div><div className="cut-button-row"><button onClick={accept}>Добавить в задание</button></div></>}</div>}
 </>}</div></details>;
}
function CutDocument({ api, task, items, users, canPlan, canCut, canAdjust, refresh, close, edit }: {
    api: Api;
    task: Row;
    items: Row[];
    users: Row[];
    canPlan: boolean;
    canCut: boolean;
    canAdjust: boolean;
    refresh: () => Promise<any>;
    close: () => void;
    edit: () => void;
}) {
    const [mode, setMode] = useState(''), [error, setError] = useState(''), [busy, setBusy] = useState(false), [values, setValues] = useState<Record<string, string>>({}), [materials, setMaterials] = useState<Record<string, string>>({}), [reason, setReason] = useState(''), [confirmed, setConfirmed] = useState(false), [pending, setPending] = useState<Row | null>(null), [copied, setCopied] = useState(false);
    const get = (id: string) => items.find(i => i.id === id) || { name: id };
    const active = ['approved', 'partial'].includes(task.status);
    const orderedLines=(lines:Row[])=>[...lines].sort((a,b)=>compareVariants(get(a.item_id),get(b.item_id)));
    const hasActions = canPlan && ['draft', 'approved', 'partial'].includes(task.status) || canAdjust && task.facts.some((f: string) => !task.reversed_facts?.includes(f));
    const operation = useRef<HTMLElement>(null);
    useEffect(() => { window.scrollTo({ top: 0, behavior: 'instant' }); }, []);
    useEffect(() => { if (mode)
        requestAnimationFrame(() => operation.current?.scrollIntoView({ block: 'start', behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' })); }, [mode]);
    function start(kind: string) { setMode(kind); setError(''); setReason(''); setPending(null); setConfirmed(false); setValues(Object.fromEntries((kind === 'revise' ? task.products : task.remaining_products).map((l: Row) => [l.item_id, kind === 'revise' ? String(l.quantity) : '']))); setMaterials(Object.fromEntries((kind === 'revise' ? task.materials : task.remaining_materials).map((l: Row) => [l.item_id, kind === 'revise' ? String(l.quantity) : '']))); }
    async function run() { setBusy(true); setError(''); try {
        if (!reason.trim())
            throw Error('Укажи основание');
        let command = 'task.' + mode;
        let payload: Row = { id: task.id, version: task.version };
        if (mode === 'confirm' || mode === 'revise') {
            if (mode === 'confirm' && !confirmed)
                throw Error('Подтверди фактические количества');
            payload = { ...payload, confirm_actual: true, products: Object.entries(values).filter(([, q]) => Number(q) > 0).map(([item_id, quantity]) => ({ item_id, quantity })), materials: Object.entries(materials).filter(([, q]) => Number(q) > 0).map(([item_id, quantity]) => ({ item_id, quantity })) };
        }
        if (mode === 'reverse_fact')
            payload.fact_id = task.facts.filter((f: string) => !task.reversed_facts?.includes(f)).at(-1);
        const request = pending || { operation_id: crypto.randomUUID(), command, payload, reason };
        setPending(request);
        await api('commands', request);
        await refresh();
        setMode('');
        setPending(null);
        requestAnimationFrame(() => window.scrollTo({ top: 0, behavior: 'instant' }));
    }
    catch (e) {
        setError(errorText(e));
    }
    finally {
        setBusy(false);
    } }
    const text = [task.number || task.id.slice(0, 8), task.title, 'Срок кроя: ' + (task.due_on || 'не указан'), ...(task.cutting?.comment?['Комментарий: '+task.cutting.comment]:[]), ...(task.cutting?.layout_outdated ? [] : task.cutting?.groups || []).map((g: Row) => [g.model, g.ratio, get(g.material).name + ' · ' + g.quantity, Object.entries(g.products).sort(([a],[b])=>compareVariants(get(a),get(b))).map(([id, q]) => get(id).name + ': ' + q).join('\n')].join('\n')), ...(!task.cutting?.groups?.length || task.cutting?.layout_outdated ? orderedLines(task.products).map((l: Row) => get(l.item_id).name + ': ' + l.quantity) : [])].join('\n\n');
    const total = task.products.reduce((sum: number, l: Row) => sum + Number(l.quantity), 0), remaining = task.remaining_products.reduce((sum: number, l: Row) => sum + Number(l.quantity), 0);
    const restLabel = task.status === 'cancelled' ? 'Не выполнено' : 'Осталось';
    const caption = mode === 'confirm' ? 'Фактический выпуск' : mode === 'revise' ? 'Изменение плана' : mode === 'cancel' ? 'Отмена остатка задания' : 'Исправление последнего факта';
    return <div className="cutting cut-document">
  <div className="cut-workspace-toolbar no-print"><button className="secondary" onClick={close}>← Задания</button><div className="cut-button-row"><button className="secondary" onClick={() => window.print()}>Печать</button><button className="secondary" onClick={async () => { try {
        await navigator.clipboard.writeText(text);
        setCopied(true);
    }
    catch {
        setError('Копирование недоступно в этом браузере');
    } }}>{copied ? 'Скопировано' : 'Копировать'}</button>{hasActions && <ActionMenu>{canPlan && active && <button onClick={() => start('revise')}>Изменить план</button>}{canPlan && ['draft', 'approved', 'partial'].includes(task.status) && <button onClick={() => start('cancel')}>Отменить остаток</button>}{canAdjust && task.facts.some((f: string) => !task.reversed_facts?.includes(f)) && <button onClick={() => start('reverse_fact')}>Исправить последний факт</button>}</ActionMenu>}</div></div>
  <section className="panel cut-hero"><div className="cut-hero-heading"><div><span className="cut-eyebrow">{task.number || task.id.slice(0, 8)}</span><h2>{task.title}</h2></div><span className={'badge cut-status status-' + task.status}>{states[task.status]}</span></div>
   <dl className="cut-document-meta"><div><dt>Срок кроя</dt><dd className={!task.due_on ? 'is-empty' : ''}>{dateText(task.due_on)}</dd></div><div><dt>Исполнитель</dt><dd className={!task.assignee ? 'is-empty' : ''}>{users.find(u => u.id === task.assignee)?.name || 'Не назначен'}</dd></div><div><dt>Ожидается к продаже</dt><dd className={!task.available_on ? 'is-empty' : ''}>{task.available_on ? dateText(task.available_on) : 'Не указана'}</dd></div></dl>
   <div className="cut-hero-footer"><span className="cut-hint">{task.created_at ? 'Создано ' + new Date(task.created_at).toLocaleDateString('ru-RU', { timeZone: 'Europe/Moscow' }) + ' · ' : ''}Версия {task.version}</span><div className="no-print">{canCut && active && <button onClick={() => start('confirm')}>Внести факт кроя</button>}{canPlan && task.status === 'draft' && <button onClick={edit}>Продолжить подготовку</button>}</div></div>
  </section>
  {task.cutting?.layout_outdated && <CutNotice>План изменён. Прежние раскладки сохранены в истории; актуальные количества указаны ниже.</CutNotice>}
  {!task.cutting?.layout_outdated && task.cutting?.groups?.length > 0 && <CutSection title="Раскладки"><div className="cut-layouts-readonly">{task.cutting.groups.map((g: Row, index: number) => <div className="cut-layout cut-layout-readonly" key={g.id}><div className="cut-layout-heading"><div><span className="cut-eyebrow">Крой {String(index + 1).padStart(2, '0')}</span><h4>{g.model}</h4></div><span className="cut-ratio">{g.ratio}</span></div><div className="cut-material-line"><span>{get(g.material).name}</span><strong>{n(g.quantity)} {unitLabel(get(g.material))}</strong></div><div className="cut-size-output">{Object.entries(g.products).sort(([a],[b])=>compareVariants(get(a),get(b))).filter(([, q]) => Number(q) > 0).map(([id, q]) => <span key={id}>{get(id).color || get(id).name}<span className="cut-size">{get(id).size}</span><strong>{n(q)}</strong></span>)}</div></div>)}</div></CutSection>}
  <CutSection title="План выпуска" action={<span className="cut-counter">{n(total)} шт.</span>}>
   {task.products.length > 0 ? <><div className="cut-totals"><div><span>Запланировано</span><strong>{n(total)}</strong></div><div><span>Выполнено</span><strong>{n(total - remaining)}</strong></div><div><span>{restLabel}</span><strong>{n(remaining)}</strong></div></div><div className="table-wrap"><table className="cut-data-table"><thead><tr><th>Изделие</th><th>План</th><th>Выполнено</th><th>{restLabel}</th></tr></thead><tbody>{orderedLines(task.products).map((l: Row) => { const left = Number(task.remaining_products.find((r: Row) => r.item_id === l.item_id)?.quantity || 0); return <tr key={l.item_id}><td><ProductCell item={get(l.item_id)}/></td><td data-label="План">{n(l.quantity)}</td><td data-label="Выполнено">{n(Number(l.quantity) - left)}</td><td data-label={restLabel}><strong>{n(left)}</strong></td></tr>; })}</tbody></table></div></> : <div className="cut-inline-empty">В черновике пока нет товаров. Продолжи подготовку, чтобы добавить позиции.</div>}
  </CutSection>
  {task.cutting?.comment&&<CutSection title="Комментарий"><p className="cut-comment-text">{task.cutting.comment}</p></CutSection>}
  {mode && <section className="panel cut-panel cut-operation no-print" ref={operation}><div className="cut-section-heading"><div><h3>{caption}</h3><p>{mode === 'confirm' ? 'Выбери выполненные позиции и проверь фактический расход ткани.' : mode === 'revise' ? 'Новые общие количества включают уже выполненное. Факт сохранится в истории.' : mode === 'cancel' ? 'Проведённый выпуск сохранится, неиспользованная ткань освободится.' : 'Последний факт будет сторнирован с сохранением истории.'}</p></div><button className="cut-icon-button" aria-label="Закрыть операцию" disabled={busy} onClick={() => { setMode(''); setPending(null); }}><Icon name="close" size={18}/></button></div>
   <div className="cut-section-body">{error && <CutNotice error>{error}</CutNotice>}<fieldset disabled={busy || !!pending} className="cut-fields">
    {mode === 'confirm' && !task.cutting?.layout_outdated && task.cutting?.groups?.length > 0 && <div className="cut-subsection"><h4>Заполнить по раскладке</h4><div className="cut-button-row">{task.cutting.groups.map((g: Row, index: number) => <button className="secondary" key={g.id} onClick={() => { setValues(Object.fromEntries(task.remaining_products.map((l: Row) => [l.item_id, String(Math.min(Number(l.quantity), Number(g.products[l.item_id] || 0)))]))); setMaterials(Object.fromEntries(task.remaining_materials.map((l: Row) => [l.item_id, l.item_id === g.material ? String(Math.min(Number(l.quantity), Number(g.quantity))) : '']))); setConfirmed(false); }}>Подставить крой {index + 1}</button>)}</div></div>}
    {['confirm', 'revise'].includes(mode) && <><div className="cut-subsection"><h4>{mode === 'revise' ? 'Общий план выпуска' : 'Изделия'}</h4><div className="table-wrap"><table className="cut-data-table cut-entry-table"><thead><tr><th>Позиция</th><th>{mode === 'revise' ? 'План' : 'Фактически'}</th></tr></thead><tbody>{orderedLines(mode === 'revise' ? task.products : task.remaining_products).map((l: Row) => <tr key={l.item_id}><td>{mode === 'confirm' ? <label className="cut-check"><input aria-label={'Выполнено: ' + get(l.item_id).name} type="checkbox" checked={Number(values[l.item_id]) > 0} onChange={e => setValues({ ...values, [l.item_id]: e.target.checked ? String(l.quantity) : '' })}/><ProductCell item={get(l.item_id)}/></label> : <ProductCell item={get(l.item_id)}/>}</td><td data-label={mode === 'revise' ? 'План' : 'Фактически'}><input aria-label={'Факт ' + get(l.item_id).name} type="number" min="0" step="1" value={values[l.item_id] || ''} onChange={e => setValues({ ...values, [l.item_id]: e.target.value })}/><small>{mode === 'revise' ? 'Общее количество' : 'Осталось по заданию: ' + n(l.quantity)}</small></td></tr>)}</tbody></table></div></div>
    <div className="cut-subsection"><h4>{mode === 'revise' ? 'Общий план расхода' : 'Фактически использованная ткань'}</h4>{(mode === 'revise' ? task.materials : task.remaining_materials).length ? <div className="cut-quantity-grid">{(mode === 'revise' ? task.materials : task.remaining_materials).map((l: Row) => <label key={l.item_id}><span>{get(l.item_id).name}<span className="cut-unit">{unitLabel(get(l.item_id))}</span></span><input aria-label={get(l.item_id).name + ' · ' + unitLabel(get(l.item_id))} type="number" min="0" step={get(l.item_id).unit === 'roll' ? '1' : '0.001'} value={materials[l.item_id] || ''} onChange={e => setMaterials({ ...materials, [l.item_id]: e.target.value })}/><span className="cut-hint">В задании осталось {n(l.quantity)} {unitLabel(get(l.item_id))}</span></label>)}</div> : <p className="cut-hint">{task.materials.length ? 'Вся выделенная ткань уже учтена в предыдущих фактах.' : 'В этом задании ткань не указана.'}</p>}</div></>}
    {mode === 'reverse_fact' && <CutNotice>Уже размороженный выпуск требует отдельного разбора.</CutNotice>}
    <label>Основание<textarea value={reason} onChange={e => setReason(e.target.value)} placeholder="Укажи основание операции"/></label>
    {mode === 'confirm' && <label className="cut-check cut-confirmation"><input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)}/><span>Проверил фактический выпуск и расход. Изделия поступят в заморозку.</span></label>}
   </fieldset><div className="cut-section-footer"><div className="cut-button-row"><button disabled={busy || mode === 'confirm' && !confirmed} onClick={run}>{busy ? 'Сохраняю…' : pending ? 'Повторить тот же запрос' : 'Подтвердить'}</button><button className="secondary" disabled={busy} onClick={() => { setMode(''); setPending(null); }}>Закрыть</button></div></div></div>
  </section>}
  {error && !mode && <CutNotice error>{error}</CutNotice>}
 </div>;
}
