import { useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import { CalendarDays, ChevronLeft, ChevronRight } from "lucide-react";
import { Select } from "./Select";

type Props = {
  value: string;
  onChange?: (value: string) => void;
  className?: string;
  disabled?: boolean;
  readOnly?: boolean;
  min?: string;
  max?: string;
  required?: boolean;
  id?: string;
  "aria-label"?: string;
};
const months = Array.from({ length: 12 }, (_, i) => new Date(2024, i, 1).toLocaleDateString("ru-RU", { month: "long" }));
const weekdays = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
function iso(date: Date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}
function parse(value: string) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) return null;
  const date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  return iso(date) === value ? date : null;
}
function shift(date: Date, days: number) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate() + days);
}
function shiftMonth(date: Date, delta: number) {
  const start = new Date(date.getFullYear(), date.getMonth() + delta, 1);
  const last = new Date(start.getFullYear(), start.getMonth() + 1, 0).getDate();
  return new Date(start.getFullYear(), start.getMonth(), Math.min(date.getDate(), last));
}

/** Дата остаётся YYYY-MM-DD; календарь не зависит от часового пояса браузера. */
export function DatePicker({ value, onChange, className = "input", disabled, readOnly, min, max, required, id, "aria-label": ariaLabel }: Props) {
  const uid = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [cursor, setCursor] = useState(() => parse(value) ?? new Date());
  const [box, setBox] = useState({ top: 0, left: 0, width: 320, maxHeight: 480 });
  const selected = parse(value);
  const today = iso(new Date());
  const locked = disabled || readOnly;
  const allowed = (date: Date) => (!min || iso(date) >= min) && (!max || iso(date) <= max);

  function clamp(date: Date) {
    if (min && iso(date) < min) return parse(min) ?? date;
    if (max && iso(date) > max) return parse(max) ?? date;
    return date;
  }
  function close() {
    setOpen(false);
    trigger.current?.focus();
  }
  function pick(date: Date) {
    if (!allowed(date)) return;
    onChange?.(iso(date));
    close();
  }
  function show() {
    if (locked) return;
    window.dispatchEvent(new Event("kassa-select-open"));
    setCursor(clamp(parse(value) ?? new Date()));
    setOpen(true);
  }
  useLayoutEffect(() => {
    if (!open) return;
    function place() {
      if (!trigger.current || !panel.current) return;
      const rect = trigger.current.getBoundingClientRect();
      const viewport = window.visualViewport;
      const top = viewport?.offsetTop ?? 0;
      const height = viewport?.height ?? window.innerHeight;
      const width = Math.min(328, window.innerWidth - 16);
      const below = top + height - rect.bottom - 8;
      const above = rect.top - top - 8;
      const maxHeight = Math.max(0, Math.min(480, height - 16));
      const panelHeight = Math.min(panel.current.scrollHeight + 2, maxHeight);
      // Если рядом с полем тесно, сдвигаем весь календарь внутрь экрана,
      // чтобы быстрые сроки оставались видимыми без прокрутки.
      const panelTop = below >= panelHeight ? rect.bottom + 8
        : above >= panelHeight ? rect.top - panelHeight - 8
        : Math.max(top + 8, top + height - panelHeight - 8);
      setBox({
        top: panelTop,
        left: Math.min(Math.max(8, rect.left), window.innerWidth - width - 8),
        width,
        maxHeight,
      });
    }
    place();
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    window.visualViewport?.addEventListener("resize", place);
    window.visualViewport?.addEventListener("scroll", place);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
      window.visualViewport?.removeEventListener("resize", place);
      window.visualViewport?.removeEventListener("scroll", place);
    };
  }, [open]);
  useEffect(() => {
    if (!open) return;
    const handle = requestAnimationFrame(() => panel.current?.querySelector<HTMLButtonElement>(`[data-date="${iso(cursor)}"]`)?.focus({ preventScroll: true }));
    return () => cancelAnimationFrame(handle);
  }, [open, cursor]);
  useEffect(() => {
    if (!open) return;
    function outside(event: MouseEvent) {
      const node = event.target as HTMLElement;
      // Списки месяца и года рендерятся порталом общего Select.
      if (panel.current?.contains(node) || trigger.current?.contains(node) || node.closest(".kassa-select-menu")) return;
      setOpen(false);
    }
    document.addEventListener("mousedown", outside);
    return () => document.removeEventListener("mousedown", outside);
  }, [open]);

  function onKey(event: KeyboardEvent) {
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); close(); return; }
    // Навигация по дням не перехватывает клавиши месяца и года.
    if (!(event.target instanceof HTMLElement) || !event.target.hasAttribute("data-date")) return;
    let next: Date | null = null;
    if (event.key === "ArrowLeft") next = shift(cursor, -1);
    if (event.key === "ArrowRight") next = shift(cursor, 1);
    if (event.key === "ArrowUp") next = shift(cursor, -7);
    if (event.key === "ArrowDown") next = shift(cursor, 7);
    if (event.key === "PageUp") next = shiftMonth(cursor, event.shiftKey ? -12 : -1);
    if (event.key === "PageDown") next = shiftMonth(cursor, event.shiftKey ? 12 : 1);
    if (event.key === "Home") next = shift(cursor, -((cursor.getDay() + 6) % 7));
    if (event.key === "End") next = shift(cursor, 6 - ((cursor.getDay() + 6) % 7));
    if (next) { event.preventDefault(); setCursor(clamp(next)); }
  }
  const first = new Date(cursor.getFullYear(), cursor.getMonth(), 1);
  const start = shift(first, -((first.getDay() + 6) % 7));
  const firstYear = parse(min ?? "")?.getFullYear() ?? Math.min(new Date().getFullYear() - 100, cursor.getFullYear());
  const lastYear = parse(max ?? "")?.getFullYear() ?? Math.max(new Date().getFullYear() + 100, cursor.getFullYear());

  return (
    <div className="date-picker">
      <button ref={trigger} type="button" id={id} disabled={disabled} aria-readonly={readOnly || undefined}
        aria-label={ariaLabel} aria-describedby={`${uid}-value`} aria-haspopup="dialog" aria-expanded={open} aria-controls={open ? `${uid}-calendar` : undefined}
        className={`${className} date-picker-trigger${open ? " kassa-select-trigger-open" : ""}`}
        onClick={() => open ? close() : show()}
        onKeyDown={(event) => { if (event.key === "ArrowDown" && !locked) { event.preventDefault(); show(); } }}>
        <span className={selected ? "" : "date-picker-placeholder"}>{selected ? selected.toLocaleDateString("ru-RU") : "Выбрать дату"}</span>
        <CalendarDays size={17} aria-hidden="true" />
      </button>
      <span id={`${uid}-value`} className="sr-only">{selected ? `Выбрана дата: ${selected.toLocaleDateString("ru-RU")}` : "Дата не выбрана"}</span>
      {open && createPortal(
        <div ref={panel} id={`${uid}-calendar`} className="date-picker-panel" role="dialog" aria-label="Выбор даты"
          style={{ position: "fixed", ...box, zIndex: 119 }} onKeyDown={onKey}>
          <div className="date-picker-header">
            <button type="button" className="date-picker-nav" aria-label="Предыдущий месяц" disabled={!allowed(new Date(cursor.getFullYear(), cursor.getMonth(), 0))} onClick={() => setCursor(clamp(shiftMonth(cursor, -1)))}><ChevronLeft size={17} /></button>
            <div className="date-picker-month">
              <Select value={String(cursor.getMonth())} onChange={(month) => setCursor(clamp(new Date(cursor.getFullYear(), Number(month), 1)))} options={months.map((label, i) => ({ value: String(i), label }))} searchable={false} menuMinWidth={170} ariaLabel="Месяц" />
            </div>
            <div className="date-picker-year">
              <Select value={String(cursor.getFullYear())} onChange={(year) => setCursor(clamp(new Date(Number(year), cursor.getMonth(), 1)))} options={Array.from({ length: Math.max(1, lastYear - firstYear + 1) }, (_, i) => ({ value: String(firstYear + i), label: String(firstYear + i) }))} menuMinWidth={120} ariaLabel="Год" />
            </div>
            <button type="button" className="date-picker-nav" aria-label="Следующий месяц" disabled={!allowed(new Date(cursor.getFullYear(), cursor.getMonth() + 1, 1))} onClick={() => setCursor(clamp(shiftMonth(cursor, 1)))}><ChevronRight size={17} /></button>
          </div>
          <div className="date-picker-caption" aria-live="polite">{selected ? selected.toLocaleDateString("ru-RU", { day: "numeric", month: "long", year: "numeric" }) : "Выбери удобную дату"}</div>
          <div className="date-picker-grid" role="grid" aria-label={`${months[cursor.getMonth()]} ${cursor.getFullYear()}`}>
            <div className="date-picker-week" role="row">{weekdays.map((day) => <span key={day} role="columnheader">{day}</span>)}</div>
            {Array.from({ length: 6 }, (_, week) => <div className="date-picker-week" role="row" key={week}>
              {Array.from({ length: 7 }, (_, day) => {
                const date = shift(start, week * 7 + day);
                const key = iso(date);
                const active = key === value;
                return <div role="gridcell" aria-selected={active} key={key}>
                  <button type="button" data-date={key} tabIndex={key === iso(cursor) ? 0 : -1}
                    disabled={!allowed(date)} aria-current={key === today ? "date" : undefined}
                    aria-label={date.toLocaleDateString("ru-RU", { weekday: "long", day: "numeric", month: "long", year: "numeric" })}
                    className={`date-picker-day${date.getMonth() !== cursor.getMonth() ? " is-outside" : ""}${key === today ? " is-today" : ""}${active ? " is-selected" : ""}`}
                    onClick={() => pick(date)}>{date.getDate()}</button>
                </div>;
              })}
            </div>)}
          </div>
          <div className="date-picker-shortcuts">
            {[{ label: "Сегодня", date: new Date() }, { label: "Завтра", date: shift(new Date(), 1) }, { label: "+7 дней", date: shift(new Date(), 7) }].map(({ label, date }) => <button type="button" key={label} disabled={!allowed(date)} onClick={() => pick(date)}>{label}</button>)}
          </div>
          <div className="date-picker-footer">
            <button type="button" disabled={!value || required} onClick={() => { onChange?.(""); close(); }}>Очистить</button>
            <button type="button" onClick={close}>Закрыть</button>
          </div>
        </div>, document.body)}
    </div>
  );
}
