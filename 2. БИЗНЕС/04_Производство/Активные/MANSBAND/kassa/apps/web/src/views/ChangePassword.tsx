import { useState } from "react";
import { Button, Field } from "../components/ui";
import { useAuth } from "../auth/AuthContext";
import { ThemeToggle } from "../lib/theme";

export function ChangePassword() {
  const { changePassword, logout } = useAuth();
  const [currentPassword, setCurrent] = useState("");
  const [newPassword, setNew] = useState("");
  const [repeat, setRepeat] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (newPassword.length < 8) return setError("Минимум 8 символов");
    if (newPassword !== repeat) return setError("Пароли не совпадают");
    setBusy(true);
    try {
      await changePassword(currentPassword, newPassword);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка");
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
          <div className="text-xl font-extrabold text-white">Смена пароля</div>
          <div className="field-label mt-1">Задайте новый пароль для входа</div>
        </div>
        <div className="space-y-4">
          <Field label="Текущий пароль" required>
            <input className="input" type="password" value={currentPassword} onChange={(e) => setCurrent(e.target.value)} autoFocus />
          </Field>
          <Field label="Новый пароль" required hint="Минимум 8 символов">
            <input className="input" type="password" value={newPassword} onChange={(e) => setNew(e.target.value)} />
          </Field>
          <Field label="Повторите новый пароль" required>
            <input className="input" type="password" value={repeat} onChange={(e) => setRepeat(e.target.value)} />
          </Field>
          {error && <div className="text-[13px] text-red-400">{error}</div>}
          <Button type="submit" className="w-full" disabled={busy}>
            {busy ? "Сохранение…" : "Сохранить"}
          </Button>
          <button type="button" onClick={logout} className="w-full text-[13px] text-mute hover:text-white">
            Выйти
          </button>
        </div>
      </form>
    </div>
  );
}
