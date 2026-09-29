import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import type { AuthResponse, User, UserRole } from "@kassa/shared";
import { api, ApiError, getToken, setToken, USE_MOCK } from "../api/client";

interface AuthState {
  user: User | null;
  loading: boolean;
  login: (login: string, password: string) => Promise<void>;
  logout: () => void;
  changePassword: (currentPassword: string, newPassword: string) => Promise<void>;
}

const Ctx = createContext<AuthState | null>(null);

const ROLES: UserRole[] = ["consultant", "logist", "finance", "crm", "rop", "admin", "seller"];

/** Имя и роль из JWT, если /auth/me не ответил по сети. Токен при этом не стираем. */
function userFromToken(token: string): User | null {
  try {
    const part = token.split(".")[1];
    if (!part) return null;
    const padded = part.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((part.length + 3) % 4);
    const binary = atob(padded);
    const bytes = Uint8Array.from(binary, (ch) => ch.charCodeAt(0));
    const json = JSON.parse(new TextDecoder().decode(bytes)) as {
      sub?: string;
      role?: string;
      name?: string;
      mustChangePassword?: boolean;
    };
    if (!json.sub || !json.role || !ROLES.includes(json.role as UserRole)) return null;
    return {
      id: json.sub,
      login: "",
      name: json.name ?? "",
      role: json.role as UserRole,
      mustChangePassword: json.mustChangePassword === true,
    };
  } catch {
    return null;
  }
}

const MOCK_USER: User = {
  id: "mock",
  login: "demo",
  name: "Демо",
  role: "admin",
  mustChangePassword: false,
};

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(USE_MOCK ? MOCK_USER : null);
  const [loading, setLoading] = useState<boolean>(!USE_MOCK);

  useEffect(() => {
    if (USE_MOCK) return;
    const token = getToken();
    if (!token) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    // Жёсткий потолок Gate (не трогаем token — иначе гонка с опоздавшим /me)
    const hardStop = window.setTimeout(() => {
      if (!cancelled) setLoading(false);
    }, 12_000);
    api
      .get<User>("/auth/me")
      .then((u) => {
        if (!cancelled) setUser(u);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        // Выход только на ответ 401. Таймаут и обрыв сети сессию не сбрасывают.
        if (err instanceof ApiError && err.status === 401) {
          setToken(null);
          setUser(null);
          return;
        }
        const provisional = userFromToken(getToken() ?? "");
        if (provisional) setUser(provisional);
      })
      .finally(() => {
        window.clearTimeout(hardStop);
        if (!cancelled) setLoading(false);
      });

    const onUnauthorized = () => {
      if (!cancelled) setUser(null);
    };
    window.addEventListener("kassa:unauthorized", onUnauthorized);

    return () => {
      cancelled = true;
      window.clearTimeout(hardStop);
      window.removeEventListener("kassa:unauthorized", onUnauthorized);
    };
  }, []);

  const value: AuthState = {
    user,
    loading,
    login: async (login, password) => {
      const res = await api.post<AuthResponse>("/auth/login", { login, password });
      setToken(res.token);
      setUser(res.user);
    },
    logout: () => {
      setToken(null);
      setUser(null);
    },
    changePassword: async (currentPassword, newPassword) => {
      await api.post("/auth/change-password", { currentPassword, newPassword });
      setUser((u) => (u ? { ...u, mustChangePassword: false } : u));
    },
  };

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth must be used within AuthProvider");
  return v;
}
