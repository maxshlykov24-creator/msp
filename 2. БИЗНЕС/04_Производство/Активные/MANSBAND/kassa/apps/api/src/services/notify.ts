import { getEnv } from "../env.js";
import type { Deal } from "@kassa/shared";

/**
 * Проброс записей в живые Telegram-группы магазинов (созвон 20.08, п.8).
 * Состав сообщения строго ограничен: имя, телефон, позиции, магазин.
 * Без источника рекламы и без цели покупки — условие Миши по партнёру.
 * События: создание заявки и проведение в «Успех» (сознательное сужение
 * от «всех записей», чтобы группа не превратилась в шум).
 *
 * Токен и chat_id групп — только в .env (TELEGRAM_BOT_TOKEN,
 * TELEGRAM_GROUP_BAUMANSKAYA, TELEGRAM_GROUP_PYATNITSKAYA). На боте висит
 * вебхук amoCRM Telegron — deleteWebhook/getUpdates не вызывать, sendMessage
 * работает при активном вебхуке.
 */

const env = getEnv();

function groupForStore(store: string): string | undefined {
  if (store.includes("Бауман")) return env.TELEGRAM_GROUP_BAUMANSKAYA;
  if (store.includes("Пятниц")) return env.TELEGRAM_GROUP_PYATNITSKAYA;
  return env.TELEGRAM_GROUP_BAUMANSKAYA;
}

function escapeHtml(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

async function send(chatId: string, text: string): Promise<void> {
  if (!env.TELEGRAM_BOT_TOKEN) return;
  try {
    await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat_id: chatId, text, parse_mode: "HTML" }),
    });
  } catch {
    // уведомления не критичны
  }
}

export type DealNotifyEvent = "created" | "success";

export async function notifyDeal(deal: Deal, event: DealNotifyEvent): Promise<void> {
  const chat = groupForStore(deal.store);
  if (!chat) return;

  const positions = deal.items
    .filter((i) => i.qty > 0)
    .map((i) => `· ${escapeHtml(i.name)}${i.qty > 1 ? ` ×${i.qty}` : ""}`)
    .join("\n");

  const header =
    event === "success" ? `🟢 <b>Продажа #${deal.number}</b>` : `🆕 <b>Заявка #${deal.number}</b>`;
  const lines = [
    header,
    `Магазин: ${escapeHtml(deal.store)}`,
    `Клиент: ${escapeHtml(deal.clientName || "—")}`,
    deal.clientPhone ? `Телефон: ${escapeHtml(deal.clientPhone)}` : null,
    positions ? `Позиции:\n${positions}` : null,
  ].filter(Boolean) as string[];

  await send(chat, lines.join("\n"));
}

/** @deprecated Совместимость: продажа = notifyDeal(deal, "success"). */
export async function notifySale(deal: Deal): Promise<void> {
  await notifyDeal(deal, "success");
}
