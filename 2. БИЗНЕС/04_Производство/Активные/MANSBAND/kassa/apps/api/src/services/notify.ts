import { getEnv } from "../env.js";
import type { Deal } from "@kassa/shared";

// Уведомления о продажах в Telegram-группы (Бауманская/Пятницкая), урезанная инфа.
// Если токен/группы не заданы — тихо пропускаем (Фаза 4 — опционально на старте).

const env = getEnv();

function groupForStore(store: string): string | undefined {
  if (store.includes("Бауман")) return env.TELEGRAM_GROUP_BAUMANSKAYA;
  if (store.includes("Пятниц")) return env.TELEGRAM_GROUP_PYATNITSKAYA;
  return env.TELEGRAM_GROUP_BAUMANSKAYA;
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

export async function notifySale(deal: Deal): Promise<void> {
  const chat = groupForStore(deal.store);
  if (!chat) return;
  const text =
    `🟢 <b>Продажа #${deal.number}</b>\n` +
    `Шоурум: ${deal.store}\n` +
    `Консультант: ${deal.consultant || "—"}\n` +
    `Сумма: ${deal.total.toLocaleString("ru-RU")} ₽`;
  await send(chat, text);
}
