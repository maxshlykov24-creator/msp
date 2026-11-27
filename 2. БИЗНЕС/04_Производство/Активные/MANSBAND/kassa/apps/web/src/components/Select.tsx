import {
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { Check, ChevronDown, Search } from "lucide-react";
import { getStageStyle } from "../lib/stageColors";

export type SelectOption = {
  value: string;
  label: string;
  /** Короткое имя в открытом списке. В закрытом поле остаётся полный label. */
  shortLabel?: string;
  /** Вторая колонка справа: банк, магазин, пояснение. */
  hint?: string;
  disabled?: boolean;
};

export type SelectGroup = {
  label: string;
  options: SelectOption[];
};

export function opts(...items: Array<string | [string, string]>): SelectOption[] {
  return items.map((item) =>
    typeof item === "string" ? { value: item, label: item } : { value: item[0], label: item[1] }
  );
}

function flatten(groups: SelectGroup[]): SelectOption[] {
  return groups.flatMap((group) => group.options);
}

function asGroups(groups: SelectGroup[] | undefined, options: SelectOption[] | undefined): SelectGroup[] {
  if (groups && groups.length > 0) return groups.filter((group) => group.options.length > 0);
  if (options && options.length > 0) return [{ label: "", options }];
  return [];
}

function matchesQuery(item: SelectOption, q: string): boolean {
  return [item.label, item.shortLabel, item.hint].some((part) => (part ?? "").toLowerCase().includes(q));
}

/** Координаты для порталов с position: fixed, с учётом клавиатуры и вырезов экрана. */
export function popoverViewport() {
  const viewport = window.visualViewport;
  const root = document.documentElement;
  const css = getComputedStyle(root);
  const inset = (side: string) => parseFloat(css.getPropertyValue(`--safe-${side}`)) || 0;
  const left = Math.max(inset("left"), viewport?.offsetLeft ?? 0) + 8;
  const right = Math.min(
    root.clientWidth - inset("right"),
    (viewport?.offsetLeft ?? 0) + (viewport?.width ?? root.clientWidth)
  ) - 8;
  const top = Math.max(inset("top"), viewport?.offsetTop ?? 0) + 8;
  const nav = document.querySelector(".kassa-mobile-nav")?.getBoundingClientRect();
  const bottom = Math.min(
    (viewport?.offsetTop ?? 0) + (viewport?.height ?? root.clientHeight),
    nav?.height ? nav.top : root.clientHeight - inset("bottom")
  ) - 8;
  return { left, right, top, bottom };
}

export function Select({
  value,
  onChange,
  options,
  groups,
  placeholder = "Выбрать",
  disabled,
  id,
  className = "",
  triggerClassName,
  size = "md",
  leading,
  searchable,
  menuMinWidth = 248,
  fitLabel = false,
  colorOptions = false,
  ariaLabel,
}: {
  value: string;
  onChange: (value: string) => void;
  options?: SelectOption[];
  groups?: SelectGroup[];
  placeholder?: string;
  disabled?: boolean;
  id?: string;
  className?: string;
  /** Шрифт кнопки. Если не задан, мелкий размер берёт 13px. */
  triggerClassName?: string;
  size?: "md" | "sm";
  leading?: ReactNode;
  searchable?: boolean;
  menuMinWidth?: number;
  /** Подпись не обрезается, кнопка шире текста. */
  fitLabel?: boolean;
  /** Показывает цвет этапа рядом с пунктами списка. */
  colorOptions?: boolean;
  ariaLabel?: string;
}) {
  const uid = useId();
  const btnRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const focusSearch = useRef(false);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [highlight, setHighlight] = useState(0);
  const [box, setBox] = useState({ top: 0, left: 0, width: 248, maxH: 320, up: false });

  const source = useMemo(() => asGroups(groups, options), [groups, options]);
  const all = useMemo(() => flatten(source), [source]);
  const selected = all.find((item) => item.value === value);
  const withSearch = searchable ?? all.length > 8;

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return source;
    return source
      .map((group) => ({
        ...group,
        options: group.options.filter((item) => matchesQuery(item, q)),
      }))
      .filter((group) => group.options.length > 0);
  }, [source, query]);

  const flat = useMemo(() => flatten(visible), [visible]);

  function close() {
    setOpen(false);
    setQuery("");
  }

  function pick(next: string) {
    onChange(next);
    close();
    btnRef.current?.focus({ preventScroll: true });
  }

  useLayoutEffect(() => {
    if (!open) return;
    function place() {
      const el = btnRef.current;
      if (!el) return;
      const rect = el.getBoundingClientRect();
      const view = popoverViewport();
      const width = Math.min(Math.max(rect.width, menuMinWidth), view.right - view.left);
      const left = Math.min(Math.max(view.left, rect.left), view.right - width);
      const gap = 8;
      const desired = 420;
      const belowTop = Math.max(view.top, rect.bottom + gap);
      const aboveBottom = Math.min(view.bottom, rect.top - gap);
      const below = view.bottom - belowTop;
      const above = aboveBottom - view.top;

      // При нехватке места рядом с кнопкой список занимает доступную область экрана.
      if (Math.max(above, below) < 140) {
        setBox({ top: view.top, left, width, maxH: Math.max(0, view.bottom - view.top), up: false });
        return;
      }
      const up = below < desired && above > below;
      setBox({
        top: up ? aboveBottom : belowTop,
        left,
        width,
        maxH: Math.min(desired, up ? above : below),
        up,
      });
    }
    place();
    const sync = (event: Event) => {
      if (event.target instanceof Node && menuRef.current?.contains(event.target)) return;
      place();
    };
    window.addEventListener("resize", sync);
    window.addEventListener("scroll", sync, true);
    window.visualViewport?.addEventListener("resize", sync);
    window.visualViewport?.addEventListener("scroll", sync);
    return () => {
      window.removeEventListener("resize", sync);
      window.removeEventListener("scroll", sync, true);
      window.visualViewport?.removeEventListener("resize", sync);
      window.visualViewport?.removeEventListener("scroll", sync);
    };
  }, [open, menuMinWidth]);

  useEffect(() => {
    if (!open) return;
    const onDoc = (event: PointerEvent) => {
      const node = event.target as Node;
      if (btnRef.current?.contains(node) || menuRef.current?.contains(node)) return;
      close();
    };
    const onForeign = () => close();
    document.addEventListener("pointerdown", onDoc);
    window.addEventListener("kassa-select-open", onForeign);
    return () => {
      document.removeEventListener("pointerdown", onDoc);
      window.removeEventListener("kassa-select-open", onForeign);
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const target = withSearch && focusSearch.current ? searchRef : menuRef;
    const frame = requestAnimationFrame(() => target.current?.focus({ preventScroll: true }));
    return () => cancelAnimationFrame(frame);
  }, [open, withSearch]);

  function revealOption(index: number) {
    const list = listRef.current;
    const option = list?.querySelector(`[data-idx="${index}"]`);
    if (!list || !option) return;
    const bounds = list.getBoundingClientRect();
    const rect = option.getBoundingClientRect();
    if (rect.top < bounds.top) list.scrollTop -= bounds.top - rect.top;
    else if (rect.bottom > bounds.bottom) list.scrollTop += rect.bottom - bounds.bottom;
  }

  useLayoutEffect(() => {
    if (open) revealOption(highlight);
    // Прокручиваем к выбранному при открытии. Наведение мыши список не двигает.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  function openMenu(searchOnOpen: boolean, seed = "") {
    if (disabled) return;
    window.dispatchEvent(new Event("kassa-select-open"));
    focusSearch.current = searchOnOpen;
    setQuery(seed);
    setHighlight(seed ? 0 : Math.max(0, all.findIndex((item) => item.value === value)));
    setOpen(true);
  }

  function moveHighlight(index: number) {
    setHighlight(index);
    revealOption(index);
  }

  function onNavKey(event: KeyboardEvent<HTMLElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      close();
      btnRef.current?.focus({ preventScroll: true });
      return;
    }
    if (event.key === "ArrowDown") {
      event.preventDefault();
      moveHighlight(Math.max(0, Math.min(flat.length - 1, highlight + 1)));
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      moveHighlight(Math.max(0, highlight - 1));
      return;
    }
    if (event.key === "Home") {
      event.preventDefault();
      moveHighlight(0);
      return;
    }
    if (event.key === "End") {
      event.preventDefault();
      moveHighlight(Math.max(0, flat.length - 1));
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      const item = flat[highlight];
      if (item && !item.disabled) pick(item.value);
    }
  }

  function onTriggerKey(event: KeyboardEvent<HTMLButtonElement>) {
    if (disabled) return;
    if (event.key === "ArrowDown" || event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      if (!open) openMenu(true);
      return;
    }
    if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
      event.preventDefault();
      if (withSearch) openMenu(true, event.key);
    }
  }

  const sm = size === "sm";
  let walk = -1;

  return (
    <div className={`relative min-w-0 ${className}`}>
      <button
        ref={btnRef}
        type="button"
        id={id}
        aria-label={ariaLabel}
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={`${uid}-menu`}
        onClick={() => {
          if (disabled) return;
          if (open) {
            close();
            return;
          }
          openMenu(!window.matchMedia("(pointer: coarse)").matches);
        }}
        onKeyDown={onTriggerKey}
        className={`input w-full flex items-center gap-2 text-left cursor-pointer ${
          sm ? "py-2" : ""
        } ${triggerClassName ?? (sm ? "text-[13px]" : "")} ${open ? "kassa-select-trigger-open" : ""} ${disabled ? "opacity-50 cursor-not-allowed" : ""}`}
      >
        {leading && <span className="text-mute shrink-0">{leading}</span>}
        <span className={`${fitLabel ? "whitespace-nowrap" : "flex-1 min-w-0 truncate"} ${selected ? "text-white" : "text-mute/70"}`}>
          {selected?.label ?? placeholder}
        </span>
        <ChevronDown size={sm ? 14 : 16} className={`text-mute shrink-0 transition ${open ? "rotate-180" : ""}`} />
      </button>

      {open &&
        createPortal(
          <div
            ref={menuRef}
            id={`${uid}-menu`}
            role="listbox"
            tabIndex={-1}
            onKeyDown={onNavKey}
            style={{
              position: "fixed",
              top: box.top,
              translate: box.up ? "0 -100%" : undefined,
              left: box.left,
              width: box.width,
              maxHeight: box.maxH,
              zIndex: 120,
            }}
            className="kassa-select-menu"
          >
            {withSearch && (
              <div className="kassa-select-search">
                <div className="relative">
                  <Search size={14} className="kassa-select-search-icon" />
                  <input
                    ref={searchRef}
                    className="input pl-8 py-2 text-[13px]"
                    value={query}
                    onChange={(e) => {
                      setQuery(e.target.value);
                      setHighlight(0);
                      listRef.current?.scrollTo(0, 0);
                    }}
                    placeholder="Найти…"
                    autoComplete="off"
                    autoCorrect="off"
                    spellCheck={false}
                  />
                </div>
              </div>
            )}
            <div ref={listRef} className="kassa-select-list">
              {flat.length === 0 && <div className="kassa-select-empty">Ничего не найдено</div>}
              {visible.map((group, gi) => (
                <div key={`${group.label}-${gi}`}>
                  {group.label && <div className="kassa-select-cap">{group.label}</div>}
                  {group.options.map((item) => {
                    walk += 1;
                    const idx = walk;
                    const active = item.value === value;
                    const hi = idx === highlight;
                    return (
                      <button
                        key={item.value || `empty-${idx}`}
                        type="button"
                        role="option"
                        data-idx={idx}
                        aria-selected={active}
                        disabled={item.disabled}
                        onPointerEnter={(event) => { if (event.pointerType === "mouse") setHighlight(idx); }}
                        onClick={() => !item.disabled && pick(item.value)}
                        className={`kassa-select-item${hi ? " is-hi" : ""}${active ? " is-on" : ""}${
                          item.value === "" && !active ? " is-empty" : ""
                        }`}
                      >
                        {colorOptions && (
                          <span
                            aria-hidden="true"
                            className="kassa-select-tone-dot"
                            style={{
                              background: getStageStyle(item.value).text,
                              boxShadow: `0 0 12px ${getStageStyle(item.value).ring}`,
                            }}
                          />
                        )}
                        <span className="min-w-0 flex items-center gap-1.5">
                          <span className="truncate">{item.shortLabel ?? item.label}</span>
                          {item.hint && <span className="kassa-select-hint">{item.hint}</span>}
                        </span>
                        {active && <Check size={15} className="kassa-select-check" />}
                      </button>
                    );
                  })}
                </div>
              ))}
            </div>
          </div>,
          document.body
        )}
    </div>
  );
}
