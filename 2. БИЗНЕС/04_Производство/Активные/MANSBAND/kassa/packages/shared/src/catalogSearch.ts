// These same character tables are used by PostgreSQL translate() and the in-memory search.
export const SEARCH_PUNCTUATION = "ё‐‑‒–—−／";
export const SEARCH_PUNCTUATION_ASCII = "е------/";
export const CODE_CYRILLIC = "авекмнорстух";
export const CODE_LATIN = "abekmhopctyx";
export const CODE_SEPARATORS = " /-._:";

function translator(from: string, to: string) {
  const table = new Map([...from].map((ch, i) => [ch, to[i] ?? ""]));
  const chars = new RegExp(`[${from.replace(/[-\\\]^]/g, "\\$&")}]`, "g");
  return (value: string) => value.replace(chars, (ch) => table.get(ch)!);
}

const foldPunctuation = translator(SEARCH_PUNCTUATION, SEARCH_PUNCTUATION_ASCII);
const foldCodeLetters = translator(CODE_CYRILLIC, CODE_LATIN);
const stripCodeSeparators = translator(CODE_SEPARATORS, "");

export function catalogText(value: string): string {
  return foldPunctuation(value.toLowerCase());
}

// Customers say both «красная куртка» and «красные брюки». MS stores the colour as «красный».
const colors: Record<string, string> = {
  бел: "белый", черн: "черный", красн: "красный", сер: "серый", син: "синий",
  зелен: "зеленый", бежев: "бежевый", коричнев: "коричневый", голуб: "голубой",
  розов: "розовый", бордов: "бордовый", фиолетов: "фиолетовый", оливков: "оливковый",
  желт: "желтый", оранжев: "оранжевый",
};
const inflectedColor = /(^|-)([а-я]+?)(?:ая|яя|ое|ее|ые|ие|ую|юю|ого|его|ых|их)$/;

export interface CatalogSearchTerm {
  text: string;
  invalid?: boolean;
  color?: string;
  code?: string;
  compact?: string;
}

export function catalogSearchTerms(query: string): CatalogSearchTerm[] {
  const normalized = catalogText(query.trim());
  const words = normalized.split(/[\s,;()\u001f]+/u).filter(Boolean);
  if (normalized && !/[\p{L}\p{N}]/u.test(normalized)) return [{ text: normalized, invalid: true }];
  return [...new Set(words)].map((text) => {
    const color = text.replace(inflectedColor, (word, prefix: string, root: string) =>
      colors[root] ? prefix + colors[root] : word);
    const term: CatalogSearchTerm = { text };
    if (color !== text) term.color = color;
    // Lookalikes and missing punctuation are meaningful for references, not Russian words.
    const code = foldCodeLetters(text);
    if (/[0-9a-z]/.test(text) || /^[a-z]+$/.test(code)) {
      term.code = code;
      const compact = stripCodeSeparators(code);
      // An explicitly typed trailing slash in «03/» is part of the search.
      if (compact.length >= 2 && !CODE_SEPARATORS.includes(code[0]!) &&
          !CODE_SEPARATORS.includes(code[code.length - 1]!)) term.compact = compact;
    }
    return term;
  });
}

type SearchValue = string | null | undefined;

/** Match every term against attributes or an identifier of the same selectable item. */
export function matchesCatalogTerms(
  terms: CatalogSearchTerm[], fields: SearchValue[], identifiers: SearchValue[] = [],
): boolean {
  if (!terms.length) return true;
  if (terms.some(term => term.invalid)) return false;
  const text = catalogText([...fields, ...identifiers].join(" "));
  // A field separator prevents a compact match spanning two unrelated identifiers.
  const codes = foldCodeLetters(catalogText(identifiers.join("\u001f")));
  const compact = stripCodeSeparators(codes);
  return terms.every((term) =>
    text.includes(term.text) || (term.color && text.includes(term.color)) ||
    (term.code && codes.includes(term.code)) || (term.compact && compact.includes(term.compact))
  );
}

export function variationSearchRank(terms: CatalogSearchTerm[], variation: string): number {
  const code = foldCodeLetters(catalogText(variation.trim()));
  const compact = stripCodeSeparators(code);
  if (terms.some(t => t.code === code || t.text === code)) return 0;
  if (terms.some(t => t.compact === compact)) return 1;
  if (terms.some(t => (t.code && code.includes(t.code)) || (t.compact && compact.includes(t.compact)))) return 2;
  return 3;
}
