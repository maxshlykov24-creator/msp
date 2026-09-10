import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";

const STORAGE_KEY = "kassa.hints";

interface HintsState {
  enabled: boolean;
  setEnabled: (value: boolean) => void;
  toggle: () => void;
}

const Ctx = createContext<HintsState | null>(null);

function readStored(): boolean {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw === "0") return false;
    if (raw === "1") return true;
  } catch {
    /* private mode */
  }
  return true;
}

function applyBody(enabled: boolean) {
  document.body.classList.toggle("hints-off", !enabled);
}

export function HintsProvider({ children }: { children: ReactNode }) {
  const [enabled, setEnabledState] = useState(() => {
    const on = readStored();
    applyBody(on);
    return on;
  });

  useEffect(() => {
    applyBody(enabled);
    try {
      localStorage.setItem(STORAGE_KEY, enabled ? "1" : "0");
    } catch {
      /* ignore */
    }
  }, [enabled]);

  const setEnabled = useCallback((value: boolean) => setEnabledState(value), []);
  const toggle = useCallback(() => setEnabledState((value) => !value), []);

  return <Ctx.Provider value={{ enabled, setEnabled, toggle }}>{children}</Ctx.Provider>;
}

export function useHints(): HintsState {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useHints outside HintsProvider");
  return ctx;
}

/** Блок пояснения раздела: скрывается вместе со всем шумом, когда тумблер выключен. */
export function Hint({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`page-hint ${className}`.trim()}>{children}</div>;
}

export function HintsToggle({ compact = false }: { compact?: boolean }) {
  const { enabled, toggle } = useHints();
  return (
    <button
      type="button"
      onClick={toggle}
      className={`sw ${enabled ? "on" : ""} ${compact ? "compact" : ""}`}
      aria-pressed={enabled}
      aria-label={enabled ? "Выключить пояснения" : "Включить пояснения"}
    >
      <i />
      <span>Пояснения</span>
    </button>
  );
}
