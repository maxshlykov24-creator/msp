// Сопоставление карточки с уже опубликованным кадром.
// «ё» и «е» здесь одно написание: в индексе цвета без «ё», в живом каталоге встречаются оба.

export type PhotoIndex = {
  products: Record<string, Record<string, Record<string, string>>>;
  suits: Record<string, string>;
};

export type CatalogSuitPhoto = {
  line: string;
  variation: string;
  color: string | null;
  pattern: string | null;
  fit: string | null;
  mixed: boolean;
};

export type PhotoChoice = { asset: string; name: string };

export type ResolvedCatalogPhoto = {
  asset: string;
  jacket: boolean;
  choices?: PhotoChoice[];
  differentAppearance: boolean;
};

export type PreparedCatalogPhotos = {
  products: PhotoIndex["products"];
  suits: Record<string, string>;
  jacketPhotos: Set<string>;
  suitOptions: Map<string, Array<{ asset: string; color: string; pattern: string }>>;
};

const ASSET = /^[a-f0-9]{32}$/;
const dimension = /^(?:[0-9]{1,3}(?:[./–-][0-9]{1,3})?|[2-9]?x{0,4}[sl]|m|one size|onesize)$/i;

export function normalizePhotoText(value: string | null | undefined): string {
  return (value ?? "").normalize("NFC").trim().toLowerCase().replace(/ё/g, "е").replace(/\s+/g, " ");
}

function foldKey(key: string): string {
  return key.split("|").map((part) => normalizePhotoText(part)).join("|");
}

function remember(map: Map<string, string | null>, key: string, asset: string) {
  if (!ASSET.test(asset)) return;
  const prev = map.get(key);
  map.set(key, prev === undefined || prev === asset ? asset : null);
}

export function prepareCatalogPhotos(index: PhotoIndex): PreparedCatalogPhotos {
  const productRules = new Map<string, string | null>();
  for (const [base, layouts] of Object.entries(index.products)) {
    for (const [mask, rules] of Object.entries(layouts)) {
      for (const [key, asset] of Object.entries(rules)) {
        remember(productRules, `${normalizePhotoText(base)}\u0000${mask}\u0000${foldKey(key)}`, asset);
      }
    }
  }
  const products: PhotoIndex["products"] = {};
  for (const [id, asset] of productRules) {
    if (!asset) continue;
    const [base, mask, key] = id.split("\u0000");
    if (!base || !mask || key === undefined) continue;
    const layouts = products[base] ?? {};
    const rules = layouts[mask] ?? {};
    rules[key] = asset;
    layouts[mask] = rules;
    products[base] = layouts;
  }

  const suitRules = new Map<string, string | null>();
  for (const [key, asset] of Object.entries(index.suits)) {
    const parts = key.split("|");
    if (parts.length !== 5) continue;
    remember(suitRules, foldKey(key), asset);
  }
  const suits: Record<string, string> = {};
  for (const [key, asset] of suitRules) if (asset) suits[key] = asset;

  const suitOptions = new Map<string, Array<{ asset: string; color: string; pattern: string }>>();
  for (const [key, asset] of Object.entries(suits)) {
    const [line, variation, color, pattern, fit] = key.split("|");
    const family = [line, variation, fit].join("|");
    const options = suitOptions.get(family) ?? [];
    options.push({ asset, color: color ?? "", pattern: pattern ?? "" });
    suitOptions.set(family, options);
  }

  return { products, suits, jacketPhotos: new Set(Object.values(suits)), suitOptions };
}

export function productAsset(index: PreparedCatalogPhotos, name: string): string | undefined {
  const label = normalizePhotoText(name);
  const start = label.lastIndexOf(" (");
  if (start < 0 || !label.endsWith(")")) return;
  const layouts = index.products[label.slice(0, start)];
  if (!layouts) return;
  const tokens = label.slice(start + 2, -1).split(",").map((token) => token.trim());
  let asset: string | undefined;
  for (const [mask, rules] of Object.entries(layouts)) {
    const positions = mask.split(",").map(Number);
    if (!positions.every((i) => dimension.test(tokens[i] ?? ""))) continue;
    const key = tokens.filter((_, i) => !positions.includes(i)).map((token) => normalizePhotoText(token)).join("|");
    const match = rules[key];
    if (match) {
      if (asset && match !== asset) return;
      asset = match;
    }
  }
  return asset;
}

export function resolveCatalogPhoto(
  index: PreparedCatalogPhotos,
  input: { name: string; suit?: CatalogSuitPhoto; groupNames?: string[] },
): ResolvedCatalogPhoto | null {
  const suit = input.suit;
  if (suit?.mixed) {
    const options = index.suitOptions.get([suit.line, suit.variation, suit.fit].map(normalizePhotoText).join("|"));
    if (!options?.length) return null;
    const color = normalizePhotoText(suit.color);
    const pattern = normalizePhotoText(suit.pattern);
    const cover = options.find((option) => option.color === color && option.pattern === pattern);
    const ordered = cover ? [cover, ...options.filter((option) => option !== cover)] : options;
    return {
      asset: ordered[0]!.asset,
      jacket: true,
      differentAppearance: !cover,
      choices: ordered.map((option) => ({
        asset: option.asset,
        name: [option.color, option.pattern].filter(Boolean).join(" · "),
      })),
    };
  }

  const asset = suit
    ? index.suits[[suit.line, suit.variation, suit.color, suit.pattern, suit.fit].map(normalizePhotoText).join("|")]
    : productAsset(index, input.name);
  if (!asset || input.groupNames?.some((item) => productAsset(index, item) !== asset)) return null;
  return { asset, jacket: index.jacketPhotos.has(asset), differentAppearance: false };
}

/** Иконка не подменяет кадр, который можно открыть. Она остаётся, только когда оба файла этого кадра недоступны, а рядом есть другие расцветки. */
export function catalogThumbMode(largeFailed: boolean, choiceCount: number): "image" | "icon" | "hidden" {
  if (!largeFailed) return "image";
  return choiceCount >= 2 ? "icon" : "hidden";
}
