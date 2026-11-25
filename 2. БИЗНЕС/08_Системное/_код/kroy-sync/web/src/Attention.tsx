import React, { useState } from 'react';

type Row = Record<string, any>;
const tones: Record<string, string> = { ok: 'В порядке', waiting: 'Ожидание', failure: 'Сбой', review: 'Разобрать' };

function when(value?: string) {
    if (!value) return '';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '';
    return date.toLocaleString('ru-RU', { timeZone: 'Europe/Moscow' });
}

export function AttentionPulse({ pulse, canPlan, canMap, onMap, requestSync }: {
    pulse: Row | null;
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
    if (!pulse) return <section className="panel"><h3>Пульс не прочитан</h3><p className="muted">Кнопка «Обновить» только перечитывает этот экран. На площадки она не ходит.</p></section>;
    const salesWaiting = (pulse.sources || []).some((source: Row) => (source.id === 'ozon-sales' || source.id === 'wb-sales') && source.tone === 'waiting');
    return <div className="pulse">
        <div className="pulse-head">
            <div>
                <p>{pulse.question}</p>
                <strong>{pulse.period?.text}</strong>
                {pulse.badge === 0 && !salesWaiting && <p className="muted">Загрузка дошла до вчера. Тем для разбора нет.</p>}
            </div>
            {canPlan && <button disabled={busy} onClick={() => void ask()}>{busy ? 'Ставлю…' : 'Запросить сверку'}</button>}
        </div>
        <p className="muted">Кнопка «Обновить» в шапке только перечитывает этот экран. На площадки она не ходит.</p>
        {note && <p className="success" role="status">{note}</p>}
        {error && <div className="error" role="alert">{error}</div>}
        <section className="pulse-sources" aria-label="Источники">{(pulse.sources || []).map((source: Row) => <div className="pulse-source" key={source.id}><div><strong>{source.title}</strong><p>{source.summary}</p>{source.at && <small>{when(source.at)}</small>}</div><b className={'pulse-tone tone-' + source.tone}>{tones[source.tone] || source.tone}</b></div>)}</section>
        {(pulse.topics || []).map((topic: Row) => <details className="panel pulse-topic" key={topic.id}><summary><span>{topic.title}</span><b className="pulse-tone tone-review">Разобрать</b></summary><p>{topic.effect}</p>{(topic.rows || []).map((row: Row, index: number) => <div className="pulse-row" key={row.item_id || row.offer_id || index}><span><b>{row.name || row.offer_id}</b>{row.offer_id && row.name ? <small>{row.offer_id}</small> : null}</span><span>{row.units != null ? row.units + ' шт.' : ''}</span>{topic.action === 'map' && canMap && row.item_id && <button type="button" className="secondary" onClick={() => onMap(row.item_id)}>Сопоставить</button>}</div>)}{topic.more > 0 && <p className="muted">Ещё {topic.more}. Показаны первые строки.</p>}</details>)}
        {(pulse.limits || []).length > 0 && <section className="panel pulse-limits"><h3>Ограничения</h3>{pulse.limits.map((limit: Row) => <p key={limit.id}><b>{limit.title}.</b> {limit.summary}</p>)}</section>}
    </div>;
}
