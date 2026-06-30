import { eq } from "drizzle-orm";
import { db } from "../db/index.js";
import { certificates } from "../db/schema.js";
import type { Certificate } from "@kassa/shared";
import { broadcast } from "../ws/hub.js";

function toCert(r: typeof certificates.$inferSelect): Certificate {
  return {
    number: r.number,
    nominal: r.nominal / 100,
    balance: r.balance / 100,
    status: r.status as Certificate["status"],
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
  const rows = await db.select().from(certificates).where(eq(certificates.number, number)).limit(1);
  const cert = rows[0];
  if (!cert) return null;
  const amount = Math.round(amountRub * 100);
  const balance = Math.max(0, cert.balance - amount);
  const status = balance === 0 ? "used" : cert.status;
  await db.update(certificates).set({ balance, status }).where(eq(certificates.number, number));
  broadcast("certificate.updated", { number });
  const updated = await db.select().from(certificates).where(eq(certificates.number, number)).limit(1);
  return updated[0] ? toCert(updated[0]) : null;
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
        balance: Math.round(cert.balance * 100),
        status: cert.status,
        guestName: cert.guestName ?? null,
      },
    });
}
