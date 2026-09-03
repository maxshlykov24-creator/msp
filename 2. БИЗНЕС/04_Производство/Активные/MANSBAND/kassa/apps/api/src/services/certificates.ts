import { and, eq, gte, sql } from "drizzle-orm";
import { randomInt } from "node:crypto";
import { db } from "../db/index.js";
import { certificates } from "../db/schema.js";
import type { Certificate } from "@kassa/shared";
import { broadcast } from "../ws/hub.js";

function toCert(r: typeof certificates.$inferSelect): Certificate {
  const expired = r.validUntil < new Date().toISOString().slice(0, 10) && r.balance > 0;
  return {
    number: r.number,
    nominal: r.nominal / 100,
    balance: r.balance / 100,
    status: expired ? "expired" : r.status as Certificate["status"],
    type: r.type as Certificate["type"],
    guestName: r.guestName ?? undefined,
    buyerDealNumber: r.buyerDealNumber ?? undefined,
    issuedAt: r.issuedAt,
    validUntil: r.validUntil,
  };
}

export async function list(): Promise<Certificate[]> {
  const rows = await db.select().from(certificates);
  return rows.map(toCert);
}

export async function redeem(number: string, amountRub: number): Promise<Certificate | null> {
  const amount = Math.round(amountRub * 100);
  if (amount <= 0) return null;
  const [updated] = await db
    .update(certificates)
    .set({
      balance: sql`${certificates.balance} - ${amount}`,
      status: sql`case when ${certificates.balance} - ${amount} = 0 then 'used' else ${certificates.status} end`,
    })
    .where(and(
      eq(certificates.number, number),
      eq(certificates.status, "active"),
      gte(certificates.balance, amount),
      gte(certificates.validUntil, new Date().toISOString().slice(0, 10))
    ))
    .returning();
  if (!updated) return null;
  broadcast("certificate.updated", { number });
  return toCert(updated);
}

export async function redeemMany(
  redemptions: Array<{ number: string; amountRub: number }>
): Promise<Certificate[]> {
  return db.transaction(async (tx) => {
    const result: Certificate[] = [];
    for (const redemption of redemptions) {
      const amount = Math.round(redemption.amountRub * 100);
      if (amount <= 0) throw new Error(`Некорректная сумма сертификата №${redemption.number}`);
      const [updated] = await tx
        .update(certificates)
        .set({
          balance: sql`${certificates.balance} - ${amount}`,
          status: sql`case when ${certificates.balance} - ${amount} = 0 then 'used' else ${certificates.status} end`,
        })
        .where(and(
          eq(certificates.number, redemption.number),
          eq(certificates.status, "active"),
          gte(certificates.balance, amount),
          gte(certificates.validUntil, new Date().toISOString().slice(0, 10))
        ))
        .returning();
      if (!updated) {
        throw new Error(`Сертификат №${redemption.number} не найден, просрочен или имеет недостаточный остаток`);
      }
      result.push(toCert(updated));
    }
    return result;
  });
}

export async function upsert(cert: Certificate): Promise<void> {
  await db
    .insert(certificates)
    .values({
      number: cert.number,
      nominal: Math.round(cert.nominal * 100),
      balance: Math.round(cert.balance * 100),
      status: cert.status,
      type: cert.type,
      guestName: cert.guestName ?? null,
      buyerDealNumber: cert.buyerDealNumber ?? null,
      issuedAt: cert.issuedAt,
      validUntil: cert.validUntil,
    })
    .onConflictDoUpdate({
      target: certificates.number,
      set: {
        // Не трогаем balance/status — иначе повторный sync продажи обнулит списания.
        nominal: Math.round(cert.nominal * 100),
        type: cert.type,
        guestName: cert.guestName ?? null,
        buyerDealNumber: cert.buyerDealNumber ?? null,
        issuedAt: cert.issuedAt,
        validUntil: cert.validUntil,
      },
    });
}

/** Выпуск: если номера ещё нет — создать с полным балансом; если есть — не трогать баланс. */
export async function issue(cert: Certificate): Promise<boolean> {
  const [inserted] = await db
    .insert(certificates)
    .values({
      number: cert.number,
      nominal: Math.round(cert.nominal * 100),
      balance: Math.round(cert.balance * 100),
      status: cert.status,
      type: cert.type,
      guestName: cert.guestName ?? null,
      buyerDealNumber: cert.buyerDealNumber ?? null,
      issuedAt: cert.issuedAt,
      validUntil: cert.validUntil,
    })
    .onConflictDoNothing()
    .returning({ number: certificates.number });
  return Boolean(inserted);
}

export async function generateDigitalNumber(): Promise<string> {
  for (let attempt = 0; attempt < 80; attempt++) {
    const number = String(randomInt(100000, 1_000_000));
    const existing = await db
      .select({ number: certificates.number })
      .from(certificates)
      .where(eq(certificates.number, number))
      .limit(1);
    if (!existing[0]) return number;
  }
  // Крайний случай: последовательный перебор от случайной точки.
  const start = randomInt(100000, 1_000_000);
  for (let i = 0; i < 1000; i++) {
    const number = String(100000 + ((start - 100000 + i) % 900000));
    const existing = await db
      .select({ number: certificates.number })
      .from(certificates)
      .where(eq(certificates.number, number))
      .limit(1);
    if (!existing[0]) return number;
  }
  throw new Error("Не удалось подобрать свободный номер сертификата");
}

export interface CertificateImportPreview {
  valid: number;
  conflicts: string[];
  errors: Array<{ line: number; message: string }>;
  imported: number;
  dryRun: boolean;
}

function splitCsvLine(line: string, delimiter: "," | ";"): string[] {
  const out: string[] = [];
  let cell = "";
  let quoted = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i]!;
    if (ch === '"') {
      if (quoted && line[i + 1] === '"') { cell += '"'; i++; }
      else quoted = !quoted;
    } else if (ch === delimiter && !quoted) {
      out.push(cell.trim());
      cell = "";
    } else cell += ch;
  }
  out.push(cell.trim());
  return out;
}

export async function importCsv(csv: string, dryRun: boolean): Promise<CertificateImportPreview> {
  const { certificateImportRowSchema } = await import("@kassa/shared");
  const lines = csv.replace(/^\uFEFF/, "").split(/\r?\n/).filter((l) => l.trim());
  if (lines.length < 2) throw new Error("CSV должен содержать заголовок и данные");
  const delimiter = lines[0]!.includes(";") ? ";" : ",";
  const headerAliases: Record<string, string> = {
    номер: "number",
    номинал: "nominal",
    остаток: "balance",
    тип: "type",
    получатель: "guestName",
    "имя получателя": "guestName",
    "дата выдачи": "issuedAt",
    "действителен до": "validUntil",
    "срок действия": "validUntil",
  };
  const headers = splitCsvLine(lines[0]!, delimiter).map((h) => {
    const trimmed = h.trim();
    return headerAliases[trimmed.toLocaleLowerCase("ru")] ?? trimmed;
  });
  const rows: Certificate[] = [];
  const errors: Array<{ line: number; message: string }> = [];
  const seenNumbers = new Set<string>();
  for (let i = 1; i < lines.length; i++) {
    const values = splitCsvLine(lines[i]!, delimiter);
    const raw = Object.fromEntries(headers.map((h, idx) => [h, values[idx] ?? ""]));
    const parsed = certificateImportRowSchema.safeParse(raw);
    if (!parsed.success) {
      errors.push({ line: i + 1, message: parsed.error.issues[0]?.message ?? "Некорректная строка" });
      continue;
    }
    if (seenNumbers.has(parsed.data.number)) {
      errors.push({ line: i + 1, message: `Дубликат номера ${parsed.data.number} внутри CSV` });
      continue;
    }
    seenNumbers.add(parsed.data.number);
    rows.push({ ...parsed.data, status: parsed.data.balance > 0 ? "active" : "used" });
  }
  const existing = rows.length
    ? await db.select({ number: certificates.number }).from(certificates)
    : [];
  const requested = new Set(rows.map((r) => r.number));
  const conflicts = existing.filter((r) => requested.has(r.number)).map((r) => r.number);
  let imported = 0;
  if (!dryRun && errors.length === 0) {
    for (const row of rows) {
      const before = await db.select({ number: certificates.number }).from(certificates)
        .where(eq(certificates.number, row.number)).limit(1);
      if (before[0]) continue;
      if (await issue(row)) imported++;
    }
  }
  return { valid: rows.length, conflicts, errors, imported, dryRun };
}
