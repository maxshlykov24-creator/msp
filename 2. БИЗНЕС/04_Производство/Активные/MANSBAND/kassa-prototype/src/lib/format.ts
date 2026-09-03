/**
 * Форматирует строку в маску телефона +7 (XXX) XXX-XX-XX.
 * Принимает произвольный ввод: цифры, пробелы, дефисы, плюс.
 * 8→7 замена на лету.
 *
 * Ключевое правило backspace:
 * Разделители ("), " и "-") добавляются ТОЛЬКО когда предыдущая группа заполнена
 * целиком — иначе при удалении форматтер перерисовывал бы символ обратно.
 * rest.length <= N → ещё не вся группа → не добавляем разделитель.
 */
export function formatPhone(raw: string): string {
  let digits = raw.replace(/\D/g, "");
  if (digits.startsWith("8")) digits = "7" + digits.slice(1);
  if (digits.length > 0 && !digits.startsWith("7")) digits = "7" + digits;
  digits = digits.slice(0, 11);
  if (digits.length === 0) return "";
  const rest = digits.slice(1); // 10 цифр после ведущей "7"
  let out = "+7";
  if (rest.length === 0) return out;
  out += " (" + rest.slice(0, Math.min(3, rest.length));
  if (rest.length <= 3) return out;          // группа кода неполная → стоп
  out += ") " + rest.slice(3, Math.min(6, rest.length));
  if (rest.length <= 6) return out;          // первая тройка неполная → стоп
  out += "-" + rest.slice(6, Math.min(8, rest.length));
  if (rest.length <= 8) return out;          // первая пара неполная → стоп
  out += "-" + rest.slice(8, 10);
  return out;
}

/**
 * Приводит отформатированный номер к виду для передачи в API/amoCRM: +7XXXXXXXXXX.
 * Пример: "+7 (925) 123-45-67" → "+79251234567"
 */
export function phoneToApi(formatted: string): string {
  const digits = formatted.replace(/\D/g, "");
  if (digits.length < 11) return formatted;
  return "+" + digits;
}

/** Нормализует строку: ё→е, нижний регистр — для ё/е-поиска. */
export function normalizeSearch(s: string): string {
  return s.toLowerCase().replace(/ё/g, "е");
}

const WD = ["вс", "пн", "вт", "ср", "чт", "пт", "сб"] as const;
const MO = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"] as const;

function dateParts(d: Date) {
  return {
    day: d.getDate(),
    mon: MO[d.getMonth()],
    wd: WD[d.getDay()],
    year: d.getFullYear(),
  };
}

/** Парсинг YYYY-MM-DD и ISO-datetime без сдвига по TZ. */
function parseInputDate(iso: string): Date {
  const datePart = iso.slice(0, 10);
  if (/^\d{4}-\d{2}-\d{2}$/.test(datePart)) {
    const [y, m, d] = datePart.split("-").map(Number);
    return new Date(y, m - 1, d);
  }
  return new Date(iso);
}

function needsYear(d: Date): boolean {
  return d.getFullYear() !== new Date().getFullYear();
}

/** Компактная дата: «6 июн (сб)», год — если не текущий. */
export function dateCompact(iso: string): string {
  const d = parseInputDate(iso);
  const { day, mon, wd, year } = dateParts(d);
  const core = needsYear(d) ? `${day} ${mon} ${year}` : `${day} ${mon}`;
  return `${core} (${wd})`;
}

/** Компактный диапазон: «6–9 июн (сб–вт)» или «30 июн (пн) → 4 июл (пт)». */
export function dateRangeCompact(from: string, to?: string): string {
  if (!to) return dateCompact(from);
  const a = parseInputDate(from);
  const b = parseInputDate(to);
  const pa = dateParts(a);
  const pb = dateParts(b);
  const sameMonth = a.getMonth() === b.getMonth() && a.getFullYear() === b.getFullYear();

  if (sameMonth) {
    const yearSuffix = needsYear(a) ? ` ${pa.year}` : "";
    return `${pa.day}–${pb.day} ${pa.mon}${yearSuffix} (${pa.wd}–${pb.wd})`;
  }

  return `${dateCompact(from)} → ${dateCompact(to)}`;
}

export function moneyPlain(n: number): string {
  const sign = n < 0 ? "−" : "";
  const v = Math.abs(Math.round(n));
  return sign + v.toLocaleString("ru-RU");
}

export function money(n: number): string {
  return moneyPlain(n) + " ₽";
}

export function shortDate(iso: string): string {
  return dateCompact(iso);
}

export function timeOf(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
}

/** Дата (+ время для ISO-datetime): «6 июн (сб), 11:10». */
export function dateRu(iso: string): string {
  if (iso.includes("T")) {
    return `${dateCompact(iso)}, ${timeOf(iso)}`;
  }
  return dateCompact(iso);
}
