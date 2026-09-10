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
}) {
  const uid = useId();
  const btnRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [highlight, setHighlight] = useState(0);
  const [box, setBox] = useState({ top: 0, left: 0, width: 240, maxH: 320, up: false });

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
        options: group.options.filter((item) => item.label.toLowerCase().includes(q)),
      }))
      .filter((group) => group.options.length > 0);
  }, [source, query]);

  const flat = useMemo(() => flatten(visible), [visible]);

  function place() {
    const el = btnRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const width = Math.min(Math.max(rect.width, 240), window.innerWidth - 16);
    const left = Math.min(Math.max(8, rect.left), window.innerWidth - width - 8);
    const below = window.innerHeight - rect.bottom - 10;
    const above = rect.top - 10;
    const up = below < 240 && above > below;
    const maxH = Math.min(380, Math.max(180, up ? above : below));
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
  }, [open, visible.length]);

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
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const node = menuRef.current?.querySelector(`[data-idx="${highlight}"]`);
    node?.scrollIntoView({ block: "nearest" });
  }, [highlight, open]);

  function onTriggerKey(event: KeyboardEvent<HTMLButtonElement>) {
    if (disabled) return;
    if (event.key === "ArrowDown" || event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      if (!open) {
        window.dispatchEvent(new Event("kassa-select-open"));
        setOpen(true);
      }
    }
  }

  function onMenuKey(event: KeyboardEvent<HTMLDivElement>) {
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
    if (event.key === "Enter") {
      event.preventDefault();
      const item = flat[highlight];
      if (item && !item.disabled) pick(item.value);
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
          window.dispatchEvent(new Event("kassa-select-open"));
          setOpen(true);
        }}
        onKeyDown={onTriggerKey}
        className={`input w-full flex items-center gap-2 text-left cursor-pointer ${
          sm ? "py-2 text-[13px]" : ""
        } ${open ? "border-gold/60 ring-2 ring-gold/15" : ""} ${disabled ? "opacity-50 cursor-not-allowed" : ""}`}
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
            onKeyDown={onMenuKey}
            style={{
              position: "fixed",
              top: box.up ? undefined : box.top,
              bottom: box.up ? window.innerHeight - box.top : undefined,
              left: box.left,
              width: box.width,
              maxHeight: box.maxH,
              zIndex: 120,
            }}
            className="card p-0 overflow-hidden flex flex-col shadow-glow"
          >
            {withSearch && (
              <div className="shrink-0 p-2 border-b border-ink-700">
                <div className="relative">
                  <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-mute" />
                  <input
                    ref={searchRef}
                    className="input pl-8 py-2 text-[13px]"
                    value={query}
                    onChange={(e) => {
                      setQuery(e.target.value);
                      setHighlight(0);
                    }}
                    placeholder="Найти…"
                  />
                </div>
              </div>
            )}
            <div className="overflow-y-auto overscroll-contain py-1">
              {flat.length === 0 && (
                <div className="px-3 py-6 text-center text-[13px] text-mute">Ничего не найдено</div>
              )}
              {visible.map((group, gi) => (
                <div key={`${group.label}-${gi}`}>
                  {group.label && (
                    <div className="sticky top-0 z-[1] px-3 py-1.5 text-[11px] font-semibold uppercase tracking-[0.14em] text-mute bg-ink-850 border-y border-ink-800 first:border-t-0">
                      {group.label}
                    </div>
                  )}
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
                        className={`w-full flex items-center gap-2 px-3 min-h-11 text-left text-[14px] transition ${
                          item.disabled
                            ? "opacity-40 cursor-not-allowed"
                            : hi
                              ? "bg-white/10 text-white"
                              : "text-white/90 hover:bg-ink-800"
                        }`}
                      >
                        <span className="flex-1 min-w-0 truncate">{item.label}</span>
                        {active && <Check size={16} className="text-gold shrink-0" />}
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
