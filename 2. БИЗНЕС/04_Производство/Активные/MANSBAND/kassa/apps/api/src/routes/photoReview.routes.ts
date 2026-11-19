import type { FastifyInstance, FastifyReply, FastifyRequest } from "fastify";
import type { PhotoReviewComment, PhotoReviewData } from "@kassa/shared";
import { eq, like } from "drizzle-orm";
import { z } from "zod";
import manifest from "../data/catalogPhotoReview.json" with { type: "json" };
import { db } from "../db/index.js";
import { appSettings } from "../db/schema.js";
import { getUserById } from "../services/auth.js";

const review: Omit<PhotoReviewData, "comments"> = manifest;
const commentPrefix = "catalog-photo-review:comment:";
const targets = new Set([
  ...review.cases.map(row => `case:${row.id}`),
  ...review.unpublished.map(row => `photo:${row.id}`),
]);
const commentSchema = z.object({
  id: z.string().uuid(),
  target: z.string().refine(value => targets.has(value)),
  text: z.string().trim().min(1).max(1600),
});

async function reviewer(req: FastifyRequest, reply: FastifyReply) {
  reply.header("Cache-Control", "private, no-store");
  const user = await getUserById(req.user.sub);
  if (!user || user.role !== "admin" || user.mustChangePassword) {
    return reply.code(403).send({ message: "Проверка фото доступна администраторам кассы" });
  }
}

export default async function photoReviewRoutes(app: FastifyInstance) {
  const access = { preHandler: [app.requireAdmin, reviewer] };
  app.get("/catalog/photo-review", access, async (): Promise<PhotoReviewData> => {
    const rows = await db.select({ value: appSettings.value }).from(appSettings)
      .where(like(appSettings.key, `${commentPrefix}%`))
      .orderBy(appSettings.updatedAt);
    return { ...review, comments: rows.map(row => row.value as PhotoReviewComment) };
  });

  app.post("/catalog/photo-review/comments", access, async (req, reply) => {
    const parsed = commentSchema.safeParse(req.body);
    if (!parsed.success) {
      return reply.code(400).send({ message: "Выбери позицию или фото и введи комментарий до 1600 символов" });
    }
    const { id, target, text } = parsed.data;
    const key = `${commentPrefix}${target}:${id}`;
    const comment: PhotoReviewComment = { id, target, text, author: req.user.name, at: new Date().toISOString() };
    const [inserted] = await db.insert(appSettings)
      .values({ key, value: comment, updatedBy: req.user.sub })
      .onConflictDoNothing()
      .returning({ value: appSettings.value });
    if (inserted) return reply.code(201).send(inserted.value);
    const [existing] = await db.select({ value: appSettings.value, authorId: appSettings.updatedBy })
      .from(appSettings).where(eq(appSettings.key, key));
    if (existing?.authorId === req.user.sub && (existing.value as PhotoReviewComment).text === text) {
      return existing.value;
    }
    return reply.code(409).send({ message: "Комментарий с таким номером уже сохранён" });
  });
}
