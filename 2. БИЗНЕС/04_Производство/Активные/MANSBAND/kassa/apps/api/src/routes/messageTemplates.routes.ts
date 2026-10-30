import type { FastifyInstance } from "fastify";
import { getEnv } from "../env.js";

/** Server-only gateway: credentials and the bridge address never reach the browser. */
export default async function messageTemplatesRoutes(app: FastifyInstance) {
  const reads = ["", "/catalog", "/manual", "/journal", "/history/:id", "/gift/preview/:id"];
  const writes = ["/draft", "/check/:id", "/publish/:id", "/test/:id", "/gift/test", "/gift/retry-pdf/:id"];
  for (const suffix of [...reads, ...writes]) {
    const write = writes.includes(suffix);
    app.route({
      method: write ? "POST" : "GET",
      url: `/message-templates${suffix}`,
      preHandler: [write ? app.requireAdmin : app.authenticate],
      handler: async (req, reply) => {
        const env = getEnv();
        if (!env.MESSAGE_TEMPLATES_URL || !env.MESSAGE_TEMPLATES_KEY) {
          return reply.code(503).send({ message: "Сервис шаблонов ещё не подключён. Рассылки продолжают работать по прежним настройкам." });
        }
        const id = (req.params as { id?: string }).id;
        if (id && !/^[a-zA-Z0-9_-]{1,80}$/.test(id)) return reply.code(400).send({ message: "Некорректный идентификатор" });
        const path = suffix.replace(":id", encodeURIComponent(id ?? ""));
        const body = write ? { ...(req.body as Record<string, unknown> ?? {}), ...(suffix.startsWith("/check") ? {} : { actor: `${req.user.sub}: ${req.user.name}` }) } : undefined;
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 35_000);
        try {
          const res = await fetch(`${env.MESSAGE_TEMPLATES_URL.replace(/\/$/, "")}/internal/templates${path}`, {
            method: write ? "POST" : "GET", signal: controller.signal,
            headers: { "X-Reminder-Key": env.MESSAGE_TEMPLATES_KEY, "Content-Type": "application/json" },
            body: body ? JSON.stringify(body) : undefined,
          });
          if(suffix.startsWith("/gift/preview") && res.ok){
            reply.header("Content-Type","application/pdf");reply.header("Cache-Control","no-store");reply.header("Content-Disposition",'attachment; filename="Mansband-preview.pdf"');
            return reply.send(Buffer.from(await res.arrayBuffer()));
          }
          const data = await res.json() as Record<string, unknown>;
          if (!res.ok) return reply.code(res.status).send({ message: typeof data.detail === "string" ? data.detail : "Сервис шаблонов отклонил запрос" });
          return data;
        } catch {
          return reply.code(502).send({ message: write ? "Результат операции неизвестен. Обнови данные перед повторным действием." : "Нет связи с сервисом шаблонов" });
        } finally { clearTimeout(timer); }
      },
    });
  }
}
