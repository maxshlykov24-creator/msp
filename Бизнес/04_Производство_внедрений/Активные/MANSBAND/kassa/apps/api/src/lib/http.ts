// HTTP с ограничением частоты (rate-limit) и ретраями на 429/5xx.
// Используется amoClient и msClient. Один лимитер на каждый внешний API.

interface RateLimiterOptions {
  rps: number; // запросов в секунду
}

class RateLimiter {
  private queue: Array<() => void> = [];
  private tokens: number;
  private readonly rps: number;
  private timer: NodeJS.Timeout | null = null;

  constructor(opts: RateLimiterOptions) {
    this.rps = opts.rps;
    this.tokens = opts.rps;
  }

  private ensureTimer() {
    if (this.timer) return;
    this.timer = setInterval(() => {
      this.tokens = this.rps;
      while (this.tokens > 0 && this.queue.length > 0) {
        this.tokens--;
        const next = this.queue.shift();
        next?.();
      }
      if (this.queue.length === 0 && this.timer) {
        clearInterval(this.timer);
        this.timer = null;
      }
    }, 1000);
  }

  acquire(): Promise<void> {
    return new Promise((resolve) => {
      if (this.tokens > 0) {
        this.tokens--;
        resolve();
        return;
      }
      this.queue.push(resolve);
      this.ensureTimer();
    });
  }
}

export interface HttpClientOptions {
  baseUrl: string;
  headers: Record<string, string>;
  rps?: number;
  maxRetries?: number;
}

export class HttpError extends Error {
  status: number;
  body: unknown;
  constructor(status: number, message: string, body: unknown) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export class HttpClient {
  private readonly limiter: RateLimiter;
  private readonly maxRetries: number;

  constructor(private readonly opts: HttpClientOptions) {
    this.limiter = new RateLimiter({ rps: opts.rps ?? 5 });
    this.maxRetries = opts.maxRetries ?? 4;
  }

  async request<T>(
    method: string,
    path: string,
    body?: unknown,
    extraHeaders?: Record<string, string>
  ): Promise<T> {
    const url = path.startsWith("http") ? path : `${this.opts.baseUrl}${path}`;
    let attempt = 0;

    while (true) {
      await this.limiter.acquire();
      const res = await fetch(url, {
        method,
        headers: { ...this.opts.headers, ...extraHeaders },
        body: body !== undefined ? JSON.stringify(body) : undefined,
      });

      if (res.status === 429 || res.status >= 500) {
        if (attempt < this.maxRetries) {
          const retryAfter = Number(res.headers.get("Retry-After"));
          const backoff = Number.isFinite(retryAfter) && retryAfter > 0
            ? retryAfter * 1000
            : Math.min(2 ** attempt * 500, 8000);
          attempt++;
          await sleep(backoff);
          continue;
        }
      }

      const text = await res.text();
      const data = text ? safeJson(text) : null;

      if (!res.ok) {
        throw new HttpError(res.status, `${method} ${url} → ${res.status}`, data);
      }
      return data as T;
    }
  }

  get<T>(path: string, extraHeaders?: Record<string, string>) {
    return this.request<T>("GET", path, undefined, extraHeaders);
  }
  post<T>(path: string, body?: unknown, extraHeaders?: Record<string, string>) {
    return this.request<T>("POST", path, body, extraHeaders);
  }
  put<T>(path: string, body?: unknown, extraHeaders?: Record<string, string>) {
    return this.request<T>("PUT", path, body, extraHeaders);
  }
  patch<T>(path: string, body?: unknown, extraHeaders?: Record<string, string>) {
    return this.request<T>("PATCH", path, body, extraHeaders);
  }
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}
