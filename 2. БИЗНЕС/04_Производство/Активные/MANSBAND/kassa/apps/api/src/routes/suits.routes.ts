import type { FastifyInstance } from "fastify";
import { z } from "zod";
import { getUserById } from "../services/auth.js";
import { halfSetList, suitCompleteness } from "../services/suitSets.js";
import { cartWarnings, listSuitBreaks, snapshotSuitBreaks } from "../services/suitBreaks.js";
import { stockStats } from "../services/stockStats.js";

const querySchema = z.object({
  warehouse: z.string().trim().max(120).optional(),
  q: z.string().trim().max(120).optional(),
  limit: z.coerce.number().int().min(1).max(500).optional(),
});

const day = z.string().regex(/^\d{4}-\d{2}-\d{2}$/);

export default async function suitsRoutes(app: FastifyInstance) {
  /**
   * Консультант видит комплектность своего магазина: ROP и админ смотрят любой
   * склад, продавцу чужие остатки в работе не нужны.
   */
  async function scopeFor(userId: string, role: string, warehouse?: string) {
    if (role !== "consultant") return { warehouse };
    const user = await getUserById(userId);
    return { store: user?.store, warehouse: undefined };
  }

  app.get("/suits/completeness", { preHandler: [app.authenticate] }, async (req, reply) => {
    const parsed = querySchema.safeParse(req.query);
    if (!parsed.success) return reply.code(400).send({ message: "Некорректный фильтр" });
    const scope = await scopeFor(req.user.sub, req.user.role, parsed.data.warehouse);
    return suitCompleteness({ ...scope, q: parsed.data.q, limit: parsed.data.limit });
  });

  app.get("/suits/half-sets", { preHandler: [app.authenticate] }, async (req, reply) => {
    const parsed = querySchema.safeParse(req.query);
    if (!parsed.success) return reply.code(400).send({ message: "Некорректный фильтр" });
    const scope = await scopeFor(req.user.sub, req.user.role, parsed.data.warehouse);
    return { rows: await halfSetList({ ...scope, q: parsed.data.q }) };
  });

  // Предупреждение в чеке до продажи: пиджак уходит без брюк своего размера.
  app.post("/suits/cart-check", { preHandler: [app.authenticate] }, async (req, reply) => {
    const parsed = z
      .object({
        store: z.string().trim().max(120).optional(),
        items: z
          .array(z.object({ productId: z.string().min(1), qty: z.number().int().min(0) }))
          .max(200),
      })
      .safeParse(req.body);
    if (!parsed.success) return reply.code(400).send({ message: "Некорректный чек" });
    return cartWarnings(parsed.data);
  });

  app.get("/suits/breaks", { preHandler: [app.authenticate] }, async (req, reply) => {
    const parsed = z
      .object({
        from: day.optional(),
        to: day.optional(),
        consultant: z.string().trim().max(120).optional(),
      })
      .safeParse(req.query);
    if (!parsed.success) return reply.code(400).send({ message: "Некорректный период" });
    // Консультант видит только свои разбитые костюмы, остальные — все.
    const consultant =
      req.user.role === "consultant" ? req.user.name : parsed.data.consultant;
    return { rows: await listSuitBreaks({ ...parsed.data, consultant }) };
  });

  // Ночной снимок комплектности: ловит разбиение, прошедшее не через кассу.
  app.post(
    "/suits/breaks/snapshot",
    { preHandler: [app.requireRoles(["admin", "rop"])] },
    async () => snapshotSuitBreaks()
  );

  app.get("/suits/stock-stats", { preHandler: [app.authenticate] }, async (req, reply) => {
    const parsed = querySchema.safeParse(req.query);
    if (!parsed.success) return reply.code(400).send({ message: "Некорректный фильтр" });
    const scope = await scopeFor(req.user.sub, req.user.role, parsed.data.warehouse);
    return stockStats(scope);
  });
}
