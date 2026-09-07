import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import App from "./App.tsx";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import { Login } from "./views/Login";
import { ChangePassword } from "./views/ChangePassword";

function Gate() {
  const { user, loading } = useAuth();
  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-ink-950 text-mute">Загрузка…</div>
    );
  }
  if (!user) return <Login />;
  if (user.mustChangePassword) return <ChangePassword />;
  return <App />;
}

/** Старый SW кэшировал index.html → Safari получал битые JS. Снимаем до монтирования React. */
async function clearStaleClient(): Promise<void> {
  try {
    if ("serviceWorker" in navigator) {
      const regs = await navigator.serviceWorker.getRegistrations();
      await Promise.all(regs.map((r) => r.unregister()));
    }
    if (typeof caches !== "undefined") {
      const keys = await caches.keys();
      await Promise.all(keys.map((k) => caches.delete(k)));
    }
  } catch {
    // private mode / нет Cache API
  }
}

async function boot() {
  await clearStaleClient();
  const root = document.getElementById("root");
  if (!root) return;
  createRoot(root).render(
    <StrictMode>
      <AuthProvider>
        <Gate />
      </AuthProvider>
    </StrictMode>
  );
}

void boot();
