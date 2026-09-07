import { USE_MOCK } from "../api/client";

/**
 * Денежные очереди (Эдвин, Миша) консультанту не показываем — решение созвона
 * 29.07.2026. Очереди консультанта и логиста видны всем: они подменяют друг друга.
 * Владелец (Максим) видит их для проверки, даже если роль в БД временно не admin.
 */
const FINANCE_ROLES = ["finance", "rop", "admin"];
const CLOSED_EDIT_ROLES = ["rop", "admin"];

function isMaxim(login?: string, name?: string): boolean {
  const who = `${login ?? ""} ${name ?? ""}`.toLowerCase();
  return who.includes("max") || who.includes("максим");
}

export function canSeeFinanceQueues(role?: string, login?: string, name?: string): boolean {
  if (USE_MOCK || FINANCE_ROLES.includes(role ?? "")) return true;
  return isMaxim(login, name);
}

/** Закрытые сделки (Успех/Провал) правят только РОП, admin и Максим. */
export function canEditClosedDeals(role?: string, login?: string, name?: string): boolean {
  if (USE_MOCK || CLOSED_EDIT_ROLES.includes(role ?? "")) return true;
  return isMaxim(login, name);
}
