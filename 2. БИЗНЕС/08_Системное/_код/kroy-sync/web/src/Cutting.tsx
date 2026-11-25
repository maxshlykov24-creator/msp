import { Choice, Select, Icon, HelpTip, ActionMenu, confirmAction, DialogFrame } from './UI';
import { DatePicker } from './DatePicker';
import React, { useEffect, useState, useRef } from 'react';
import './Cutting.css';
import {compareSizes,compareVariants,productCategory,photoCandidates} from './catalogData';
import {ItemPhoto as Photo} from './ItemPhoto';
import {ProductFilters,StockSummary,useProductFilters} from './ProductFilters';
import {modelStock, words} from './catalogData';
type Row = Record<string, any>;
type Api = (path: string, body?: any) => Promise<any>;
const states: Record<string, string> = { draft: 'Черновик', approved: 'Выдано', partial: 'Частично выполнено', complete: 'Завершено', cancelled: 'Отменено' };
const n = (v: any) => v == null ? '—' : Number(v).toLocaleString('ru-RU');
function sourceRemark(label: string, data: Row | undefined, period: Row) {
    if (!data) return '';
    const operational = String(data.reason || '').startsWith('Оперативные продажи');
    const gap = Boolean(data.start && data.end && (data.start > period.start || data.end < period.end));
    const failed = Boolean(data.reason) && !operational;
    if (!gap && !failed && ['ready', 'ready_cached'].includes(data.state)) return '';
    const retry = data.next_retry_at ? ' Повтор не раньше ' + new Date(data.next_retry_at).toLocaleString('ru-RU', { timeZone: 'Europe/Moscow' }) + '.' : '';
    const coverage = gap ? ' Сверка покрывает ' + data.start + ' по ' + data.end + ', выбранный период шире. Пропуск посчитан как 0.' : '';
    return label + ': ' + (data.reason || 'данные ещё загружаются') + '.' + coverage + retry + ' Пропуск посчитан как 0.';
}
const today = () => new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/Moscow', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
const createdDay = (r: Row) => r.created_at ? new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/Moscow', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(r.created_at)) : '';
const shift = (days: number) => { const d = new Date(today() + 'T12:00:00Z'); d.setUTCDate(d.getUTCDate() + days); return d.toISOString().slice(0, 10); };
const defaults = () => ({ start: shift(-30), end: shift(-1), horizon: 30, target_date: shift(30), include_fbo: true, include_uncertain: [] as string[] });
const errorText = (e: unknown) => e instanceof Error ? e.message : String(e);
const unitLabel = (i?: Row) => i?.unit === 'roll' ? 'рул.' : i?.unit === 'meter' ? 'м' : 'шт.';
const foldName = (value: string) => value.trim().toLocaleLowerCase('ru');
const normFor = (model: string, norms: Record<string, number>) => Object.keys(norms).find(name => foldName(name) === foldName(model)) || '';
const dateText = (value?: string) => value ? new Date(value + 'T12:00:00Z').toLocaleDateString('ru-RU') : 'Не указан';
function CutSection({ title, description, action, children, className = '' }: {
    title: string;
    description?: string;
    action?: React.ReactNode;
    children: React.ReactNode;
    className?: string;
}) {
    return <section className={'panel cut-panel ' + className}><div className="cut-section-heading"><div className="help-heading"><h3>{title}</h3>{description && <HelpTip label={'Пояснение: '+title}>{description}</HelpTip>}</div>{action && <div className="cut-section-action">{action}</div>}</div><div className="cut-section-body">{children}</div></section>;
}
function ProductCell({ item }: {
    item: Row;
}) { return <span className="cut-product-cell"><Photo item={item}/><span><strong>{item.model || item.name}</strong><span className="cut-product-caption">{item.color || (!item.model ? item.name : 'Цвет не указан')}{item.size && <span className="cut-size">{item.size}</span>}</span></span></span>; }
function CutNotice({ children, error = false }: {
    children: React.ReactNode;
    error?: boolean;
}) { return <div className={'cut-notice ' + (error ? 'is-error' : '')} role={error ? 'alert' : undefined}><Icon name="attention" size={18}/><div>{children}</div></div>; }
const stepGuide = [
    { label: 'Товары', help: 'Выбери модели, цвета и размеры для задания.' },
    { label: 'Потребность', help: 'Проверь продажи площадок и доступный запас, затем задай количество к крою.' },
    { label: 'Раскладки', help: 'Объединяй цвета одной модели с одинаковым распределением размеров.' },
    { label: 'Проверка и выдача', help: 'Допуск по каждому размеру ±20%.' },
];
function useCutChrome() {
    const ref = useRef<HTMLDivElement>(null);
    useEffect(() => {
        document.documentElement.dataset.cutFocus = '1';
        const node = ref.current;
        const apply = () => { if (node) document.documentElement.style.setProperty('--cut-chrome', Math.ceil(node.getBoundingClientRect().height) + 'px'); };
        apply();
        const observer = new ResizeObserver(apply);
        if (node) observer.observe(node);
        window.addEventListener('resize', apply);
        return () => {
            observer.disconnect();
            window.removeEventListener('resize', apply);
            delete document.documentElement.dataset.cutFocus;
            document.documentElement.style.removeProperty('--cut-chrome');
        };
    }, []);
    return ref;
}
function pairKey(item: Row) { return (item.color || '') + '\0' + (item.size || ''); }
function hasDuplicatePairs(items: Row[]) {
    const seen = new Set<string>();
    for (const item of items) {
        const key = pairKey(item);
        if (seen.has(key)) return true;
        seen.add(key);
    }
    return false;
}
function DemandTable({ rows, items, qty, previous, targetDate, uncertain, onQty, onUncertain }: {
    rows: Row[];
    items: Row[];
    qty: Record<string, string>;
    previous: Row | null;
    targetDate: string;
    uncertain: string[];
    onQty: (id: string, value: string) => void;
    onUncertain: (id: string, checked: boolean) => void;
}) {
    const byId = (id: string) => items.find(i => i.id === id)!;
    const ordered = rows.slice().sort((a, b) => compareVariants(byId(a.item_id), byId(b.item_id)));
    return <div className="table-wrap"><table className="cut-data-table cut-demand-table"><thead><tr><th>Товар</th><th>Продажи − возвраты</th><th>Доступно у нас</th><th>ФБО</th><th>Ожидается</th><th>Рекомендация</th><th>К крою</th></tr></thead><tbody>{ordered.map(row => {
        const item = byId(row.item_id);
        const platforms = Object.entries(row.platforms || {});
        const fbo = Object.entries(row.fbo || {});
        const sales = row.net != null ? n(row.net) : row.net_known != null ? n(row.net_known) : '—';
        const previousRecommendation = previous?.rows.find((x: Row) => x.item_id === item.id)?.recommendation;
        const fboValue = n(row.fbo_missing?.length ? row.fbo_counted : row.fbo_total);
        return <tr key={item.id}>
            <td><ProductCell item={item}/></td>
            <td data-label="Продажи − возвраты">{platforms.length ? <details className="cut-disclosure"><summary className="cut-figure"><strong className="cut-num">{sales}</strong><Icon name="down" size={13}/></summary><div className="cut-breakdown">{platforms.map(([key, v]: [string, any]) => <div key={key} title={v.known ? 'Загружено ' + new Date(v.at).toLocaleString('ru-RU', { timeZone: 'Europe/Moscow' }) + ' · ' + (v.source || '') : v.reason || undefined}><span>{key === 'wb' ? 'ВБ' : 'Ozon'}</span><strong>{v.known ? `${v.sales} − ${v.returns} = ${v.net ?? v.sales - v.returns}` : '0'}</strong>{key==='ozon'&&(v.catalog_matches||[]).map((card:Row)=><small key={card.product_id}><a href={'https://www.ozon.ru/product/'+(card.skus?.[0]||card.product_id)+'/'} target="_blank" rel="noreferrer">{card.archived?'Карточка в архиве':'Карточка Ozon'}</a>{card.offer_id&&<span> · {card.offer_id}</span>}</small>)}{!v.known && <small>{v.reason || 'Посчитано как 0'}</small>}{v.known && v.listed === false && <small>Не найдено в подключённом каталоге</small>}</div>)}</div></details> : <span className="cut-figure"><strong className="cut-num">{sales}</strong><span/></span>}</td>
            <td data-label="Доступно у нас"><span className="cut-figure" title={row.stock_assumed_zero ? 'Для рекомендации принято 0. Складской остаток не изменён.' : undefined}><strong className="cut-num">{row.stock_assumed_zero ? '—' : n(row.free)}</strong><span/></span>{row.stock_assumed_zero && <small>Остаток не внесён</small>}</td>
            <td data-label="ФБО">{fbo.length ? <details className="cut-disclosure"><summary className="cut-figure"><strong className="cut-num">{fboValue}</strong><Icon name="down" size={13}/></summary>{fbo.map(([platform, v]: [string, any]) => <p key={platform}>{platform==='wb'?'Wildberries':'Ozon'}: {v.known?n(v.quantity):'Нет сверенных данных'}{v.stale&&v.known?' · нужно обновить':''}</p>)}</details> : <span className="cut-figure"><strong className="cut-num">{fboValue}</strong><span/></span>}</td>
            <td data-label="Ожидается">{row.coverage?.length ? <details className="cut-disclosure"><summary className="cut-figure"><strong className="cut-num">{n(row.future)}</strong><Icon name="down" size={13}/></summary><div className="cut-breakdown">{row.coverage.map((c: Row) => <div key={c.id}><strong>{c.title}</strong><span>{n(c.quantity)} · {c.date ? dateText(c.date) : 'Без даты'}{c.included ? ' · учтено' : ''}</span>{c.date>targetDate&&<small>Позже выбранной даты</small>}{c.uncertain && (!c.date||c.date<=targetDate) && <label className="cut-check"><input type="checkbox" checked={uncertain.includes(c.id)} onChange={e => onUncertain(c.id, e.target.checked)}/><span>Учесть после обновления</span></label>}</div>)}</div></details> : <span className="cut-figure"><strong className="cut-num">{n(row.future)}</strong><span/></span>}</td>
            <td data-label="Рекомендация"><span className="cut-figure"><strong className="cut-num">{n(row.recommendation)}</strong><span/></span>{previous && previousRecommendation !== row.recommendation && <small>Было {n(previousRecommendation)}</small>}</td>
            <td data-label="К крою"><input aria-label={'К крою ' + item.name} className="cut-qty" type="number" min="0" step="1" value={qty[item.id] || ''} onChange={e => onQty(item.id, e.target.value)}/></td>
        </tr>;
    })}</tbody></table></div>;
}
function ReleaseMatrix({ products, values, onChange }: {
    products: Row[];
    values: Record<string, string>;
    onChange: (id: string, value: string) => void;
}) {
    if (!products.length) return <p className="cut-hint">В задании нет количеств этой модели.</p>;
    if (hasDuplicatePairs(products)) return <div className="cut-quantity-grid">{products.map(item => <label key={item.id}><span>{item.color}<span className="cut-size">{item.size}</span></span><input aria-label={(item.color || '') + ' · ' + (item.size || '')} type="number" min="0" step="1" value={values[item.id] || ''} onChange={e => onChange(item.id, e.target.value)}/></label>)}</div>;
    const colors = [...new Set(products.map(item => item.color || ''))];
    const sizes = [...new Set(products.map(item => item.size || ''))].sort(compareSizes);
    return <div className="cut-matrix-scroll"><table className="cut-release-matrix"><thead><tr><th scope="col">Цвет</th>{sizes.map(size => <th scope="col" key={size || 'none'}>{size || 'Без размера'}</th>)}</tr></thead><tbody>{colors.map(color => <tr key={color || 'none'}><th scope="row">{color || 'Цвет не указан'}</th>{sizes.map(size => { const item = products.find(entry => (entry.color || '') === color && (entry.size || '') === size); return <td key={size || 'none'} className={item ? undefined : 'cut-matrix-empty'}>{item && <input aria-label={(item.color || 'Цвет не указан') + ' · ' + (item.size || 'Без размера')} type="number" min="0" step="1" value={values[item.id] || ''} onChange={e => onChange(item.id, e.target.value)}/>}</td>; })}</tr>)}</tbody></table></div>;
}
export function Cutting({ api, items, rows, locations, permissions, refresh, onPulse }: {
    api: Api;
    items: Row[];
    rows: Row[];
    locations: string[];
    permissions: string[];
    refresh: () => Promise<any>;
    onPulse?: () => void;
}) {
    const [users, setUsers] = useState<Row[]>([]), [opened, setOpened] = useState<Row | null>(null), [editing, setEditing] = useState(false), [query, setQuery] = useState(''), [status, setStatus] = useState('active'), [person, setPerson] = useState(''), [model, setModel] = useState(''), [from, setFrom] = useState(''), [to, setTo] = useState(''), [filtersOpen, setFiltersOpen] = useState(false);
    useEffect(() => { api('cutting/assignees').then(setUsers).catch(() => { }); }, []);
    const plan = permissions.includes('cut_plan'), cut = permissions.includes('cut'), adjust = permissions.includes('adjust');
    const models = [...new Set(items.filter(i => i.kind === 'garment').map(i => i.model))];
    const get = (id: string) => items.find(i => i.id === id) || { name: 'Неизвестная позиция' };
    const filtered = rows.filter(r => (status === 'all' || status === 'awaiting_materials' && r.awaiting_materials && ['approved','partial'].includes(r.status) || status === 'active' && ['draft', 'approved', 'partial'].includes(r.status) || r.status === status) && (!person || r.assignee === person) && (!model || r.products.some((l: Row) => get(l.item_id).model === model)) && (!from || createdDay(r) >= from) && (!to || createdDay(r) <= to) && (!query || [r.title, r.number, r.legacy_number, r.id].join(' ').toLowerCase().includes(query.toLowerCase()))).sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''));
    if (editing)
        return <CutEditor api={api} items={items} locations={locations} users={users} existing={opened} close={() => setEditing(false)} saved={async () => { await refresh(); setEditing(false); setOpened(null); }} onPulse={onPulse}/>;
    if (opened)
        return <CutDocument api={api} task={rows.find(r => r.id === opened.id) || opened} items={items} users={users} canPlan={plan} canCut={cut} canAdjust={adjust} refresh={refresh} close={() => setOpened(null)} edit={() => setEditing(true)}/>;
    const filterCount = Number(!!person) + Number(!!model) + Number(!!from || !!to);
    const reset = () => { setStatus('active'); setPerson(''); setModel(''); setFrom(''); setTo(''); setQuery(''); };
    const dateLabel = (v: string) => v ? new Date(v + 'T12:00:00Z').toLocaleDateString('ru-RU') : 'Не указан';
    const progress = (r: Row) => { const total = r.products.reduce((sum: number, l: Row) => sum + Number(l.quantity), 0), left = r.remaining_products.reduce((sum: number, l: Row) => sum + Number(l.quantity), 0); return { total, done: total - left }; };
    const overdue = (r: Row) => r.due_on && r.due_on < today() && ['approved', 'partial'].includes(r.status);
    const chips = [...(status !== 'active' ? [{ text: status === 'all' ? 'Все статусы' : states[status], clear: () => setStatus('active') }] : []), ...(person ? [{ text: users.find(u => u.id === person)?.name || 'Исполнитель', clear: () => setPerson('') }] : []), ...(model ? [{ text: model, clear: () => setModel('') }] : []), ...(from || to ? [{ text: from && to ? 'с ' + dateLabel(from) + ' по ' + dateLabel(to) : from ? 'с ' + dateLabel(from) : 'по ' + dateLabel(to), clear: () => { setFrom(''); setTo(''); } }] : [])];
    return <div className="cutting">
  <div className="cut-toolbar cut-heading"><div/>{plan && <button onClick={() => { setOpened(null); setEditing(true); }}>+ Задание</button>}</div>
  <div className="cut-search-row"><div className="search-field"><Icon name="search" size={18}/><input aria-label="Найти задание" placeholder="Номер или название задания" value={query} onChange={e => setQuery(e.target.value)}/>{query && <button aria-label="Очистить поиск заданий" onClick={() => setQuery('')}><Icon name="close" size={15}/></button>}</div><div className="cut-status-filter"><Select label="Статус задания" value={status} onChange={setStatus} options={[{ value: 'active', label: 'Активные задания' }, { value: 'all', label: 'Все статусы' }, { value: 'awaiting_materials', label: 'Ожидают ткань' }, ...Object.entries(states).map(([value, label]) => ({ value, label }))]}/></div><button className={'secondary filter-toggle ' + (filtersOpen || filterCount ? 'active' : '')} aria-expanded={filtersOpen} aria-controls="cut-filters" onClick={() => setFiltersOpen(!filtersOpen)}><Icon name="filter" size={17}/><span>Фильтры</span>{filterCount > 0 && <span className="filter-count">{filterCount}</span>}<Icon name="down" size={15}/></button></div>
  {filtersOpen && <div id="cut-filters" className="cut-filter-panel"><div className="cut-filter-title"><strong>Фильтры заданий</strong><button className="text-button" aria-label="Скрыть фильтры" onClick={() => setFiltersOpen(false)}><Icon name="close" size={16}/></button></div><div className="cut-filter-grid"><label>Исполнитель<Select label="Исполнитель" value={person} onChange={setPerson} options={[{ value: '', label: 'Все исполнители' }, ...users.map(u => ({ value: u.id, label: u.name }))]}/></label><label>Модель<Select label="Модель в заданиях" value={model} onChange={setModel} options={[{ value: '', label: 'Все модели' }, ...models.map(m => ({ value: m, label: m }))]}/></label><label>Создано с<DatePicker label="Создано с" max={to || undefined} value={from} onChange={setFrom}/></label><label>По дату<DatePicker label="По дату" min={from || undefined} value={to} onChange={setTo}/></label></div></div>}
  <div className="cut-list-meta"><span>Показано {filtered.length} из {rows.length}</span>{(chips.length > 0 || query) && <button className="text-button" onClick={reset}>Сбросить фильтры</button>}</div>
  {chips.length > 0 && <div className="filter-chips">{chips.map(c => <button key={c.text} onClick={c.clear} aria-label={'Убрать фильтр: ' + c.text}>{c.text}<Icon name="close" size={13}/></button>)}</div>}
  {filtered.length > 0 ? <><div className="panel table-wrap cut-register"><table><thead><tr><th>Задание</th><th>Статус</th><th>Срок кроя</th><th>Исполнитель</th><th>Выполнено</th><th aria-label="Открыть"/></tr></thead><tbody>{filtered.map(r => { const { total, done } = progress(r); return <tr className="product-row" key={r.id} tabIndex={0} aria-label={'Открыть задание: ' + r.title} onClick={() => setOpened(r)} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            setOpened(r);
        } }}><td className="cut-title-cell"><strong>{r.title}</strong><small>{r.number || r.id.slice(0, 8)}<span className="meta-dot">·</span>{r.created_at ? new Date(r.created_at).toLocaleDateString('ru-RU', { timeZone: 'Europe/Moscow' }) : 'Дата не указана'}</small></td><td><span className={'badge cut-status status-' + r.status}>{states[r.status]}</span>{r.awaiting_materials&&['approved','partial'].includes(r.status)&&<small className="cut-warning">Ожидает ткань</small>}</td><td>{dateLabel(r.due_on)}{overdue(r) && <small className="cut-warning">Просрочено</small>}</td><td>{users.find(u => u.id === r.assignee)?.name || 'Не назначен'}</td><td><div className="cut-progress"><span className="cut-progress-count"><strong>{n(done)}</strong><span>из {n(total)}</span></span><progress aria-label="Выполнение задания" max={total || 1} value={done}/></div></td><td><Icon name="chevron" size={16}/></td></tr>; })}</tbody></table></div>
  <div className="cut-mobile-list">{filtered.map(r => { const { total, done } = progress(r); return <button className="cut-task-card" key={r.id} onClick={() => setOpened(r)}><span className="cut-card-top"><small>{r.number || r.id.slice(0, 8)}</small><span className={'badge cut-status status-' + r.status}>{states[r.status]}</span>{r.awaiting_materials&&['approved','partial'].includes(r.status)&&<small className="cut-warning">Ожидает ткань</small>}</span><strong>{r.title}</strong><span className="cut-card-meta"><span>Срок: {dateLabel(r.due_on)}</span>{overdue(r) && <span className="cut-warning">Просрочено</span>}</span><span className="cut-card-meta">{users.find(u => u.id === r.assignee)?.name || 'Исполнитель не назначен'}</span><span className="cut-card-bottom"><span>{n(done)} из {n(total)} выполнено</span><Icon name="chevron" size={16}/></span><progress aria-label="Выполнение задания" max={total || 1} value={done}/></button>; })}</div></> : <div className="cut-empty"><h3>{rows.length ? 'Задания не найдены' : 'Здесь будут задания на крой'}</h3><p>{rows.length ? 'Попробуй изменить условия или очистить поиск.' : 'Создай задание: выбери товары, проверь потребность и подготовь раскладки.'}</p>{rows.length ? <button className="secondary" onClick={reset}>Сбросить фильтры</button> : plan && <button className="secondary" onClick={() => { setOpened(null); setEditing(true); }}>Создать первое задание</button>}</div>}
 </div>;
}
function CutEditor({ api, items, locations, users, existing, close, saved, onPulse }: {
    api: Api;
    items: Row[];
    locations: string[];
    users: Row[];
    existing: Row | null;
    close: () => void;
    saved: () => Promise<void>;
    onPulse?: () => void;
}) {
    const old = existing?.cutting || {};
    const [syncMessage, setSyncMessage] = useState('');
    const [draftId] = useState(() => crypto.randomUUID()), [salesStatus, setSalesStatus] = useState<Row>({});
    useEffect(() => { let active=true;const refresh=()=>api('cutting/sales-status').then(data=>{if(active)setSalesStatus(data)}).catch(()=>{});refresh();const timer=setInterval(refresh,60000);return()=>{active=false;clearInterval(timer)}; }, []);
    const [step, setStep] = useState(0), [title, setTitle] = useState(existing?.title || ''), [location, setLocation] = useState(existing?.location || locations[0] || ''), [selected, setSelected] = useState<string[]>(old.selected || existing?.products.map((l: Row) => l.item_id) || []), [qty, setQty] = useState<Record<string, string>>(old.quantities || Object.fromEntries((existing?.products || []).map((l: Row) => [l.item_id, String(l.quantity)]))), [params, setParams] = useState<Row>(old.parameters || defaults()), [calc, setCalc] = useState<Row | null>(old.calculation || null), [previous, setPrevious] = useState<Row | null>(null), [reason, setReason] = useState(old.manual_reason || ''), [due, setDue] = useState(old.due_on || existing?.due_on || ''), [available, setAvailable] = useState(old.available_on || existing?.available_on || ''), [assignee, setAssignee] = useState(old.assignee || existing?.assignee || ''), [groups, setGroups] = useState<Row[]>(old.groups || []), [confirmed, setConfirmed] = useState(false), [search, setSearch] = useState(''), [error, setError] = useState(''), [busy, setBusy] = useState(false), [norms, setNorms] = useState<Record<string, number>>({}), [pending, setPending] = useState<Row | null>(null), [dirty, setDirty] = useState(false);
    const [calcStale, setCalcStale] = useState(false), [periodOpen, setPeriodOpen] = useState(!old.calculation), [commentOpen, setCommentOpen] = useState(Boolean((old.comment || existing?.comment || '').trim())), [layoutEdit, setLayoutEdit] = useState<string | null>(null), [remarksOpen, setRemarksOpen] = useState(false);
    const chromeRef = useCutChrome(), stepsRef = useRef<HTMLElement>(null), calcSeen = useRef(calc);
    calcSeen.current = calc;
    function clearCalc() { if (calcSeen.current) setCalcStale(true); setCalc(null); }
    useEffect(() => {
        window.scrollTo({ top: 0, behavior: 'instant' });
        const nav = stepsRef.current;
        const current = nav?.querySelector<HTMLElement>('.is-current');
        if (nav && current) nav.scrollTo({ left: Math.max(0, current.offsetLeft - 16), behavior: 'auto' });
    }, [step]);
    const garments = items.filter(i => i.kind === 'garment').sort(compareVariants), materials = items.filter(i => i.kind === 'material');
    useEffect(() => { api('norms').then(r => setNorms(r.values)).catch(() => { }); }, []);
    useEffect(() => { if (!Object.keys(norms).length) return; setGroups(current => { let touched = false; const next = current.map(g => { if (g.norm) return g; const match = normFor(g.model, norms); if (!match) return g; touched = true; return { ...g, norm: match }; }); return touched ? next : current; }); }, [norms]);
    useEffect(() => { const f = (e: BeforeUnloadEvent) => { if (dirty) {
        e.preventDefault();
        e.returnValue = '';
    } }; window.addEventListener('beforeunload', f); return () => window.removeEventListener('beforeunload', f); }, [dirty]);
    useEffect(() => { const guard = (e: Event) => { if(dirty){e.preventDefault();confirmAction('Выйти из задания?','Есть несохранённые изменения.','Выйти без сохранения').then(ok=>{if(ok){setDirty(false);(e as CustomEvent).detail?.retry?.()}})} }; window.addEventListener('yuji:before-navigation', guard); return () => window.removeEventListener('yuji:before-navigation', guard); }, [dirty]);
    const byId = (id: string) => items.find(i => i.id === id)!;
    const materialAvailable=(id:string)=>{const b=byId(id)?.balances?.find((b:Row)=>b.location===location);return b?.known?Number(b.free):null};
    const taskModels = [...new Set(selected.map(id => byId(id)?.model).filter(Boolean))];
    const catalogModels = new Set(garments.map(i => i.model)).size;
    const orderedIds=(ids:string[])=>[...ids].sort((a,b)=>compareVariants(byId(a),byId(b)));
    const productFilters=useProductFilters(garments,location);
    const [comment,setComment]=useState(old.comment||existing?.comment||'');
    const [allowPurchase,setAllowPurchase]=useState(old.allow_purchase===true);
    const [manualQty,setManualQty]=useState<string[]>(old.manual_quantities||Object.entries(old.quantities||Object.fromEntries((existing?.products||[]).map((l:Row)=>[l.item_id,l.quantity]))).filter(([,q])=>String(q).trim()!=='').map(([id])=>id));
    const models = [...new Set(productFilters.shown.map(i=>i.model))];
    const recoveryKey=useRef('');
    const [recovery,setRecovery]=useState<Row|null>(null);
    useEffect(()=>{let active=true;api('me').then(user=>{if(!active)return;const key='yuji.cut.recovery.'+user.id+'.'+(existing?.id||'new');recoveryKey.current=key;try{const raw=localStorage.getItem(key);if(raw){const data=JSON.parse(raw);if(data.schema===1&&data.baseVersion===(existing?.version||0))setRecovery(data)}}catch{}}).catch(()=>{});return()=>{active=false}},[]);
    useEffect(()=>{if(!dirty||!recoveryKey.current)return;const timer=setTimeout(()=>{try{localStorage.setItem(recoveryKey.current,JSON.stringify({schema:1,baseVersion:existing?.version||0,title,location,selected,qty,params,calc,reason,due,available,assignee,groups,comment,allowPurchase,manualQty,at:Date.now()}))}catch{}},500);return()=>clearTimeout(timer)},[dirty,title,location,selected,qty,params,calc,reason,due,available,assignee,groups,comment,allowPurchase,manualQty]);
    function clearRecovery(){try{if(recoveryKey.current)localStorage.removeItem(recoveryKey.current)}catch{}setRecovery(null)}
    function restoreRecovery(){if(!recovery)return;setTitle(recovery.title);setLocation(recovery.location);setSelected(recovery.selected.filter((id:string)=>items.some(i=>i.id===id)));setQty(recovery.qty);setParams(recovery.params);clearCalc();setReason(recovery.reason);setDue(recovery.due);setAvailable(recovery.available);setAssignee(recovery.assignee);setGroups(recovery.groups);setComment(recovery.comment);if(String(recovery.comment||'').trim())setCommentOpen(true);setAllowPurchase(recovery.allowPurchase);setManualQty(recovery.manualQty);setRecovery(null);changed()}
    function changed() { setDirty(true); setConfirmed(false); setPending(null); }
    function toggle(id: string) { changed(); setSelected(selected.includes(id) ? selected.filter(x => x !== id) : [...selected, id]); clearCalc(); setGroups([]); }
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
        setCalcStale(false);
        setPeriodOpen(false);
        setQty(q => Object.fromEntries(selected.map(id => {
            const row = result.rows.find((r: Row) => r.item_id === id);
            if (manualQty.includes(id) || row?.recommendation == null) return [id, q[id] ?? ''];
            return [id, String(row.recommendation)];
        })));
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
    function addGroup() { const id = crypto.randomUUID(); const model = byId(selected[0])?.model || ''; changed(); setGroups([...groups, { id, model, ratio: '', material: '', quantity: '', products: {}, norm: normFor(model, norms), preliminary: true }]); setLayoutEdit(id); }
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
            const payload = { new_id: draftId, ...(existing ? { id: existing.id, version: existing.version } : {}), title: title || 'Задание на крой', location, products, materials: materialLines, cutting: { schema: 1, allow_purchase: allowPurchase, comment, selected, quantities: qty, manual_quantities: manualQty, parameters: params, calculation: calc, groups, manual_reason: reason, due_on: due, available_on: available, assignee, technology_confirmed: confirmed, excluded: selected.filter(id => !(productTotals[id] > 0)) } };
            const request = pending || { operation_id: crypto.randomUUID(), command: issue ? 'task.issue' : 'task.save', payload, reason: issue ? 'Выдача проверенного задания' : 'Сохранение черновика' };
            setPending(request);
            await api('commands', request);
            clearRecovery();
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
    const blocked = step === 1 && !hasNeed && calc ? 'Укажи количество хотя бы для одного товара.' : step === 2 && !completeGroups ? 'Заполни ткань, расход и план выпуска каждого кроя.' : step === 3 && !canIssue ? 'Проверь исполнителя, сроки и отклонения.' : '';
    const remarks = [...new Set([
        sourceRemark('Ozon', salesStatus.platforms?.ozon, params),
        sourceRemark('Wildberries', salesStatus.platforms?.wb, params),
        ...(calc?.warnings || []),
        calc && calc.rows.some((r: Row) => r.stock_assumed_zero) ? 'Свой остаток не внесён у части позиций. Для рекомендации он равен 0. Склад не изменён.' : '',
        previous && calc && previous.fingerprint !== calc.fingerprint ? 'Исходные данные изменились. Ручные количества сохранены.' : '',
    ].filter(Boolean))] as string[];
    const gapsAreRecent = params.start === shift(-30) && params.end === shift(-1);
    return <div className="cutting cut-editor">
  <div className="cut-chrome no-print" ref={chromeRef}>
    <div className="cut-chrome-bar">
      <button className="secondary cut-back" disabled={busy} onClick={closeEditor}>К заданиям</button>
      <span className="cut-context">{existing?.number || 'Новое задание'}</span>
      <div className="cut-chrome-actions"><button className="secondary" disabled={busy || !!pending || !selected.length} onClick={() => save(false)}>Черновик</button>{step < 3 ? <button disabled={busy || step === 0 && !selected.length || step === 1 && (!calc || !hasNeed) || step === 2 && !completeGroups} onClick={() => setStep(step + 1)}>Далее</button> : <button disabled={busy || !confirmed || !canIssue || !!pending} onClick={() => save(true)}>{busy ? 'Сохраняю…' : 'Выдать'}</button>}</div>
    </div>
    <div className="cut-chrome-nav">
      <nav className="cut-steps" aria-label="Подготовка задания" ref={stepsRef}>{stepGuide.map((item, index) => <button className={step === index ? 'is-current' : ''} key={item.label} aria-current={step === index ? 'step' : undefined} disabled={index===1&&!selected.length || index>1&&!calc || busy} onClick={() => setStep(index)}><span>{item.label}</span></button>)}</nav>
      <HelpTip label={'Пояснение: ' + stepGuide[step].label}>{stepGuide[step].help}</HelpTip>
    </div>
    {blocked ? <p className="cut-hint cut-chrome-hint">{blocked}</p> : null}
  </div>
  {recovery&&<CutNotice>Есть незавершённое редактирование на этом устройстве.<div className="cut-button-row"><button onClick={restoreRecovery}>Восстановить</button><button className="secondary" onClick={clearRecovery}>Не восстанавливать</button></div></CutNotice>}
  {error && <CutNotice error>{error}{pending && <div className="cut-button-row"><button disabled={busy} onClick={() => save(pending.command === 'task.issue')}>Повторить запрос</button><button className="secondary" onClick={() => { setPending(null); setError(''); }}>Вернуться к редактированию</button></div>}</CutNotice>}
  <fieldset disabled={busy || !!pending} className="cut-fields">

   {step === 0 && <>
    <section className="panel cut-panel"><div className="cut-section-heading cut-section-heading-slim"><span className="cut-counter">Выбрано {selected.length}</span></div>
     <ProductFilters filters={productFilters} categoryLabel="Категории товаров для кроя" searchLabel="Найти товар для кроя"/>
     <div className="cut-picker">{models.map(model => { const variants = productFilters.shown.filter(i => i.model === model), all = variants.every(i => selected.includes(i.id)), count = variants.filter(i => selected.includes(i.id)).length; return <details key={model}><summary><Photo item={variants.find(i=>photoCandidates(i).length)||variants[0]}/><strong>{model}</strong><StockSummary {...modelStock(variants,location)} className="cut-picker-stock"/><span className={'cut-model-count ' + (count ? 'has-selection' : '')}>выбрано {count} из {variants.length}</span>{calc&&<small className="cut-sold">Продано за период: {(() => { const values = variants.map(i => { const row = calc.rows.find((r:Row)=>r.item_id===i.id); return row?.net ?? row?.net_known ?? null; }); return values.every(v => v == null) ? '—' : n(values.reduce((sum, v) => sum + (v || 0), 0)); })()}</small>}<Icon name="down" size={16}/></summary><div className="cut-picker-content"><button className="text-button" onClick={() => { changed(); setSelected([...selected.filter(id => !variants.some(i => i.id === id)), ...(all ? [] : variants.map(i => i.id))]); clearCalc(); setGroups([]); }}>{all ? 'Снять выбор' : 'Выбрать все варианты'}</button><div className="cut-variant-options">{variants.map(i => <label className="cut-check" key={i.id}><input aria-label={(i.color || i.name) + ' · ' + (i.size || 'Без размера')} type="checkbox" checked={selected.includes(i.id)} onChange={() => toggle(i.id)}/><span>{i.color || i.name}<span className="cut-size">{i.size || 'Без размера'}</span></span></label>)}</div></div></details>; })}{!models.length && <div className="cut-inline-empty">Модели не найдены. Попробуй другое название.</div>}</div>
    </section>
   </>}
   {step === 1 && <div className="cut-step">
     <div className="cut-period">
      <div className="cut-period-line">
       <button type="button" className="cut-period-toggle" aria-expanded={periodOpen} onClick={() => setPeriodOpen(value => !value)}><span>Продажи {dateText(params.start)} — {dateText(params.end)} · запас {params.horizon} дн. · покрытие к {dateText(params.target_date)} · ФБО {params.include_fbo === true ? 'включено' : 'выключено'}</span>{calcStale && !calc && <span className="cut-period-stale">Расчёт сброшен. Нужен новый расчёт.</span>}</button>
       {remarks.length > 0 && <button type="button" className="secondary" onClick={() => setRemarksOpen(true)}>{words(remarks.length, 'замечание', 'замечания', 'замечаний')}</button>}
       <button disabled={!selected.length} onClick={() => calculate(!!calc)}>{busy ? syncMessage || 'Сверяем…' : calc ? 'Обновить данные' : 'Рассчитать потребность'}</button>
       {!selected.length && <span className="cut-hint">Сначала выбери товары выше.</span>}
      </div>
      {periodOpen && <div className="cut-period-edit"><div className="cut-form-grid cut-period-grid"><label>Продажи с<DatePicker label="Продажи с" value={params.start} onChange={value => { setParams({ ...params, report_ids:{}, start: value }); clearCalc(); changed(); }}/></label><label>По<DatePicker label="Продажи по" max={shift(-1)} value={params.end} onChange={value => { setParams({ ...params, report_ids:{}, end: value }); clearCalc(); changed(); }}/></label><label>Запас на дней<input type="number" min="1" max="365" value={params.horizon} onChange={e => { setParams({ ...params, horizon: Number(e.target.value) }); clearCalc(); changed(); }}/></label><label>Покрытие к дате<DatePicker label="Покрытие к дате" value={params.target_date} onChange={value => { setParams({ ...params, target_date: value }); clearCalc(); changed(); }}/></label></div><label className="cut-check"><input type="checkbox" checked={params.include_fbo===true} onChange={e=>{setParams({...params,include_fbo:e.target.checked});clearCalc();changed()}}/><span>Учитывать доступные остатки ФБО. При отключении расчёт не вычитает запасы площадок.</span></label></div>}
     </div>
     {calc && <div className="cut-result-stack"><div className="cut-result-heading"><span className="cut-hint">Обновлено {new Date(calc.calculated_at).toLocaleString('ru-RU', { timeZone: 'Europe/Moscow' })}</span><button className="secondary" onClick={applyRecommendations}>Применить рекомендации</button></div>
      <DemandTable rows={calc.rows} items={items} qty={qty} previous={previous} targetDate={params.target_date} uncertain={params.include_uncertain || []} onQty={(id, value) => { setManualQty(ids => [...new Set([...ids, id])]); setQty({ ...qty, [id]: value }); setGroups([]); changed(); }} onUncertain={(id, checked) => { const current = params.include_uncertain || []; setParams({ ...params, include_uncertain: checked ? [...current, id] : current.filter((item: string) => item !== id) }); clearCalc(); changed(); }}/>
     </div>}
     {remarksOpen && <DialogFrame title="Замечания к расчёту" close={() => setRemarksOpen(false)} className="cut-remark-dialog"><ul className="cut-remark-list">{remarks.map(text => <li key={text}>{text}</li>)}</ul>{calc && onPulse && <div className="cut-remark-actions">{!gapsAreRecent && <p className="cut-hint">В этом задании другой период. Список пробелов показывает последние 30 дней.</p>}<button type="button" className="secondary" onClick={() => { setRemarksOpen(false); onPulse(); }}>Все пробелы</button></div>}</DialogFrame>}
    </div>}
   {step === 2 && <div className="cut-step">
    <LayoutBuilder api={api} selected={orderedIds(selected).map(byId).filter(i => Number(qty[i.id]) > 0)} qty={qty} groups={groups} materials={materials} norms={norms} location={location} pinnedOpen={!groups.length} add={g => { changed(); setGroups([...groups.filter(x=>!g.some(y=>y.model===x.model)), ...g]); }}/>

    {groups.map((g, index) => { const material = byId(g.material); const products = orderedIds(selected).map(byId).filter(i => i.model === g.model && Number(qty[i.id]) > 0); const chips = Object.entries(g.products || {}).filter(([, q]) => Number(q) > 0).sort(([a],[b]) => compareVariants(byId(a), byId(b))); return <section className={'cut-layout ' + (layoutEdit === g.id ? 'is-open' : 'is-compact')} key={g.id}><div className="cut-layout-summary"><strong>{g.model || 'Модель не выбрана'}</strong><span>{material?.name || 'Ткань не выбрана'}</span><span>{g.quantity ? n(g.quantity) + ' ' + unitLabel(material) : 'Расход не указан'}</span>{g.ratio && <span className="cut-ratio">{g.ratio}</span>}<button className="secondary" onClick={() => setLayoutEdit(layoutEdit === g.id ? null : g.id)}>{layoutEdit === g.id ? 'Свернуть' : 'Изменить'}</button><button className="text-button" onClick={() => { changed(); setGroups(groups.filter((_, i) => i !== index)); }}>Убрать крой</button></div>{material?.unit === 'meter' && <p className="cut-meter-warning">Ткань в каталоге в метрах, норму в метры не переводим: длины рулона нет.</p>}<div className="cut-size-output">{chips.length ? chips.map(([id, q]) => <span key={id}>{byId(id).color || byId(id).name}<span className="cut-size">{byId(id).size}</span><strong>{n(q)}</strong></span>) : <span className="cut-hint">Выпуск не заполнен</span>}</div>{layoutEdit === g.id && <div className="cut-layout-edit"><div className="cut-form-grid"><label>Модель в задании<Choice label="Модель в задании" value={g.model} onChange={e => { const model = e.target.value; changed(); setGroups(groups.map((x, i) => i === index ? { ...x, model, products: {}, norm: normFor(model, norms) } : x)); }}>{taskModels.map(m => <option key={m}>{m}</option>)}</Choice></label><label>Выход с рулона<Choice label="Выход с рулона" value={g.norm || ''} onChange={e => updateGroup(index, 'norm', e.target.value)}><option value="">Выбери норму Жени</option>{Object.entries(norms).map(([name, pieces]) => <option key={name} value={name}>{name} · {pieces} шт. с рулона</option>)}</Choice></label><label className="cut-span">Доля размеров<input aria-label="Доля размеров" placeholder="Например, 50% S · 50% M" value={g.ratio} onChange={e => updateGroup(index, 'ratio', e.target.value)}/></label><label>Ткань<Choice label="Ткань" value={g.material} onChange={e => updateGroup(index, 'material', e.target.value)}><option value="">Выбери материал</option>{materials.map(m => <option key={m.id} value={m.id}>{m.name} · {unitLabel(m)}</option>)}</Choice></label><label>Расход{g.planned_rolls?' на '+g.planned_rolls+' рул.':''}{g.material ? ' · ' + unitLabel(byId(g.material)) : ''}<input aria-label="Расход" type="number" min="0" step={byId(g.material)?.unit === 'roll' ? '1' : '0.001'} value={g.quantity} onChange={e => updateGroup(index, 'quantity', e.target.value)}/></label></div><p className="cut-hint">{g.norm && norms[g.norm] ? norms[g.norm] + ' шт. с целого рулона. Это не метры ткани и не доля размеров.' : 'Норма Жени — штуки с целого рулона. К ткани и размерам она не привязана.'}</p><div className="cut-subsection"><h4>Плановый выпуск</h4><ReleaseMatrix products={products} values={g.products || {}} onChange={(id, value) => updateGroup(index, 'products', { ...g.products, [id]: value })}/></div></div>}</section>; })}
    {!groups.length && <div className="cut-inline-empty">Подбери раскладки автоматически или добавь ручной крой.</div>}
    <div className="cut-button-row"><button className="secondary" onClick={addGroup}>+ Ручной крой</button></div>
   </div>}
   {step === 3 && <>
    <CutSection title="Исполнение задания"><div className="cut-form-grid cut-form-grid--three"><label>Исполнитель<Choice label="Исполнитель" value={assignee} onChange={e => { setAssignee(e.target.value); changed(); }}><option value="">Выбери сотрудника</option>{users.map(u => <option key={u.id} value={u.id}>{u.name}</option>)}</Choice></label><label>Срок кроя<DatePicker label="Срок кроя" value={due} onChange={value => { setDue(value); changed(); }}/></label><label>Ожидается к продаже<DatePicker label="Ожидается к продаже" min={due || undefined} value={available} onChange={value => { setAvailable(value); changed(); }}/><small className="cut-hint">Не снимает заморозку автоматически.</small></label></div></CutSection>
    <CutSection title="Проверка выпуска"><div className="table-wrap"><table className="cut-data-table"><thead><tr><th>Товар</th><th>Рекомендация</th><th>Потребность</th><th>План выпуска</th><th>Отклонение</th></tr></thead><tbody>{orderedIds(selected).filter(id => Number(qty[id]) > 0).map(id => { const q = Number(qty[id]), actual = productTotals[id] || 0, dev = (actual / q - 1) * 100; return <tr key={id}><td><ProductCell item={byId(id)}/></td><td data-label="Рекомендация">{n(calc?.rows.find((r:Row)=>r.item_id===id)?.recommendation)}</td><td data-label="Потребность">{n(q)}</td><td data-label="План выпуска"><strong>{n(actual)}</strong></td><td data-label="Отклонение"><span className={actual && Math.abs(dev) > 20 ? 'cut-missing' : ''}>{actual ? (dev > 0 ? '+' : '') + dev.toFixed(1) + '%' : 'Не включён'}</span></td></tr>; })}</tbody></table></div>{outsideTolerance && <CutNotice>Есть отклонения больше ±20%. Скорректируй потребность или раскладки перед выдачей.</CutNotice>}</CutSection>
    <CutSection title="Ткань и докупка"><label className="cut-check"><input type="checkbox" checked={allowPurchase} onChange={e=>{setAllowPurchase(e.target.checked);changed()}}/><span>Выдать с ожиданием ткани. Резервируется только доступное количество.</span></label><div className="cut-material-list">{Object.entries(materialTotals).map(([id, q]) => <div key={id}><span>{byId(id).name}</span><strong>{n(q)} <small>{unitLabel(byId(id))}</small><small>Доступно: {n(materialAvailable(id))} · Докупить: {n(Math.max(0,q-(materialAvailable(id)||0)))}</small></strong></div>)}</div><label className="cut-check cut-confirmation"><input type="checkbox" checked={confirmed} onChange={e => { setConfirmed(e.target.checked); setDirty(true); setPending(null); }}/><span>Проверены количества, совместимость ткани и применимость раскладок</span></label></CutSection>
   </>}
   <details className="cut-comment" open={commentOpen} onToggle={event => setCommentOpen(event.currentTarget.open)}><summary>Комментарий</summary><label><span className="sr-only">Комментарий к заданию</span><textarea aria-label="Комментарий к заданию" maxLength={4000} placeholder="Детали и пожелания к заданию" value={comment} onChange={e=>{setComment(e.target.value);changed()}}/></label></details>
  </fieldset>
 </div>;
}
function LayoutBuilder({ api, selected, qty, groups, materials, norms, location, add, pinnedOpen }: {
    api: Api;
    selected: Row[];
    qty: Record<string, string>;
    groups: Row[];
    materials: Row[];
    norms: Record<string, number>;
    location: string;
    add: (groups: Row[]) => void;
    pinnedOpen: boolean;
}) {
    const models = [...new Set(selected.map(i => i.model))];
    const [purchase,setPurchase]=useState(true), [open, setOpen] = useState(pinnedOpen);
    useEffect(() => { if (pinnedOpen) setOpen(true); }, [pinnedOpen]);
    const [lengths,setLengths]=useState<Record<string,string>>({});
    const [model, setModel] = useState(models[0] || ''), [norm, setNorm] = useState(''), [bindings, setBindings] = useState<Record<string, string>>({}), [ratios, setRatios] = useState(''), [result, setResult] = useState<Row | null>(null), [error, setError] = useState(''), [busy, setBusy] = useState(false);
    const variants = selected.filter(i => i.model === model), colors = [...new Set(variants.map(i => i.color))], sizes = [...new Set(variants.map(i => i.size))].sort(compareSizes);
    const [templateOwner,setTemplateOwner]=useState(''),[templateMessage,setTemplateMessage]=useState('');
    useEffect(()=>{api('me').then(u=>setTemplateOwner(u.id)).catch(()=>{})},[]);
    function template(save:boolean){try{if(!templateOwner)return;const key='yuji.cut.layout.'+templateOwner+'.'+model;if(save){if(!norm||!ratios.trim()||colors.some(c=>!bindings[c]))throw Error('Сначала заполни норму, ткань и соотношения');localStorage.setItem(key,JSON.stringify({schema:1,norm,bindings,ratios,sizes,lengths}));setTemplateMessage('Настройки модели сохранены на этом устройстве')}else{const raw=localStorage.getItem(key);if(!raw)throw Error('Для этой модели пока нет сохранённых настроек');const value=JSON.parse(raw);if(value.schema!==1||JSON.stringify(value.sizes)!==JSON.stringify(sizes))throw Error('Состав размеров изменился. Укажи соотношения заново');setNorm(norms[value.norm]?value.norm:'');setBindings(Object.fromEntries(Object.entries(value.bindings).filter(([,id])=>materials.some(m=>m.id===id)).map(([color,id])=>[color,String(id)])));setLengths(value.lengths||{});setRatios(value.ratios);setTemplateMessage('Настройки восстановлены. Проверь ткань и соотношения перед подбором')}setError('');setResult(null)}catch(e){setError(errorText(e))}}
    const inputSignature = JSON.stringify({ ids: selected.map(i => i.id), qty, groups, model, norm, bindings, lengths, ratios, purchase });
    useEffect(() => { setResult(null); }, [inputSignature]);
    async function run() {
        setBusy(true);
        setError('');
        setResult(null);
        try {
            const patterns = ratios.split('\n').filter(x => x.trim()).map(line => { const values = line.split(':').map(Number); if (values.length !== sizes.length || values.some(v => !Number.isInteger(v) || v < 0))
                throw Error('Укажи целые доли через двоеточие для каждого размера'); return Object.fromEntries(sizes.map((s, i) => [s, values[i]])); });
            const needs = Object.fromEntries(colors.map(c => [c, Object.fromEntries(sizes.map(s => [s, variants.filter(i => i.color === c && i.size === s).reduce((sum, i) => sum + Number(qty[i.id]), 0)]))]));
            const available = Object.fromEntries(colors.map(c => {
                const m=materials.find(i=>i.id===bindings[c]), b=m?.balances.find((v:Row)=>v.location===location);
                if(!m)throw Error('Выбери ткань для цвета '+c);
                const length=m.unit==='roll'?1:Number(lengths[c]);
                if(!purchase&&(!b?.known||!(length>0)))throw Error('Укажи длину рулона и подтверди остаток ткани или включи подбор с докупкой');
                const used=groups.filter(g=>g.model!==model&&g.material===m.id).reduce((sum,g)=>sum+Number(g.quantity),0);
                return [c,length>0?Math.max(0,Math.floor((Number(b?.free||0)-used)/length)):0];
            }));
            if (new Set(Object.values(bindings)).size !== colors.length)
                throw Error('Для разных цветов выбери разные материалы');
            const r = await api('planning/grouped-layout', { model: norm, needs_by_color: needs, ratios: patterns, available_by_color: available, allow_purchase: purchase, item_ids:variants.map(i=>i.id), material_by_item:Object.fromEntries(variants.map(i=>[i.id,bindings[i.color]])) });
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
            groups.push({ id: crypto.randomUUID(), model, color, material: bindings[color], quantity: materials.find(m=>m.id===bindings[color])?.unit==='meter'?(Number(lengths[color])>0?String(Number((rolls*Number(lengths[color])).toFixed(3))):''):String(rolls), planned_rolls:rolls, meters_per_roll:Number(lengths[color])||null, ratio: Object.entries(result!.ratios[pattern]).filter(([, v]) => Number(v) > 0).map(([s, v]) => s + ': ' + v).join(' · '), norm, norm_value: result!.norm, norm_snapshot:Object.fromEntries(Object.entries(result!.norm_snapshot||{}).filter(([id])=>variants.some(i=>i.id===id&&i.color===color))), preliminary: true, products: Object.fromEntries(variants.filter(i => i.color === color).map(i => [i.id, String((output[i.size] || 0) * rolls)])) });
        }
    } add(groups); setResult(null); }
    const rollMaterials = materials.filter(m => ['roll','meter'].includes(m.unit));
    return <details className="cut-auto-layout" open={open} onToggle={event => setOpen(pinnedOpen ? true : event.currentTarget.open)}><summary onClick={event => { if (pinnedOpen) event.preventDefault(); }}><span><strong>Подобрать раскладки</strong><small>{rollMaterials.length ? 'По потребности, доступной ткани и нормам' : 'Добавь материал в каталог ткани.'}</small></span><Icon name="down" size={17}/></summary><div className="cut-auto-body"><label className="cut-check"><input type="checkbox" checked={purchase} onChange={e=>{setPurchase(e.target.checked);setResult(null)}}/><span>Полная потребность с докупкой</span></label>
  {!models.length&&<CutNotice>Вернись в «Потребность» и укажи количество к крою хотя бы для одной позиции.</CutNotice>}
  {!rollMaterials.length&&<CutNotice>Для подбора добавь материал в каталог ткани.</CutNotice>}
  {!!rollMaterials.length && models.length>0 && <>
  <div className="cut-form-grid"><label>Модель<Choice label="Модель для подбора" value={model} onChange={e => { setModel(e.target.value); setBindings({});setLengths({});setNorm(normFor(e.target.value,norms)); setResult(null); setRatios(''); }}>{models.map(m => <option key={m}>{m}</option>)}</Choice></label><label>Норма Жени<Choice label="Норма Жени" value={norm} onChange={e => { setNorm(e.target.value); setResult(null); }}><option value="">Сопоставь модель с нормой</option>{Object.entries(norms).map(([m, q]) => <option key={m} value={m}>{m} · {q} шт.</option>)}</Choice></label></div>
  <div className="cut-subsection"><h4>Ткань по цветам</h4><div className="cut-form-grid">{colors.map(c => <label key={c}>{c || 'Цвет не указан'}<Choice label={'Ткань · ' + (c || 'Цвет не указан')} value={bindings[c] || ''} onChange={e => { setBindings({ ...bindings, [c]: e.target.value }); setResult(null); }}><option value="">Выбери ткань</option>{rollMaterials.map(m => <option key={m.id} value={m.id}>{m.name} · {unitLabel(m)}</option>)}</Choice>{materials.find(m=>m.id===bindings[c])?.unit==='meter'&&<><span>Метров в рулоне</span><input type="number" min="0.001" step="0.001" value={lengths[c]||''} placeholder="Или укажи расход после подбора" onChange={e=>setLengths({...lengths,[c]:e.target.value})}/></>}</label>)}</div></div>
  <div className="cut-button-row"><button className="secondary" disabled={!templateOwner} onClick={()=>template(false)}>Восстановить настройки модели</button><button className="text-button" disabled={!templateOwner} onClick={()=>template(true)}>Запомнить настройки</button></div>{templateMessage&&<p className="cut-hint">{templateMessage}</p>}
  {norm && Object.values(bindings).some(Boolean) && <label>Допустимые соотношения: {sizes.join(' : ')}<textarea value={ratios} onChange={e => { setRatios(e.target.value); setResult(null); }} placeholder="Каждая раскладка с новой строки, например 1:1:0"/><span className="cut-hint">Укажи только допустимые для ткани соотношения. Норма даёт предварительный выход.</span></label>}
  {error && <CutNotice error>{error}</CutNotice>}
  <div className="cut-button-row"><button className="secondary" disabled={busy || !norm || !ratios} onClick={run}>{busy ? 'Подбираю…' : 'Подобрать'}</button></div>
  {result && <div className="cut-auto-result"><CutNotice>{result.message}{result.norm&&<div>Применённый выход: {result.norm} шт. с рулона</div>}</CutNotice>{result.result && <><div className="cut-output-grid">{Object.entries(result.result).map(([color, row]: [
        string,
        any
    ]) => <div key={color}><span className="cut-eyebrow">{color}</span><strong>{row.rolls} <small>рул.</small></strong>{row.purchase_rolls>0&&<p>{materials.find(m=>m.id===bindings[color])?.unit==='meter'&&!Number(lengths[color])?'Наличие в рулонах не проверено':'Докупить: '+row.purchase_rolls+' рул.'}</p>}<p>{Object.entries(row.output).sort(([a],[b])=>compareSizes(a,b)).map(([size, q]) => size + ' — ' + q + ' шт.').join(' · ')}</p></div>)}</div><div className="cut-button-row"><button onClick={accept}>Добавить в задание</button></div></>}</div>}
  </>}
 </div></details>;
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
    const [factGroup,setFactGroup]=useState('');
    const [receiptItem,setReceiptItem]=useState(''),[receiptQty,setReceiptQty]=useState('');
    const [mode, setMode] = useState(''), [error, setError] = useState(''), [busy, setBusy] = useState(false), [values, setValues] = useState<Record<string, string>>({}), [materials, setMaterials] = useState<Record<string, string>>({}), [reason, setReason] = useState(''), [confirmed, setConfirmed] = useState(false), [pending, setPending] = useState<Row | null>(null), [copied, setCopied] = useState(false);
    const get = (id: string) => items.find(i => i.id === id) || { name: id };
    const groupRemaining=(g:Row)=>{const facts=Object.entries(task.group_facts||{}).filter(([id,f]:[string,any])=>!(task.reversed_facts||[]).includes(id)&&f.group_id===g.id).map(([,f])=>f as Row);return {products:Object.fromEntries(Object.entries(g.products).map(([id,q])=>[id,Math.max(0,Number(q)-facts.reduce((sum,f)=>sum+f.products.filter((l:Row)=>l.item_id===id).reduce((n:number,l:Row)=>n+Number(l.quantity),0),0))])),quantity:Math.max(0,Number(g.quantity)-facts.reduce((sum,f)=>sum+f.materials.reduce((n:number,l:Row)=>n+Number(l.quantity),0),0))}};
    const active = ['approved', 'partial'].includes(task.status);
    const orderedLines=(lines:Row[])=>[...lines].sort((a,b)=>compareVariants(get(a.item_id),get(b.item_id)));
    const hasActions = canPlan && ['draft', 'approved', 'partial'].includes(task.status) || canAdjust && task.facts.some((f: string) => !task.reversed_facts?.includes(f));
    const operation = useRef<HTMLElement>(null);
    const chromeRef = useCutChrome();
    useEffect(() => { window.scrollTo({ top: 0, behavior: 'instant' }); }, []);
    useEffect(() => { if (mode)
        requestAnimationFrame(() => operation.current?.scrollIntoView({ block: 'start', behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' })); }, [mode]);
    function start(kind: string) { setFactGroup('');setMode(kind); setError(''); setReason(''); setPending(null); setConfirmed(false); setValues(Object.fromEntries((kind === 'revise' ? task.products : task.remaining_products).map((l: Row) => [l.item_id, kind === 'revise' || kind === 'confirm' ? String(l.quantity) : '']))); setMaterials(Object.fromEntries((kind === 'revise' ? task.materials : task.remaining_materials).map((l: Row) => [l.item_id, kind === 'revise' || kind === 'confirm' ? String(l.quantity) : '']))); }
    async function run() { setBusy(true); setError(''); try {
        if (!reason.trim())
            throw Error('Укажи основание');
        let command = 'task.' + mode;
        let payload: Row = { id: task.id, version: task.version };
        if (mode === 'confirm' || mode === 'revise') {
            if (mode === 'confirm' && !confirmed)
                throw Error('Подтверди фактические количества');
            payload = { ...payload, ...(mode==='confirm'&&factGroup?{group_id:factGroup}:{}), confirm_actual: true, products: Object.entries(values).filter(([, q]) => Number(q) > 0).map(([item_id, quantity]) => ({ item_id, quantity })), materials: Object.entries(materials).filter(([, q]) => Number(q) > 0).map(([item_id, quantity]) => ({ item_id, quantity })) };
        }
        if (mode === 'receive') {command='material.receive';payload={item_id:receiptItem,location:task.location,quantity:receiptQty};}
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
    const text = [task.number || task.id.slice(0, 8), 'Версия '+task.version, task.title, ...(task.awaiting_materials?['Ожидается ткань',...(task.purchase_materials||[]).map((l:Row)=>get(l.item_id).name+': докупить '+n(l.quantity)+' '+unitLabel(get(l.item_id)))]:[]), 'Срок кроя: ' + (task.due_on || 'не указан'), ...(task.cutting?.comment?['Комментарий: '+task.cutting.comment]:[]), ...(task.cutting?.layout_outdated ? [] : task.cutting?.groups || []).map((g: Row) => [g.model, g.ratio, get(g.material).name + ' · ' + g.quantity, Object.entries(g.products).sort(([a],[b])=>compareVariants(get(a),get(b))).map(([id, q]) => get(id).name + ': ' + q).join('\n')].join('\n')), ...(!task.cutting?.groups?.length || task.cutting?.layout_outdated ? orderedLines(task.products).map((l: Row) => get(l.item_id).name + ': ' + l.quantity) : [])].join('\n\n');
    const total = task.products.reduce((sum: number, l: Row) => sum + Number(l.quantity), 0), remaining = task.remaining_products.reduce((sum: number, l: Row) => sum + Number(l.quantity), 0);
    const restLabel = task.status === 'cancelled' ? 'Не выполнено' : 'Осталось';
    const caption = mode === 'confirm' ? 'Фактический выпуск' : mode === 'revise' ? 'Изменение плана' : mode === 'cancel' ? 'Отмена остатка задания' : 'Исправление последнего факта';
    async function copyTask() { try { await navigator.clipboard.writeText(text); setCopied(true); } catch { setError('Копирование недоступно в этом браузере'); } }
    return <div className="cutting cut-document">
  <div className="cut-chrome no-print" ref={chromeRef}><div className="cut-chrome-bar"><button className="secondary cut-back" onClick={close}>К заданиям</button><span className="cut-context">{task.number || task.id.slice(0, 8)}</span><div className="cut-chrome-actions"><button className="secondary cut-chrome-wide" onClick={() => window.print()}>Печать</button><button className="secondary cut-chrome-wide" onClick={copyTask}>{copied ? 'Скопировано' : 'Копировать'}</button>{canCut && active && <button onClick={() => start('confirm')}>Внести факт кроя</button>}{canPlan && task.status === 'draft' && <button className="secondary" onClick={edit}>Продолжить подготовку</button>}<span className={hasActions ? 'cut-doc-menu has-actions' : 'cut-doc-menu'}><ActionMenu><button className="cut-narrow-action" onClick={() => window.print()}>Печать</button><button className="cut-narrow-action" onClick={copyTask}>{copied ? 'Скопировано' : 'Копировать'}</button>{canPlan && active && <button onClick={() => start('revise')}>Изменить план</button>}{canPlan && ['draft', 'approved', 'partial'].includes(task.status) && <button onClick={() => start('cancel')}>Отменить остаток</button>}{canAdjust && task.facts.some((f: string) => !task.reversed_facts?.includes(f)) && <button onClick={() => start('reverse_fact')}>Исправить последний факт</button>}</ActionMenu></span></div></div></div>
  <section className="panel cut-hero"><div className="cut-hero-heading"><h2><span className="cut-eyebrow">{task.number || task.id.slice(0, 8)}</span>{task.title}</h2><span className={'badge cut-status status-' + task.status}>{states[task.status]}</span></div>
   <dl className="cut-document-meta"><div><dt>Срок кроя</dt><dd className={!task.due_on ? 'is-empty' : ''}>{dateText(task.due_on)}</dd></div><div><dt>Исполнитель</dt><dd className={!task.assignee ? 'is-empty' : ''}>{users.find(u => u.id === task.assignee)?.name || 'Не назначен'}</dd></div><div><dt>Ожидается к продаже</dt><dd className={!task.available_on ? 'is-empty' : ''}>{task.available_on ? dateText(task.available_on) : 'Не указана'}</dd></div></dl>
   <div className="cut-hero-footer"><span className="cut-hint">{task.created_at ? 'Создано ' + new Date(task.created_at).toLocaleDateString('ru-RU', { timeZone: 'Europe/Moscow' }) + ' · ' : ''}Версия {task.version}</span></div>
  </section>
  {task.awaiting_materials && active && <CutSection title="Ожидается ткань"><div className="cut-material-list">{(task.purchase_materials||[]).map((l:Row)=><div key={l.item_id}><span>{get(l.item_id).name}</span><strong>{n(l.quantity)} {unitLabel(get(l.item_id))}</strong></div>)}</div>{canAdjust&&<button className="secondary" onClick={()=>{setReceiptItem(task.purchase_materials?.[0]?.item_id||'');setReceiptQty('');start('receive')}}>Принять ткань</button>}{canPlan&&<button className="secondary" disabled={busy} onClick={async()=>{setBusy(true);try{await api('commands',{operation_id:crypto.randomUUID(),command:'task.reserve_materials',payload:{id:task.id,version:task.version},reason:'Закрепление поступившей ткани'});await refresh()}catch(e){setError(errorText(e))}finally{setBusy(false)}}}>Закрепить доступную ткань</button>}</CutSection>}
  {task.cutting?.layout_outdated && <CutNotice>План изменён. Прежние раскладки сохранены в истории; актуальные количества указаны ниже.</CutNotice>}
  {!task.cutting?.layout_outdated && task.cutting?.groups?.length > 0 && <CutSection title="Раскладки"><div className="cut-layouts-readonly">{task.cutting.groups.map((g: Row, index: number) => <div className="cut-layout cut-layout-readonly" key={g.id}><div className="cut-layout-heading"><div><span className="cut-eyebrow">Крой {String(index + 1).padStart(2, '0')}</span><h4>{g.model}</h4></div><span className="cut-ratio">{g.ratio}</span></div><div className="cut-material-line"><span>{get(g.material).name}</span><strong>{n(g.quantity)} {unitLabel(get(g.material))}</strong></div><div className="cut-size-output">{Object.entries(g.products).sort(([a],[b])=>compareVariants(get(a),get(b))).filter(([, q]) => Number(q) > 0).map(([id, q]) => <span key={id}>{get(id).color || get(id).name}<span className="cut-size">{get(id).size}</span><strong>{n(q)}</strong></span>)}</div></div>)}</div></CutSection>}
  <CutSection title="План выпуска">
   {task.products.length > 0 ? <><div className="cut-totals"><div><span>Запланировано, шт.</span><strong>{n(total)}</strong></div><div className="is-done"><span>Выполнено</span><strong>{n(total - remaining)}</strong></div><div className="is-left"><span>{restLabel}</span><strong>{n(remaining)}</strong></div></div><div className="table-wrap"><table className="cut-data-table"><thead><tr><th>Изделие</th><th>План</th><th>Выполнено</th><th>{restLabel}</th></tr></thead><tbody>{orderedLines(task.products).map((l: Row) => { const left = Number(task.remaining_products.find((r: Row) => r.item_id === l.item_id)?.quantity || 0); return <tr key={l.item_id}><td><ProductCell item={get(l.item_id)}/></td><td data-label="План">{n(l.quantity)}</td><td className="cut-done" data-label="Выполнено">{n(Number(l.quantity) - left)}</td><td className="cut-left" data-label={restLabel}><strong>{n(left)}</strong></td></tr>; })}</tbody></table></div></> : <div className="cut-inline-empty">В черновике пока нет товаров. Продолжи подготовку, чтобы добавить позиции.</div>}
  </CutSection>
  {task.cutting?.comment&&<CutSection title="Комментарий"><p className="cut-comment-text">{task.cutting.comment}</p></CutSection>}
  {mode && <section className="panel cut-panel cut-operation no-print" ref={operation}><div className="cut-section-heading"><div className="help-heading"><h3>{mode==='receive'?'Приёмка ткани':caption}</h3><HelpTip label="Пояснение к операции">{mode === 'receive' ? 'Внеси фактически поступившее количество. После приёмки закрепи ткань за заданием.' : mode === 'confirm' ? 'Выбери выполненные позиции и проверь фактический расход ткани.' : mode === 'revise' ? 'Новые общие количества включают уже выполненное. Факт сохранится в истории.' : mode === 'cancel' ? 'Проведённый выпуск сохранится, неиспользованная ткань освободится.' : 'Последний факт будет сторнирован с сохранением истории.'}</HelpTip></div><button className="cut-icon-button" aria-label="Закрыть операцию" disabled={busy} onClick={() => { setMode(''); setPending(null); }}><Icon name="close" size={18}/></button></div>
   <div className="cut-section-body">{error && <CutNotice error>{error}</CutNotice>}<fieldset disabled={busy || !!pending} className="cut-fields">
    {mode==='receive'&&<div className="cut-form-grid"><label>Ткань<Choice label="Ткань для приёмки" value={receiptItem} onChange={e=>setReceiptItem(e.target.value)}>{items.filter(i=>i.kind==='material').map(i=><option key={i.id} value={i.id}>{i.name}</option>)}</Choice></label><label>Поступило · {unitLabel(get(receiptItem))}<input type="number" min="0" step={get(receiptItem).unit==='roll'?'1':'0.001'} value={receiptQty} onChange={e=>setReceiptQty(e.target.value)}/></label></div>}
    {mode === 'confirm' && !task.cutting?.layout_outdated && task.cutting?.groups?.length > 0 && <div className="cut-subsection"><h4>Заполнить по раскладке</h4><div className="cut-button-row">{task.cutting.groups.map((g: Row, index: number) => <button className="secondary" key={g.id} onClick={() => { setFactGroup(g.id||'');setValues(Object.fromEntries(task.remaining_products.map((l: Row) => [l.item_id, String(Math.min(Number(l.quantity), Number(groupRemaining(g).products[l.item_id] || 0)))]))); setMaterials(Object.fromEntries(task.remaining_materials.map((l: Row) => [l.item_id, l.item_id === g.material ? String(Math.min(Number(l.quantity), groupRemaining(g).quantity)) : '']))); setConfirmed(false); }}>Подставить крой {index + 1}</button>)}</div></div>}
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
