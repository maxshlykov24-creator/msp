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
