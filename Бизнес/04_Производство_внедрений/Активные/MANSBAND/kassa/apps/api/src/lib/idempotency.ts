import { eq } from "drizzle-orm";
import { db } from "../db/index.js";
import { idempotencyKeys } from "../db/schema.js";

// Идемпотентность: один и тот же ключ → один результат (защита от двойного
// проведения продажи/отгрузки/загрузки фото при ретраях и двойных кликах).

export async function withIdempotency<T>(
  key: string,
  scope: string,
  fn: () => Promise<T>
): Promise<{ result: T; reused: boolean }> {
  const existing = await db
    .select()
    .from(idempotencyKeys)
    .where(eq(idempotencyKeys.key, key))
    .limit(1);

  if (existing.length > 0 && existing[0]?.result != null) {
    return { result: existing[0].result as T, reused: true };
  }

  const result = await fn();

  await db
    .insert(idempotencyKeys)
    .values({ key, scope, result: result as object })
    .onConflictDoUpdate({
      target: idempotencyKeys.key,
      set: { result: result as object },
    });

  return { result, reused: false };
}
