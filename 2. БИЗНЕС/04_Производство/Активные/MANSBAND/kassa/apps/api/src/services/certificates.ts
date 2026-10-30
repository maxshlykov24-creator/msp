import { and, eq, gte, lte, or, isNull, sql } from "drizzle-orm";
import { canonicalNumber, newNumber } from "./certificateRegistry.js";
import { moscowTodayYmd } from "@kassa/shared";
import { db } from "../db/index.js";
import { certificates } from "../db/schema.js";
import type { Certificate } from "@kassa/shared";
import { broadcast } from "../ws/hub.js";

function toCert(r: typeof certificates.$inferSelect): Certificate {
  const expired = r.validUntil < moscowTodayYmd() && r.balance > 0;
  return {
    number: r.number,
    validFrom: r.validFrom ?? undefined,
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

export interface CertificateUsage {
  dealNumber: number;
  createdAt: string;
  paidAt: string | null;
  clientName: string;
  store: string;
  consultant: string;
  stage: string;
  amount: number;
}

/** Заявки, в оплате которых списан этот сертификат. Продажа самого сертификата сюда не входит. */
export async function listUsages(number: string): Promise<CertificateUsage[]> {
  const needle = number.trim();
  if (!needle) return [];
  const result = await db.execute(sql`
    SELECT d.number, d.stage, d.created_at, d.data
    FROM deals d
    WHERE EXISTS (
      SELECT 1
      FROM jsonb_array_elements(COALESCE(d.data->'payments', '[]'::jsonb)) p
      WHERE btrim(COALESCE(p->>'certificateNumber', '')) = ${needle}
    )
    ORDER BY d.created_at DESC
  `);
  const rawRows = (Array.isArray(result)
    ? result
    : ((result as { rows?: unknown[] }).rows ?? [])) as Array<Record<string, unknown>>;

  return rawRows.map((row) => {
    const data = row.data && typeof row.data === "object" ? (row.data as Record<string, unknown>) : {};
    const payments = Array.isArray(data.payments) ? data.payments : [];
    let amount = 0;
    let paidAt: string | null = null;
    for (const payment of payments) {
      if (!payment || typeof payment !== "object") continue;
      const line = payment as { certificateNumber?: unknown; amount?: unknown; paidAt?: unknown };
      if (String(line.certificateNumber ?? "").trim() !== needle) continue;
      const sum = Number(line.amount);
      if (Number.isFinite(sum) && sum > 0) amount += sum;
      if (typeof line.paidAt === "string" && line.paidAt.trim()) paidAt = line.paidAt.trim();
    }
    const created = row.created_at instanceof Date ? row.created_at.toISOString() : String(row.created_at ?? "");
    return {
      dealNumber: Number(row.number),
      createdAt: created,
      paidAt,
      clientName: typeof data.clientName === "string" ? data.clientName : "",
      store: typeof data.store === "string" ? data.store : "",
      consultant: typeof data.consultant === "string" ? data.consultant : "",
      stage: typeof row.stage === "string" ? row.stage : "",
      amount,
    };
  });
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
      eq(certificates.number, canonicalNumber(number)),
      eq(certificates.status, "active"),
      gte(certificates.balance, amount),
      gte(certificates.validUntil, moscowTodayYmd()),
      or(isNull(certificates.validFrom), lte(certificates.validFrom, moscowTodayYmd()))
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
          eq(certificates.number, canonicalNumber(redemption.number)),
          eq(certificates.status, "active"),
          gte(certificates.balance, amount),
          gte(certificates.validUntil, moscowTodayYmd()),
      or(isNull(certificates.validFrom), lte(certificates.validFrom, moscowTodayYmd()))
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
  const number=canonicalNumber(cert.number);
  const [existing]=await db.select().from(certificates).where(eq(certificates.number,number));
  if(existing){if(existing.buyerDealNumber!==cert.buyerDealNumber)throw new Error("Конфликт владельца сертификата");return;}
  if(!await issue(cert))throw new Error("Номер занят или требует сверки");
}

/** Выпуск: если номера ещё нет — создать с полным балансом; если есть — не трогать баланс. */
export async function issue(cert: Certificate, reviewedLegacy=false): Promise<boolean> {
  const number=canonicalNumber(cert.number);
  return db.transaction(async tx=>{
    const held=await tx.execute(sql`SELECT * FROM certificate_numbers WHERE number=${number} FOR UPDATE`);
    if(held.length && !(reviewedLegacy && held[0]!.state==='reserved'))return false;
    if(!held.length){const reserved=await tx.execute(sql`INSERT INTO certificate_numbers(number,state) VALUES (${number},'issued') ON CONFLICT DO NOTHING RETURNING number`);if(!reserved.length)return false;}
    const inserted=await tx.insert(certificates).values({number,nominal:Math.round(cert.nominal*100),balance:Math.round(cert.balance*100),status:cert.status,type:cert.type,guestName:cert.guestName,buyerDealNumber:cert.buyerDealNumber,issuedAt:cert.issuedAt,validUntil:cert.validUntil,validFrom:cert.validFrom??null}).onConflictDoNothing().returning({number:certificates.number});
    if(inserted.length)await tx.execute(sql`UPDATE certificate_numbers SET state='issued' WHERE number=${number}`);
    return Boolean(inserted.length);
  });
}

export async function generateDigitalNumber(): Promise<string> {
  for(let i=0;i<80;i++){const number=newNumber();const rows=await db.execute(sql`SELECT number FROM certificate_numbers WHERE number=${number}`);if(!rows.length)return number;}
  throw new Error("Не удалось подобрать свободный номер");
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
      if (await issue(row, true)) imported++;
    }
  }
  return { valid: rows.length, conflicts, errors, imported, dryRun };
}
