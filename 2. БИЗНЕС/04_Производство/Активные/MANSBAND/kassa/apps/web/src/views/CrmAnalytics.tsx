import { useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";

export function CrmAnalytics() {
  const { user } = useAuth();
  const [attempt, setAttempt] = useState(0);
  const [html, setHtml] = useState("");
  const [mode, setMode] = useState<"demo" | "review" | "live">("demo");
  const [error, setError] = useState("");
  const admin = user?.role === "admin";

  useEffect(() => {
    let active = true;
    setHtml("");
    setMode("demo");
    setError("");
    if (admin) {
      api.get<{ html: string; mode: "demo" | "review" | "live" }>("/stats/crm-preview").then((report) => {
        if (active) {
          setHtml(report.html);
          setMode(report.mode);
        }
      }).catch((err: unknown) => {
        if (active) setError(err instanceof ApiError ? err.message : "Не удалось открыть аналитику");
      });
    }
    return () => { active = false; };
  }, [admin, user?.id, attempt]);

  return <section className="analytics-screen">
    <div className="mb-3 flex flex-wrap items-center justify-between gap-2 text-sm">
      <a href="#dashboard" className="text-mute-soft underline underline-offset-4">К статистике кассы</a>
      {admin && <span className="rounded-full border border-ink-600 px-3 py-1 text-mute-soft">{mode === "live" ? "Боевой срез" : mode === "review" ? "Сверка не закрыта" : "Прототип · демоданные"}</span>}
    </div>
    {!admin ? <p role="status">Эта страница доступна только администраторам.</p>
      : error ? <div role="alert" className="card p-4">{error} <button className="ml-2 underline" onClick={() => setAttempt((n) => n + 1)}>Повторить</button></div>
      : html ? <iframe title="Аналитика CRM и логистики" sandbox="allow-scripts allow-popups allow-popups-to-escape-sandbox" srcDoc={html}
          className="block w-full rounded-xl border-0" />
      : <p role="status">Загружаем аналитику…</p>}
  </section>;
}
