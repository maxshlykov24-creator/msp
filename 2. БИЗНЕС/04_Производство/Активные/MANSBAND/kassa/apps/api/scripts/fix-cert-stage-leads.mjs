/**
 * Сделки на «Сертификат оплачен/продан» без признаков сертификата → «Новая заявка».
 *
 * На сервере (после сборки dist):
 *   docker compose exec -T api node --input-type=module -e "$(cat scripts/...)" 
 * Удобнее — одноразовый eval уже выполнен 08.08; этот файл — для повтора из репо.
 *
 * Локально с env API:
 *   node --input-type=module apps/api/scripts/fix-cert-stage-leads.mjs
 *
 * DRY=1 — только отчёт. DAYS=7 — окно по created_at.
 */
import * as amo from "../dist/clients/amo.js";
import { AMO_PIPELINE_SALES } from "@kassa/shared";

const DAYS = Number(process.env.DAYS || 7);
const DRY = process.env.DRY === "1";
const CERT_NAMES = /сертификат\s*оплачен|сертификат\s*продан/i;

function normalize(name) {
  return name.toLowerCase().trim().replace(/ё/g, "е").replace(/\s+/g, " ");
}

function isCertRelated(lead) {
  const name = (lead.name || "").toLowerCase();
  if (/сертификат|\bcert\b/i.test(name)) return true;
  for (const f of lead.custom_fields_values || []) {
    const n = (f.field_name || "").toLowerCase();
    const v = String(f.values?.[0]?.value ?? "").trim();
    if (/сертификат|№ сертификата|certificate/i.test(n) && v) return true;
    if (/сертификат/i.test(v)) return true;
  }
  return false;
}

async function main() {
  const pipelines = await amo.getPipelines();
  const sales = pipelines.find((p) => p.id === AMO_PIPELINE_SALES);
  const statuses = sales?._embedded?.statuses || [];
  const certIds = statuses.filter((s) => CERT_NAMES.test(s.name)).map((s) => s.id);
  const newId = statuses.find((s) => normalize(s.name) === "новая заявка")?.id;
  if (!newId) throw new Error("Не найден этап «Новая заявка»");
  if (certIds.length === 0) throw new Error("Не найден этап сертификата");

  const from = Math.floor(Date.now() / 1000) - DAYS * 24 * 3600;
  const leads = await amo.listLeadsInPipelineStatuses(AMO_PIPELINE_SALES, certIds, {
    createdFrom: from,
  });
  console.log(`На этапе сертификата за ${DAYS} дн.: ${leads.length}`);

  const move = [];
  const keep = [];
  for (const l of leads) {
    const full = (await amo.getLead(l.id)) || l;
    if (isCertRelated(full)) keep.push(full);
    else move.push(full);
  }
  console.log(
    "Оставить:",
    keep.map((l) => `${l.id} ${l.name}`)
  );
  console.log(
    "Перенести:",
    move.map((l) => `${l.id} ${l.name}`)
  );
  if (DRY || move.length === 0) {
    console.log(DRY ? "DRY=1 — без изменений" : "Нечего переносить");
    return;
  }

  for (const l of move) {
    await amo.updateLead(l.id, { statusId: newId });
    await amo
      .addLeadNote(
        l.id,
        "Авто: перенесено с этапа сертификата → «Новая заявка» (нет признаков сертификата)."
      )
      .catch(() => {});
    console.log("moved", l.id);
  }
  console.log("Готово:", move.length);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
