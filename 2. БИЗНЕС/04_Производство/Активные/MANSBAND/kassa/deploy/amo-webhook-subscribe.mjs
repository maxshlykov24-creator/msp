#!/usr/bin/env node
// Подписка amoCRM только на смену этапа. По умолчанию ничего не создаёт.
// Секрет и полный адрес в вывод не попадают. Запуск с --apply создаёт подписку.

export function isKassaStatusWebhook(destination) {
  try {
    const url = new URL(destination);
    const host = url.hostname.toLowerCase();
    return (host === "mansband-kassa.ru" || host === "www.mansband-kassa.ru")
      && url.pathname.includes("/api/webhooks/amo");
  } catch {
    return false;
  }
}

export function publicWebhookView(destination, events = []) {
  const url = new URL(destination);
  return { host: url.host, path: url.pathname, events };
}

function webhookRows(payload) {
  const rows = payload?._embedded?.webhooks;
  return Array.isArray(rows) ? rows : [];
}

async function main() {
  const apply = process.argv.includes("--apply");
  const base = process.env.AMOCRM_API_BASE?.replace(/\/$/, "");
  const token = process.env.AMOCRM_LONG_LIVED_TOKEN;
  const secret = process.env.WEBHOOK_SECRET;
  const publicBase = (process.env.PUBLIC_BASE_URL || "https://mansband-kassa.ru").replace(/\/$/, "");
  if (!base || !token || !secret) {
    console.error("Нужны AMOCRM_API_BASE, AMOCRM_LONG_LIVED_TOKEN и WEBHOOK_SECRET");
    process.exit(1);
  }
  if (secret === "change-me" || secret.length < 16) {
    console.error("Секрет вебхука не задан");
    process.exit(1);
  }
  const headers = { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
  const listed = await fetch(`${base}/webhooks`, { headers });
  const payload = await listed.json().catch(() => ({}));
  if (!listed.ok) {
    console.error(`Чтение подписок не удалось: ${listed.status}`);
    process.exit(1);
  }
  const rows = webhookRows(payload);
  for (const row of rows) {
    const destination = String(row.destination ?? "");
    if (!isKassaStatusWebhook(destination)) continue;
    const view = publicWebhookView(destination, row.settings ?? []);
    console.log(`Подписка кассы уже есть: ${view.host}${view.path} события=${view.events.join(",") || "нет"}`);
    process.exit(0);
  }
  if (!apply) {
    console.log(`Подписки кассы нет. Просмотрено ${rows.length}. Создание не выполнялось.`);
    process.exit(0);
  }
  const destination = `${publicBase}/api/webhooks/amo?secret=${encodeURIComponent(secret)}`;
  const created = await fetch(`${base}/webhooks`, {
    method: "POST",
    headers,
    body: JSON.stringify({ destination, settings: ["status_lead"] }),
  });
  const body = await created.json().catch(() => ({}));
  if (!created.ok) {
    console.error(`Подписка не создана: ${created.status}`);
    process.exit(1);
  }
  const view = publicWebhookView(String(body.destination ?? destination), body.settings ?? ["status_lead"]);
  console.log(`Создана подписка ${body.id ?? "без id"}: ${view.host}${view.path} события=${view.events.join(",")}`);
}

if (process.argv[1]?.endsWith("amo-webhook-subscribe.mjs") || process.env.KASSA_SUBSCRIBE === "1") {
  main().catch((error) => {
    console.error(error instanceof Error ? error.message : "Подписка не выполнена");
    process.exit(1);
  });
}
