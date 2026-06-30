// Деньги храним и считаем в копейках (целые), чтобы не накапливать ошибки float.
// Рубли используем только на границах ввода/вывода.

export function toKopecks(rubles: number): number {
  return Math.round(rubles * 100);
}

export function toRubles(kopecks: number): number {
  return Math.round(kopecks) / 100;
}

export function sumPayments(payments: Array<{ amount: number }>): number {
  // amount приходит в рублях из UI → суммируем в копейках
  return payments.reduce((acc, p) => acc + toKopecks(p.amount), 0);
}
