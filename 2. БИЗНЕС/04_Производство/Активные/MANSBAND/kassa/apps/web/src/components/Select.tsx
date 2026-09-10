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

export function Select({
  value,
  onChange,
  options,
  groups,
  placeholder = "Выбрать",
  disabled,
  id,
  className = "",
  size = "md",
  leading,
  searchable,
  menuMinWidth = 248,
}: {
  value: string;
  onChange: (value: string) => void;
  options?: SelectOption[];
  groups?: SelectGroup[];
  placeholder?: string;
  disabled?: boolean;
  id?: string;
  className?: string;
  size?: "md" | "sm";
  leading?: ReactNode;
  searchable?: boolean;
  menuMinWidth?: number;
}) {
  const uid = useId();
  const btnRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
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

  function place() {
    const el = btnRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const width = Math.min(Math.max(rect.width, menuMinWidth), window.innerWidth - 16);
    const left = Math.min(Math.max(8, rect.left), window.innerWidth - width - 8);
    const below = window.innerHeight - rect.bottom - 10;
    const above = rect.top - 10;
    const up = below < 240 && above > below;
    const maxH = Math.min(400, Math.max(180, up ? above : below));
    setBox({
      top: up ? rect.top - 6 : rect.bottom + 6,
      left,
      width,
      maxH,
      up,
    });
  }

  function close() {
    setOpen(false);
    setQuery("");
  }

  function pick(next: string) {
    onChange(next);
    close();
    btnRef.current?.focus();
  }

  useLayoutEffect(() => {
    if (!open) return;
    place();
    const sync = () => place();
    window.addEventListener("resize", sync);
    window.addEventListener("scroll", sync, true);
    return () => {
      window.removeEventListener("resize", sync);
      window.removeEventListener("scroll", sync, true);
    };
  }, [open, visible.length, menuMinWidth]);

  useEffect(() => {
    if (!open) return;
    const onDoc = (event: MouseEvent) => {
      const node = event.target as Node;
      if (btnRef.current?.contains(node) || menuRef.current?.contains(node)) return;
      close();
    };
    const onForeign = () => close();
    document.addEventListener("mousedown", onDoc);
    window.addEventListener("kassa-select-open", onForeign);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      window.removeEventListener("kassa-select-open", onForeign);
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const idx = Math.max(0, flat.findIndex((item) => item.value === value));
    setHighlight(idx);
    if (withSearch) {
      requestAnimationFrame(() => searchRef.current?.focus());
    } else {
      requestAnimationFrame(() => menuRef.current?.focus());
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    if (highlight >= flat.length) setHighlight(Math.max(0, flat.length - 1));
  }, [flat.length, highlight, open]);

  useEffect(() => {
    if (!open) return;
    const node = menuRef.current?.querySelector(`[data-idx="${highlight}"]`);
    node?.scrollIntoView({ block: "nearest" });
  }, [highlight, open]);

  function openMenu(seed = "") {
    if (disabled) return;
    window.dispatchEvent(new Event("kassa-select-open"));
    if (seed) setQuery(seed);
    setOpen(true);
  }

  function onNavKey(event: KeyboardEvent<HTMLElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      close();
      btnRef.current?.focus();
      return;
    }
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setHighlight((i) => Math.min(flat.length - 1, i + 1));
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      setHighlight((i) => Math.max(0, i - 1));
      return;
    }
    if (event.key === "Home") {
      event.preventDefault();
      setHighlight(0);
      return;
    }
    if (event.key === "End") {
      event.preventDefault();
      setHighlight(Math.max(0, flat.length - 1));
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
      if (!open) openMenu();
      return;
    }
    if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
      event.preventDefault();
      openMenu(event.key);
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
          openMenu();
        }}
        onKeyDown={onTriggerKey}
        className={`input w-full flex items-center gap-2 text-left cursor-pointer ${
          sm ? "py-2 text-[13px]" : ""
        } ${open ? "kassa-select-trigger-open" : ""} ${disabled ? "opacity-50 cursor-not-allowed" : ""}`}
      >
        {leading && <span className="text-mute shrink-0">{leading}</span>}
        <span className={`flex-1 min-w-0 truncate ${selected ? "text-white" : "text-mute/70"}`}>
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
              top: box.up ? undefined : box.top,
              bottom: box.up ? window.innerHeight - box.top : undefined,
              left: box.left,
              width: box.width,
              maxHeight: box.maxH,
              zIndex: 120,
            }}
            className="kassa-select-menu"
          >
            {withSearch && (
              <div className="kassa-select-search">
                <Search size={14} className="kassa-select-search-icon" />
                <input
                  ref={searchRef}
                  className="input pl-8 py-2 text-[13px]"
                  value={query}
                  onChange={(e) => {
                    setQuery(e.target.value);
                    setHighlight(0);
                  }}
                  onKeyDown={onNavKey}
                  placeholder="Найти…"
                  autoComplete="off"
                  autoCorrect="off"
                  spellCheck={false}
                />
              </div>
            )}
            <div className="kassa-select-list">
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
                        onMouseEnter={() => setHighlight(idx)}
                        onClick={() => !item.disabled && pick(item.value)}
                        className={`kassa-select-item${hi ? " is-hi" : ""}${active ? " is-on" : ""}${
                          item.value === "" && !active ? " is-empty" : ""
                        }`}
                      >
                        <span className="min-w-0 truncate">{item.shortLabel ?? item.label}</span>
                        {item.hint && <span className="kassa-select-hint">{item.hint}</span>}
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
