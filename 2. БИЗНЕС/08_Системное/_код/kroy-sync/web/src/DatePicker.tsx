import React, { useId, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Icon, isLastPopupTab, scrollWithin, useAnchoredPopup } from './UI';

type PickerType = 'date' | 'month' | 'datetime-local';
type Pane = 'days' | 'months' | 'years';
type Props = {
  type?: PickerType;
  label: string;
  value: string;
  onChange: (value: string) => void;
  min?: string;
  max?: string;
  required?: boolean;
  disabled?: boolean;
};

const dayLength = 86_400_000;
const yearPage = 12;
const weekdays = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
const pad = (n: number) => String(n).padStart(2, '0');

// Calendar arithmetic uses plain days. A date-time still returns a local wall-clock value.
function atDate(year: number, month: number, day = 1) {
  const date = new Date(0);
  date.setUTCFullYear(year, month, day);
  return date.getTime();
}

function parseDay(value?: string) {
  if (!value) return null;
  const date = Date.parse((value.length === 7 ? value + '-01' : value.slice(0, 10)) + 'T00:00:00Z');
  return Number.isFinite(date) ? date : null;
}

function parts(day: number) {
  const date = new Date(day);
  return { year: date.getUTCFullYear(), month: date.getUTCMonth(), day: date.getUTCDate(), weekday: (date.getUTCDay() + 6) % 7 };
}

function moveMonths(date: number, amount: number) {
  const { year, month, day } = parts(date);
  const lastDay = parts(atDate(year, month + amount + 1) - dayLength).day;
  return atDate(year, month + amount, Math.min(lastDay, day));
}

function pageStart(pane: Pane, date: number) {
  const { year, month } = parts(date);
  if (pane === 'days') return atDate(year, month);
  if (pane === 'months') return atDate(year, 0);
  return atDate(1 + yearPage * Math.floor((year - 1) / yearPage), 0);
}

const pageMonths = { days: 1, months: 12, years: yearPage * 12 };
const moscowDay = new Intl.DateTimeFormat('sv-SE', { timeZone: 'Europe/Moscow' });
const localDay = new Intl.DateTimeFormat('sv-SE');
const fullDate = new Intl.DateTimeFormat('ru-RU', { dateStyle: 'long', timeZone: 'UTC' });
const shortDate = new Intl.DateTimeFormat('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric', timeZone: 'UTC' });
const monthFormat = new Intl.DateTimeFormat('ru-RU', { month: 'long', timeZone: 'UTC' });
const monthAndYear = (date: number) => monthFormat.format(date) + ' ' + parts(date).year;
const dateValue = (date: number) => new Date(date).toISOString().slice(0, 10);

export function DatePicker({ type = 'date', label, value, onChange, min, max, required, disabled }: Props) {
  const today = parseDay((type === 'datetime-local' ? localDay : moscowDay).format(new Date()))!;
  const low = parseDay(min) ?? atDate(1, 0), high = parseDay(max) ?? atDate(9999, 11, 31);
  const selected = parseDay(value);
  const [open, setOpen] = useState(false), [invalid, setInvalid] = useState(false);
  const [pane, setPane] = useState<Pane>('days'), [cursor, setCursor] = useState(today);
  const [draft, setDraft] = useState<number | null>(null), [hour, setHour] = useState(''), [minute, setMinute] = useState('');
  const trigger = useRef<HTMLButtonElement>(null), popup = useRef<HTMLDivElement>(null), focusCell = useRef(false);
  const id = useId();
  const withTime = type === 'datetime-local';
  const position = useAnchoredPopup(open, trigger, popup, {
    width: 320, height: pane === 'days' ? (withTime ? 482 : 392) : 320,
  }, () => setOpen(false));
  const clamp = (date: number) => Math.min(high, Math.max(low, date));
  const allows = (first: number, last = first) => low <= high && last >= low && first <= high;
  const start = pageStart(pane, cursor);
  const hasSelection = selected !== null;
  const displayed = withTime ? draft : selected;
  const currentYear = parts(cursor).year;
  const pageLabels = { days: 'месяц', months: 'год', years: 'период' };
  const placeholder = { date: 'Выбери дату', month: 'Выбери месяц', 'datetime-local': 'Выбери дату и время' }[type];
  const summary = selected === null ? placeholder
    : type === 'month' ? monthAndYear(selected)
    : shortDate.format(selected) + (withTime ? ', ' + value.slice(11, 16) : '');

  function show() {
    setCursor(clamp(selected ?? today));
    setPane(type === 'month' ? 'months' : 'days');
    setDraft(selected);
    setHour(value.slice(11, 13));
    setMinute(value.slice(14, 16));
    focusCell.current = true;
    setOpen(true);
  }

  function close(restoreFocus = false) {
    if (restoreFocus) trigger.current?.focus({ preventScroll: true });
    setOpen(false);
  }

  function commit(next: string) {
    if (trigger.current?.matches(':disabled')) return;
    setInvalid(false);
    onChange(next);
    close(true);
  }

  function focusDate(date: number, nextPane = pane) {
    const next = clamp(date);
    if (next === cursor && nextPane === pane) return;
    focusCell.current = true;
    setPane(nextPane);
    setCursor(next);
  }

  function pickDate(date: number) {
    if (type === 'month') commit(dateValue(date).slice(0, 7));
    else if (withTime) {
      setDraft(date);
      focusDate(date, 'days');
    } else commit(dateValue(date));
  }

  useLayoutEffect(() => {
    if (open && trigger.current?.matches(':disabled')) setOpen(false);
  });
  useLayoutEffect(() => {
    const panel = popup.current;
    if (!open || !panel) return;
    const cell = panel.querySelector<HTMLButtonElement>('.date-cell[tabindex="0"]:not(:disabled)');
    if (focusCell.current) {
      (cell ?? panel.querySelector<HTMLButtonElement>('.date-title'))?.focus({ preventScroll: true });
      focusCell.current = false;
    }
    if (cell) scrollWithin(panel, cell);
  }, [open, cursor, pane, position.maxHeight]);

  function pageAllowed(direction: number) {
    const first = moveMonths(start, pageMonths[pane] * direction);
    return allows(first, moveMonths(first, pageMonths[pane]) - dayLength);
  }

  function onGridKey(e: React.KeyboardEvent) {
    const width = pane === 'days' ? 7 : 3;
    const column = pane === 'days' ? parts(cursor).weekday
      : pane === 'months' ? parts(cursor).month % width : (currentYear - parts(start).year) % width;
    const steps: Record<string, number> = {
      ArrowLeft: -1, ArrowRight: 1, ArrowUp: -width, ArrowDown: width, Home: -column, End: width - column - 1,
    };
    if (e.key === 'PageUp' || e.key === 'PageDown') {
      e.preventDefault();
      const direction = e.key === 'PageUp' ? -1 : 1;
      focusDate(moveMonths(cursor, (pane === 'days' && e.shiftKey ? 12 : pageMonths[pane]) * direction));
    } else if (Object.hasOwn(steps, e.key)) {
      e.preventDefault();
      const step = steps[e.key];
      focusDate(pane === 'days' ? cursor + step * dayLength : moveMonths(cursor, step * (pane === 'months' ? 1 : 12)));
    }
  }

  const firstDay = start - parts(start).weekday * dayLength;
  const dates = pane === 'days'
    ? Array.from({ length: 42 }, (_, index) => firstDay + index * dayLength)
    : Array.from({ length: 12 }, (_, index) => moveMonths(start, index * (pane === 'months' ? 1 : 12)));
  const columns = pane === 'days' ? 7 : 3;
  const rows = dates.filter((_, index) => index % columns === 0).map((_, index) => dates.slice(index * columns, (index + 1) * columns));
  const title = pane === 'days' ? monthAndYear(cursor)
    : pane === 'months' ? String(currentYear) : parts(start).year + '–' + Math.min(9999, parts(start).year + yearPage - 1);
  const quick = type === 'month' ? atDate(parts(today).year, parts(today).month) : today;
  const quickAllowed = allows(quick, type === 'month' ? moveMonths(quick, 1) - dayLength : quick);
  const time = draft !== null && hour && minute && Number(hour) < 24 && Number(minute) < 60
    ? dateValue(draft) + 'T' + pad(Number(hour)) + ':' + pad(Number(minute)) : '';
  const canApplyTime = !!time && (!min || time >= min) && (!max || time <= max);

  return <div className="date-control">
    <button type="button" ref={trigger} className="select-trigger date-trigger" aria-label={label}
      aria-describedby={id + '-value'} aria-expanded={open} aria-haspopup="dialog" aria-controls={open ? id : undefined}
      aria-invalid={invalid || undefined} aria-disabled={disabled || undefined} disabled={disabled}
      onClick={() => open ? close() : show()}
      onKeyDown={e => { if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); show(); } }}>
      <span id={id + '-value'} data-placeholder={!hasSelection}>{summary}</span><Icon name="calendar" size={17}/>
    </button>
    <input type={type} tabIndex={-1} className="select-validation" hidden aria-hidden="true"
      value={value} min={min} max={max} required={required} disabled={disabled} onChange={() => {}}
      onInvalid={e => { e.preventDefault(); setInvalid(true); trigger.current?.focus(); }}/>
    {invalid && <span className="picker-error" role="alert">{hasSelection ? 'Проверь доступный диапазон' : placeholder}</span>}
    {open && createPortal(<div id={id} className="date-popover picker-popup" ref={popup} style={position} role="dialog" aria-label={label}
      onKeyDown={e => {
        if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(true); }
        if (isLastPopupTab(e, e.currentTarget)) close(true);
      }}>
      <div className="date-navigation">
        <button type="button" className="date-arrow previous" aria-label={'Предыдущий ' + pageLabels[pane]} disabled={!pageAllowed(-1)}
          onClick={() => setCursor(clamp(moveMonths(cursor, -pageMonths[pane])))}><Icon name="chevron" size={16}/></button>
        <button type="button" className="date-title"
          aria-label={pane === 'days' ? 'Выбрать месяц и год' : pane === 'months' ? 'Выбрать год' : 'К выбору месяца'}
          onClick={() => focusDate(cursor, pane === 'months' ? 'years' : 'months')}>
          <span aria-live="polite">{title}</span><Icon name="down" size={15}/>
        </button>
        <button type="button" className="date-arrow" aria-label={'Следующий ' + pageLabels[pane]} disabled={!pageAllowed(1)}
          onClick={() => setCursor(clamp(moveMonths(cursor, pageMonths[pane])))}><Icon name="chevron" size={16}/></button>
      </div>
      <div className={'date-grid ' + pane} role="grid" aria-label={title} onKeyDown={onGridKey}>
        {pane === 'days' && <div className="date-row date-week" role="row">
          {weekdays.map(weekday => <span key={weekday} role="columnheader">{weekday}</span>)}
        </div>}
        {rows.map(row => <div className="date-row" role="row" key={row[0]}>
          {row.map(date => {
            const { year, month, day } = parts(date);
            const last = pane === 'days' ? date : moveMonths(date, pane === 'months' ? 1 : 12) - dayLength;
            const contains = (value: number | null) => value !== null && value >= date && value <= last;
            const caption = pane === 'days' ? day : pane === 'months' ? monthFormat.format(date) : year;
            const name = pane === 'days' ? fullDate.format(date) : pane === 'months' ? monthAndYear(date) : String(year);
            return <div key={date} role="gridcell" aria-selected={contains(displayed)}>
              <button type="button" className="date-cell" value={dateValue(date)} aria-label={name}
                aria-current={contains(today) ? 'date' : undefined} tabIndex={contains(cursor) ? 0 : -1}
                data-outside={pane === 'days' && month !== parts(start).month} disabled={!allows(date, last)}
                onClick={() => {
                  if (pane === 'years') focusDate(date, 'months');
                  else if (pane === 'months' && type !== 'month') focusDate(date, 'days');
                  else pickDate(date);
                }}>{caption}</button>
            </div>;
          })}
        </div>)}
      </div>
      {withTime && pane === 'days' && <div className="date-time">
        <div className="date-time-fields" role="group" aria-label="Время">
          <input aria-label="Часы" placeholder="чч" inputMode="numeric" maxLength={2} value={hour} onChange={e => setHour(e.target.value.replace(/\D/g, '').slice(0, 2))}/>
          <span aria-hidden="true">:</span>
          <input aria-label="Минуты" placeholder="мм" inputMode="numeric" maxLength={2} value={minute} onChange={e => setMinute(e.target.value.replace(/\D/g, '').slice(0, 2))}/>
        </div>
        <button type="button" disabled={!canApplyTime} onClick={() => commit(time)}>Применить</button>
      </div>}
      {((!required && hasSelection) || quickAllowed) && <div className="date-footer">
        {!required && hasSelection && <button type="button" onClick={() => commit('')}>Очистить</button>}
        {quickAllowed && <button type="button" className="date-today" onClick={() => pickDate(type === 'month' ? quick : today)}>
          {type === 'month' ? 'Текущий месяц' : 'Сегодня'}
        </button>}
      </div>}
    </div>, document.body)}
  </div>;
}
