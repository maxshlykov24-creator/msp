import argon2 from "argon2";
import { eq } from "drizzle-orm";
import { db } from "../db/index.js";
import { users } from "../db/schema.js";
import type { User } from "@kassa/shared";

function toUser(r: typeof users.$inferSelect): User {
  return {
    id: r.id,
    login: r.login,
    name: r.name,
    role: r.role as User["role"],
    mustChangePassword: r.mustChangePassword,
  };
}

export async function verifyCredentials(login: string, password: string): Promise<User | null> {
  const rows = await db.select().from(users).where(eq(users.login, login)).limit(1);
  const u = rows[0];
  if (!u) return null;
  const ok = await argon2.verify(u.passwordHash, password).catch(() => false);
  return ok ? toUser(u) : null;
}

export async function getUserById(id: string): Promise<User | null> {
  const rows = await db.select().from(users).where(eq(users.id, id)).limit(1);
  return rows[0] ? toUser(rows[0]) : null;
}

export async function listUsers(): Promise<User[]> {
  const rows = await db.select().from(users).orderBy(users.name);
  return rows.map(toUser);
}

export async function updateUserRole(id: string, role: User["role"]): Promise<User | null> {
  const [row] = await db.update(users).set({ role }).where(eq(users.id, id)).returning();
  return row ? toUser(row) : null;
}

/**
 * Создание пользователя из интерфейса (созвон 20.08, п.7). Логин уникален;
 * пароль временный — при первом входе система требует его сменить.
 */
export async function createUser(input: {
  login: string;
  name: string;
  password: string;
  role: User["role"];
}): Promise<{ user?: User; error?: string }> {
  const existing = await db.select({ id: users.id }).from(users).where(eq(users.login, input.login)).limit(1);
  if (existing[0]) return { error: `Логин «${input.login}» уже занят` };
  const passwordHash = await argon2.hash(input.password);
  const [row] = await db
    .insert(users)
    .values({
      login: input.login,
      name: input.name,
      passwordHash,
      role: input.role,
      mustChangePassword: true,
    })
    .returning();
  if (!row) return { error: "Не удалось создать пользователя" };
  return { user: toUser(row) };
}

export async function changePassword(
  userId: string,
  currentPassword: string,
  newPassword: string
): Promise<{ ok: boolean; error?: string }> {
  const rows = await db.select().from(users).where(eq(users.id, userId)).limit(1);
  const u = rows[0];
  if (!u) return { ok: false, error: "Пользователь не найден" };
  const ok = await argon2.verify(u.passwordHash, currentPassword).catch(() => false);
  if (!ok) return { ok: false, error: "Неверный текущий пароль" };
  const hash = await argon2.hash(newPassword);
  await db.update(users).set({ passwordHash: hash, mustChangePassword: false }).where(eq(users.id, userId));
  return { ok: true };
}

export async function hashPassword(password: string): Promise<string> {
  return argon2.hash(password);
}
