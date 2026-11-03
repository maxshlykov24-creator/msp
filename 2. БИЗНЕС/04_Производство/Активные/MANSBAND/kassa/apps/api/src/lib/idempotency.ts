import { AsyncLocalStorage } from "node:async_hooks";
import postgres from "postgres";
import { getEnv } from "../env.js";
import { eq } from "drizzle-orm";
import { db, sql } from "../db/index.js";
import { idempotencyKeys } from "../db/schema.js";
import { HttpError } from "./http.js";

// Длинная внешняя операция не должна занимать соединение пула бизнес-запросов.
// Вложенные блокировки используют ту же сессию, поэтому не исчерпывают пул.
const lockSql = postgres(getEnv().DATABASE_URL, {max:8,idle_timeout:1,connect_timeout:10});
type LockContext = { connection: Awaited<ReturnType<typeof sql.reserve>>; keys: Set<string> };
const lockContext = new AsyncLocalStorage<LockContext>();

/** Конфликт возвращаем сразу; один запрос удерживает одну отдельную lock-сессию. */
export async function withOperationLock<T>(key: string, fn: () => Promise<T>): Promise<T> {
  const current = lockContext.getStore();
  if (!current) {
    const connection = await lockSql.reserve();
    try { return await lockContext.run({connection,keys:new Set()}, () => withOperationLock(key,fn)); }
    finally { connection.release(); }
  }
  if(current.keys.has(key))throw Object.assign(new Error("Операция уже выполняется в этом запросе"),{statusCode:409});
  const {connection}=current;
  let acquired=false;
  current.keys.add(key);
  try {
    const [row]=await connection`select pg_try_advisory_lock(hashtextextended(${key},714)) as acquired`;
    acquired=Boolean(row?.acquired);
    if(!acquired)throw Object.assign(new Error("Операция уже выполняется. Обновите заявку перед повтором."),{statusCode:409});
    return await fn();
  } finally {
    current.keys.delete(key);
    if(acquired){await connection`select pg_advisory_unlock(hashtextextended(${key},714))`;}
  }
}

export async function closeOperationLocks(): Promise<void> { await lockSql.end(); }

/** Внешнее действие: подтверждённый результат сохраняется отдельно от всей продажи.
 * После неизвестного исхода автоматический повтор запрещён до сверки документа.
 */
export async function withIdempotency<T>(key: string, scope: string, fn: () => Promise<T>): Promise<{ result: T; reused: boolean }> {
  return withOperationLock(key, async () => {
    const [existing] = await db.select().from(idempotencyKeys).where(eq(idempotencyKeys.key, key)).limit(1);
    const result = existing?.result as { __operation?: string } | null;
    if (result && !result.__operation) return { result: result as T, reused: true };
    if (result?.__operation === "started" || result?.__operation === "unknown") {
      throw Object.assign(new Error("Результат внешней операции неизвестен. Нужна сверка перед повтором: " + key), { statusCode: 409 });
    }
    const save = async (value: object) => db.insert(idempotencyKeys).values({ key, scope, result: value })
      .onConflictDoUpdate({ target: idempotencyKeys.key, set: { result: value } });
    await save({ __operation: "started" });
    try {
      const value = await fn();
      await save(value as object);
      return { result: value, reused: false };
    } catch (error) {
      const rejected = error instanceof HttpError && error.status >= 400 && error.status < 500 && error.status !== 408;
      await save({ __operation: rejected ? "rejected" : "unknown" });
      throw error;
    }
  });
}
