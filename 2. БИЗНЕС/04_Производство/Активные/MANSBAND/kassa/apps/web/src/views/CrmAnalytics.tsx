import { useEffect, useRef, useState } from "react";
import { DatePicker } from "../components/DatePicker";
import { api, ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";

type ReportDate = { value: string; min?: string; max?: string };

function connectReportCalendar(html: string): string {
  // Календарь кассы живёт снаружи sandbox. В обеих версиях отчёта #anchor
  // передаёт дату штатному обработчику change внутри отчёта.
  return html.replace(/<\/body>/i, `<script>
    (() => {
      let last;
      const anchor = () => document.querySelector("input#anchor[type=date]");
      const sync = () => {
        const input = anchor();
        if (input) input.hidden = true;
        const date = input ? { value: input.value, min: input.min, max: input.max } : null;
        const key = JSON.stringify(date);
        if (key === last) return;
        last = key;
        parent.postMessage({ type: "kassa-report-date", date }, "*");
      };
      addEventListener("message", (event) => {
        const value = event.data?.value;
        const input = anchor();
        if (event.source !== parent || event.data?.type !== "kassa-report-set-date" || !input ||
            typeof value !== "string" || !/^\\d{4}-\\d{2}-\\d{2}$/.test(value)) return;
        input.value = value;
        if (input.checkValidity()) input.dispatchEvent(new Event("change", { bubbles: true }));
      });
      new MutationObserver(sync).observe(document.body, { childList: true, subtree: true });
      sync();
    })();
  </script></body>`);
}

export function CrmAnalytics() {
  const { user } = useAuth();
  const [attempt, setAttempt] = useState(0);
  const iframe = useRef<HTMLIFrameElement>(null);
  const [date, setDate] = useState<ReportDate | null>(null);
  const [html, setHtml] = useState("");
  const [mode, setMode] = useState<"demo" | "review" | "live">("demo");
  const [error, setError] = useState("");
  const admin = user?.role === "admin";

  useEffect(() => {
    let active = true;
    setHtml("");
    setDate(null);
    setMode("demo");
    setError("");
    if (admin) {
      api.get<{ html: string; mode: "demo" | "review" | "live" }>("/stats/crm-preview").then((report) => {
        if (active) {
          setHtml(connectReportCalendar(report.html));
          setMode(report.mode);
        }
      }).catch((err: unknown) => {
        if (active) setError(err instanceof ApiError ? err.message : "Не удалось открыть аналитику");
      });
    }
    return () => { active = false; };
  }, [admin, user?.id, attempt]);

  useEffect(() => {
    if (!admin) return;
    function receiveDate(event: MessageEvent) {
      const source = iframe.current?.contentWindow;
      if (!source || event.source !== source || event.data?.type !== "kassa-report-date") return;
      const value = event.data.date;
      const validDay = (day: unknown): day is string => typeof day === "string" && /^\d{4}-\d{2}-\d{2}$/.test(day);
      setDate(value && validDay(value.value) ? {
        value: value.value,
        min: validDay(value.min) ? value.min : undefined,
        max: validDay(value.max) ? value.max : undefined,
      } : null);
    }
    window.addEventListener("message", receiveDate);
    return () => window.removeEventListener("message", receiveDate);
  }, [admin]);

  return <section className="analytics-screen">
    <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-sm">
      <a href="#dashboard" className="text-mute-soft underline underline-offset-4">К статистике кассы</a>
      {admin && date && (
        <DatePicker
          {...date}
          required
          aria-label="Дата внутри периода"
          onChange={(value) => iframe.current?.contentWindow?.postMessage({ type: "kassa-report-set-date", value }, "*")}
        />
      )}
      {admin && <span className="rounded-full border border-ink-600 px-3 py-1 text-mute-soft">{mode === "live" ? "Боевой срез" : mode === "review" ? "Сверка не закрыта" : "Прототип · демоданные"}</span>}
    </div>
    {!admin ? <p role="status">Эта страница доступна только администраторам.</p>
      : error ? <div role="alert" className="card p-4">{error} <button className="ml-2 underline" onClick={() => setAttempt((n) => n + 1)}>Повторить</button></div>
      : html ? <iframe ref={iframe} title="Аналитика CRM и логистики" sandbox="allow-scripts allow-popups allow-popups-to-escape-sandbox" srcDoc={html}
          className="block w-full rounded-xl border-0" />
      : <p role="status">Загружаем аналитику…</p>}
  </section>;
}
