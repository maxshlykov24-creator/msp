import { randomInt } from "node:crypto";
import { sql } from "drizzle-orm";
import { db } from "../db/index.js";
import { certificates } from "../db/schema.js";
import type { Certificate } from "@kassa/shared";

export const LEGACY_NUMBERS = ["412454","412456","412358","00873","761233","00728","410956","62267","849355","465534","00789","00881","00788","00894","00871","558901","00884","483775","823371","490086","00776"];
export function canonicalNumber(raw: string): string {
  const value=raw.trim();
  const compact=value.toUpperCase().replace(/[\s-]/g, "");
  if (/^MB\d{8}$/.test(compact)) return `MB-${compact.slice(2,6)}-${compact.slice(6)}`;
  return value;
}
export function newNumber(): string {
  const n=String(randomInt(10_000_000,100_000_000));return `MB-${n.slice(0,4)}-${n.slice(4)}`;
}
export async function initializeCertificateRegistry() {
  await db.execute(sql`ALTER TABLE certificates ADD COLUMN IF NOT EXISTS valid_from text`);
  await db.execute(sql`CREATE TABLE IF NOT EXISTS certificate_numbers (
    number text PRIMARY KEY, source_key text UNIQUE, state text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(), note text NOT NULL DEFAULT '')`);
  await db.execute(sql`INSERT INTO certificate_numbers(number,state,note)
    SELECT number,'issued','existing registry' FROM certificates ON CONFLICT(number) DO NOTHING`);
  for (const number of LEGACY_NUMBERS) await db.execute(sql`INSERT INTO certificate_numbers(number,state,note)
    VALUES (${number},'reserved','owner photo 2026-10-30; amount and dates require review') ON CONFLICT(number) DO NOTHING`);
}
export async function reservedNumbers(): Promise<Array<{number:string; note:string}>> {
  const rows=await db.execute(sql`SELECT number,note FROM certificate_numbers WHERE state='reserved' ORDER BY number`);
  return Array.from(rows) as unknown as Array<{number:string;note:string}>;
}
/** Cross-source issuance key is amo lead ID, or local UUID before CRM exists. */
export async function ensureCertificate(sourceKey:string, cert:Certificate, preferred?:string):Promise<Certificate> {
  return db.transaction(async tx=>{
    await tx.execute(sql`SELECT pg_advisory_xact_lock(hashtextextended(${sourceKey}, 0))`);
    const existing=await tx.execute(sql`SELECT number FROM certificate_numbers WHERE source_key=${sourceKey}`);
    let number=existing[0]?.number as string|undefined;
    if (!number && preferred?.trim()) {
      const requested=canonicalNumber(preferred);
      const held=await tx.execute(sql`SELECT * FROM certificate_numbers WHERE number=${requested} FOR UPDATE`);
      if (held.length) {
        const [financial]=await tx.select().from(certificates).where(sql`${certificates.number}=${requested}`);
        if (!financial || !cert.buyerDealNumber || financial.buyerDealNumber!==cert.buyerDealNumber || held[0].source_key) {
          throw new Error("Номер сертификата занят или требует сверки; новый автоматически не выдаётся");
        }
        await tx.execute(sql`UPDATE certificate_numbers SET source_key=${sourceKey} WHERE number=${requested}`);
        number=requested;
      } else {
        await tx.execute(sql`INSERT INTO certificate_numbers(number,source_key,state) VALUES (${requested},${sourceKey},'issued')`);number=requested;
      }
    }
    if (!number) {
      for(let i=0;i<50;i++) {
        const candidate=newNumber();const inserted=await tx.execute(sql`INSERT INTO certificate_numbers(number,source_key,state)
          VALUES (${candidate},${sourceKey},'issued') ON CONFLICT(number) DO NOTHING RETURNING number`);
        if(inserted.length){number=candidate;break;}
      }
    }
    if(!number)throw new Error("Не удалось закрепить номер сертификата");
    const [prior]=await tx.select().from(certificates).where(sql`${certificates.number}=${number}`);
    if(prior) return {number:prior.number,nominal:prior.nominal/100,balance:prior.balance/100,status:prior.status as Certificate["status"],type:prior.type as Certificate["type"],issuedAt:prior.issuedAt,validUntil:prior.validUntil,validFrom:prior.validFrom??undefined,buyerDealNumber:prior.buyerDealNumber??undefined};
    await tx.insert(certificates).values({number,nominal:Math.round(cert.nominal*100),balance:Math.round(cert.nominal*100),status:"active",type:cert.type,guestName:cert.guestName,buyerDealNumber:cert.buyerDealNumber,issuedAt:cert.issuedAt,validUntil:cert.validUntil,validFrom:cert.validFrom??cert.issuedAt});
    return {...cert,number};
  });
}
