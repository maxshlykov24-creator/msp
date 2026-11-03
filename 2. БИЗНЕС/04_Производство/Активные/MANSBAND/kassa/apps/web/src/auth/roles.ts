import { USE_MOCK } from "../api/client";

/** Финансовые очереди доступны только по назначенной роли. */
export function canSeeFinanceQueues(role?: string): boolean {
  return USE_MOCK || ["finance", "rop", "admin"].includes(role ?? "");
}
