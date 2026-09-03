// Тонкий типизированный HTTP-клиент к бэкенду кассы.
// База берётся из VITE_API_BASE (по умолчанию относительный /api — проксируется nginx).

const rawApi = import.meta.env.VITE_API_BASE as string | undefined;
const API_BASE = rawApi && rawApi.trim() ? rawApi.trim() : "/api";

const TOKEN_KEY = "kassa_token";
/** Обычные запросы. */
const REQUEST_TIMEOUT_MS = 45_000;
/** Проверка сессии — быстро падаем на экран входа. */
const ME_TIMEOUT_MS = 10_000;
/** Логин на Safari/LTE: первый POST иногда «висит» до abort. */
const LOGIN_TIMEOUT_MS = 45_000;
/** Список заявок. */
const DEALS_TIMEOUT_MS = 20_000;

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string | null) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    // private mode / квота — игнорируем
  }
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

function isAuthPath(path: string): boolean {
  return path.startsWith("/auth/");
}

function timeoutFor(method: string, path: string): number {
  if (path === "/auth/login" && method === "POST") return LOGIN_TIMEOUT_MS;
  if (path === "/auth/me") return ME_TIMEOUT_MS;
  if (isAuthPath(path)) return ME_TIMEOUT_MS;
  if (path === "/deals" || path.startsWith("/deals?")) return DEALS_TIMEOUT_MS;
  return REQUEST_TIMEOUT_MS;
}

async function requestOnce<T>(
  method: string,
  path: string,
  body: unknown | undefined,
  timeoutMs: number
): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);

  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: ctrl.signal,
      cache: "no-store",
      credentials: "same-origin",
    });
  } catch (err) {
    clearTimeout(timer);
    const name = err instanceof Error ? err.name : "";
    if (name === "AbortError") {
      throw new ApiError(408, "Сервер не ответил вовремя. Проверьте сеть и попробуйте снова.");
    }
    throw new ApiError(0, "Нет связи с сервером");
  } finally {
    clearTimeout(timer);
  }

  const text = await res.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }

  if (res.status === 401) {
    // /auth/login — оставляем текст «Неверный логин…».
    // /auth/me — реальная потеря сессии → выход.
    // Остальные 401 не затирают токен: иначе один сбойный POST (генерация
    // номера и т.п.) убивает сессию, а UI остаётся «как будто залогинен».
    if (path === "/auth/me") {
      setToken(null);
      try {
        window.dispatchEvent(new CustomEvent("kassa:unauthorized"));
      } catch {
        // SSR / нет window
      }
    }
    const msg =
      data && typeof data === "object" && "message" in data
        ? String((data as { message?: string }).message)
        : "Не авторизован";
    throw new ApiError(401, msg || "Не авторизован");
  }

  if (!res.ok) {
    const msg =
      (data && typeof data === "object" && ("message" in data || "error" in data)
        ? String(
            (data as { message?: string; error?: string }).message ||
              (data as { error?: string }).error
          )
        : null) || `Ошибка ${res.status}`;
    throw new ApiError(res.status, msg);
  }
  return data as T;
}

/** Прогрев соединения перед логином (Safari/LTE). */
export async function warmupApi(): Promise<void> {
  try {
    await requestOnce("GET", "/health", undefined, 8_000);
  } catch {
    // игнорируем — логин всё равно попробуем
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const timeoutMs = timeoutFor(method, path);
  const maxAttempts =
    method === "POST" && path === "/auth/login" ? 3 : method === "GET" && path === "/auth/me" ? 1 : 1;

  let lastErr: unknown;
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    try {
      return await requestOnce<T>(method, path, body, timeoutMs);
    } catch (err) {
      lastErr = err;
      const retryable =
        err instanceof ApiError && (err.status === 408 || err.status === 0);
      if (!retryable || attempt >= maxAttempts) break;
      await new Promise((r) => setTimeout(r, 500 * attempt));
    }
  }
  throw lastErr;
}

/** Собирает query без `undefined`, пустых строк и ручной конкатенации URL. */
export function apiPath(
  path: string,
  params: Record<string, string | number | boolean | null | undefined>
): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    query.set(key, String(value));
  }
  const suffix = query.toString();
  return suffix ? `${path}?${suffix}` : path;
}

/** Бинарный ответ (PDF бирки и т.п.) — мимо JSON-парсинга. */
export async function apiBlob(method: string, path: string, body?: unknown): Promise<Blob> {
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
    cache: "no-store",
    credentials: "same-origin",
  });
  if (!res.ok) {
    let msg = `Ошибка ${res.status}`;
    try {
      const data = JSON.parse(await res.text()) as { message?: string };
      if (data?.message) msg = data.message;
    } catch {
      // не JSON — оставляем статус
    }
    throw new ApiError(res.status, msg);
  }
  return res.blob();
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  getQuery: <T>(
    path: string,
    params: Record<string, string | number | boolean | null | undefined>
  ) => request<T>("GET", apiPath(path, params)),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  put: <T>(path: string, body?: unknown) => request<T>("PUT", path, body),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, body),
  del: <T>(path: string) => request<T>("DELETE", path),
};

export const USE_MOCK = (import.meta.env.VITE_USE_MOCK as string | undefined) === "1";
