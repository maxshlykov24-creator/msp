/**
 * Короткий стабильный ключ для API (Zod max 128).
 * Не кладём сырые списки UUID — они легко превышают лимит.
 */
export function shortIdempotencyKey(prefix: string, ...parts: string[]): string {
  const raw = parts.join("|");
  let h1 = 2166136261;
  let h2 = 0;
  for (let i = 0; i < raw.length; i++) {
    const c = raw.charCodeAt(i);
    h1 ^= c;
    h1 = Math.imul(h1, 16777619);
    h2 = (Math.imul(31, h2) + c) | 0;
  }
  const fp =
    (h1 >>> 0).toString(16).padStart(8, "0") + (h2 >>> 0).toString(16).padStart(8, "0");
  const key = `${prefix}:${fp}`;
  return key.length <= 128 ? key : key.slice(0, 128);
}
