// Нормализация телефонов РФ для поиска/сопоставления контактов amoCRM.

export function normalizePhone(raw: string | undefined | null): string {
  if (!raw) return "";
  let digits = raw.replace(/\D/g, "");
  if (digits.length === 11 && digits.startsWith("8")) digits = "7" + digits.slice(1);
  if (digits.length === 10) digits = "7" + digits;
  return digits;
}

// Последние 10 цифр — устойчивый ключ сравнения (без кода страны).
export function phoneTail(raw: string | undefined | null): string {
  const n = normalizePhone(raw);
  return n.length >= 10 ? n.slice(-10) : n;
}

export function formatPhoneE164(raw: string): string {
  const n = normalizePhone(raw);
  return n ? `+${n}` : "";
}
