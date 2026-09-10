import { useState } from "react";
import { Button, Field } from "../components/ui";
import { useAuth } from "../auth/AuthContext";
import { warmupApi } from "../api/client";
import { ThemeToggle } from "../lib/theme";

export function Login() {
  const { login } = useAuth();
  const [loginValue, setLoginValue] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      // Safari/LTE: сначала «размять» TCP к /api, потом POST login
      await warmupApi();
      await login(loginValue.trim(), password);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка входа");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center p-4 bg-ink-950">
      <div className="fixed top-4 right-4 z-10">
        <ThemeToggle />
      </div>
      <form onSubmit={submit} className="card p-8 w-full max-w-sm">
        <div className="text-center mb-6">
          <div className="text-2xl font-extrabold text-white tracking-wide">MANSBAND</div>
          <div className="field-label mt-1">Касса · вход</div>
        </div>
        <div className="space-y-4">
          <Field label="Логин" required>
            <input
              className="input"
              value={loginValue}
              onChange={(e) => setLoginValue(e.target.value)}
              autoFocus
              autoComplete="username"
              autoCapitalize="none"
              autoCorrect="off"
              spellCheck={false}
            />
          </Field>
          <Field label="Пароль" required>
            <input
              className="input"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
            />
          </Field>
          {error && <div className="text-[13px] text-red-400">{error}</div>}
          <Button type="submit" className="w-full" disabled={busy}>
            {busy ? "Вход…" : "Войти"}
          </Button>
        </div>
      </form>
    </div>
  );
}
