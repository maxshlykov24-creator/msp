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

// PWA: регистрация service worker (только в проде).
if ("serviceWorker" in navigator && import.meta.env.PROD) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  });
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AuthProvider>
      <Gate />
    </AuthProvider>
  </StrictMode>
);
