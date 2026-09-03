import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import type { AuthResponse, User } from "@kassa/shared";
import { api, getToken, setToken, USE_MOCK } from "../api/client";

interface AuthState {
  user: User | null;
  loading: boolean;
  login: (login: string, password: string) => Promise<void>;
  logout: () => void;
  changePassword: (currentPassword: string, newPassword: string) => Promise<void>;
}

const Ctx = createContext<AuthState | null>(null);

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
    api
      .get<User>("/auth/me")
      .then(setUser)
      .catch(() => setToken(null))
      .finally(() => setLoading(false));
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
