import React, { useState } from 'react';
import { Icon } from './UI';

type Row = Record<string, any>;
const tones: Record<string, string> = { ok: 'В порядке', waiting: 'Ожидание', failure: 'Сбой', review: 'Разобрать' };

function when(value?: string) {
    if (!value) return '';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '';
    return date.toLocaleString('ru-RU', { timeZone: 'Europe/Moscow' });
}

function lead(pulse: Row) {
    const sources: Row[] = pulse.sources || [];
    const topics: Row[] = pulse.topics || [];
    const failed = sources.filter(source => source.tone === 'failure');
    const salesWaiting = sources.some(source => (source.id === 'ozon-sales' || source.id === 'wb-sales') && source.tone === 'waiting');
    if (failed.length) return 'Есть сбой источника. Пока он не пройдёт, продажам за период лучше не верить.';
    if (salesWaiting) return 'Продажи за период ещё дочитываются. Это не сбой.';
    if (topics.length) return 'Продажи за период дошли. Ниже то, что в расчёт не вошло.';
    return 'Продажи за период дошли. Разбирать нечего.';
}

function amount(row: Row) {
    if (row.detail) return row.detail;
    return row.units != null ? row.units + ' шт.' : '';
}

function Topic({ topic, canMap, onMap }: { topic: Row; canMap: boolean; onMap: (itemId: string) => void }) {
    const rows: Row[] = topic.rows || [];
    const lines = <>
        {rows.map((row, index) => <div className="pulse-row" key={row.item_id || row.offer_id || index}><span><b>{row.name || row.offer_id}</b>{row.offer_id && row.name ? <small>{row.offer_id}</small> : null}</span><span className="pulse-units">{amount(row)}</span>{topic.action === 'map' && canMap && row.item_id && <button type="button" className="secondary" onClick={() => onMap(row.item_id)}>Сопоставить</button>}</div>)}
        {topic.more > 0 && <p className="muted">Ещё {topic.more}. Показаны первые строки.</p>}
    </>;
    if (!rows.length) return <div className="pulse-topic is-plain"><strong>{topic.title}</strong><p>{topic.effect}</p></div>;
    return <details className="pulse-topic" open={topic.id === 'wb-lines'}><summary><span><strong>{topic.title}</strong><small>{topic.effect}</small></span><Icon name="down" size={16}/></summary>{lines}</details>;
}

export function AttentionPulse({ pulse, opening, canPlan, canMap, onMap, requestSync }: {
    pulse: Row | null;
    opening?: boolean;
    canPlan: boolean;
    canMap: boolean;
    onMap: (itemId: string) => void;
    requestSync: () => Promise<Row>;
}) {
    const [busy, setBusy] = useState(false), [note, setNote] = useState(''), [error, setError] = useState('');
    async function ask() {
        setBusy(true); setError('');
        try {
            const job = await requestSync();
            setNote(job?.status === 'done' ? 'Такая сверка только что выполнена.' : 'Сверка поставлена. Повтор не создаёт вторую задачу.');
        } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
        finally { setBusy(false); }
    }
    if (!pulse) return <section className="panel"><h3>{opening ? 'Открываю раздел…' : 'Пульс не прочитан'}</h3><p className="muted">Кнопка «Обновить» только перечитывает этот экран. На площадки она не ходит.</p></section>;
    const topics: Row[] = pulse.topics || [];
    const limits: Row[] = pulse.limits || [];
    return <div className="pulse">
        <section className="panel pulse-lead">
            <div>
                <h3>{lead(pulse)}</h3>
                <p>{pulse.period?.text}</p>
            </div>
            {canPlan && <div className="pulse-sync"><button className="secondary" disabled={busy} onClick={() => void ask()}>{busy ? 'Ставлю…' : 'Запросить сверку'}</button><small>«Обновить» в шапке только перечитывает экран. Сверка ходит на площадки.</small></div>}
        </section>
        {note && <p className="success" role="status">{note}</p>}
        {error && <div className="error" role="alert">{error}</div>}
        {topics.length > 0 && <section className="panel" aria-label="Разобрать"><h3>Разобрать</h3><p>Это не вошло в рекомендацию и не заменено нулём.</p>{topics.map(topic => <Topic key={topic.id} topic={topic} canMap={canMap} onMap={onMap}/>)}</section>}
        <section className="panel" aria-label="Источники"><h3>Источники</h3><div className="pulse-sources">{(pulse.sources || []).map((source: Row) => <div className="pulse-source" key={source.id}><div className="pulse-source-top"><strong>{source.title}</strong><span className={'pulse-pill tone-' + source.tone}>{tones[source.tone] || source.tone}</span></div><p>{source.summary}</p>{source.at && <small>{when(source.at)}</small>}</div>)}</div></section>
        {limits.length > 0 && <section className="panel pulse-limits" aria-label="Ограничения"><h3>Ограничения</h3><p>Известные правила чтения. Это не новые сбои.</p>{limits.map(limit => <div className="pulse-limit" key={limit.id}><strong>{limit.title}</strong><p>{limit.summary}</p></div>)}</section>}
    </div>;
}
