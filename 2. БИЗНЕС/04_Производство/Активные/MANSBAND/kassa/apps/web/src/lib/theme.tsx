import { Moon, Sun } from "lucide-react";
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";

const STORAGE_KEY = "kassa.theme";

export type Theme = "dark" | "light";

interface ThemeState {
  theme: Theme;
  setTheme: (theme: Theme) => void;
  toggle: () => void;
}

const Ctx = createContext<ThemeState | null>(null);

function readStored(): Theme {
  try {
    return localStorage.getItem(STORAGE_KEY) === "light" ? "light" : "dark";
  } catch {
    return "dark";
  }
}

function applyTheme(theme: Theme) {
  const light = theme === "light";
  document.documentElement.classList.toggle("theme-light", light);
  document.body.classList.toggle("theme-light", light);
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", light ? "#ededed" : "#0a0a0a");
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(() => {
    const next = readStored();
    applyTheme(next);
    return next;
  });

  useEffect(() => {
    applyTheme(theme);
    try {
      localStorage.setItem(STORAGE_KEY, theme);
    } catch {
      /* ignore */
    }
  }, [theme]);

  const setTheme = useCallback((next: Theme) => setThemeState(next), []);
  const toggle = useCallback(() => setThemeState((prev) => (prev === "light" ? "dark" : "light")), []);

  return <Ctx.Provider value={{ theme, setTheme, toggle }}>{children}</Ctx.Provider>;
}

export function useTheme(): ThemeState {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useTheme outside ThemeProvider");
  return ctx;
}

export function ThemeToggle({ compact = false }: { compact?: boolean }) {
  const { theme, setTheme } = useTheme();
  const light = theme === "light";
  const icon = compact ? 14 : 15;
  return (
    <div className={`theme-pill ${compact ? "is-compact" : ""}`} role="group" aria-label="Тема">
      <button
        type="button"
        className={light ? "is-on" : ""}
        aria-pressed={light}
        aria-label="Светлая тема"
        onClick={() => setTheme("light")}
      >
        <Sun size={icon} />
      </button>
      <button
        type="button"
        className={light ? "" : "is-on"}
        aria-pressed={!light}
        aria-label="Тёмная тема"
        onClick={() => setTheme("dark")}
      >
        <Moon size={icon} />
      </button>
    </div>
  );
}
