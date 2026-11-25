import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import {
  catalogThumbMode,
  prepareCatalogPhotos,
  productAsset,
  resolveCatalogPhoto,
  type PhotoIndex,
} from "../src/lib/catalogPhotoMatch.ts";

const hex = "0123456789abcdef";
const asset = (n: number) => hex[n]!.repeat(32);
const raw = JSON.parse(readFileSync(new URL("../src/data/catalogPhotos.json", import.meta.url), "utf8")) as PhotoIndex;
const prepared = prepareCatalogPhotos(raw);

function legacyProductAsset(index: PhotoIndex, name: string) {
  const label = name.normalize("NFC").trim().toLowerCase().replace(/\s+/g, " ");
  const start = label.lastIndexOf(" (");
  if (start < 0 || !label.endsWith(")")) return;
  const layouts = index.products[label.slice(0, start)];
  if (!layouts) return;
  const tokens = label.slice(start + 2, -1).split(",").map((token) => token.trim());
  const dimension = /^(?:[0-9]{1,3}(?:[./–-][0-9]{1,3})?|[2-9]?x{0,4}[sl]|m|one size|onesize)$/i;
  let found: string | undefined;
  for (const [mask, rules] of Object.entries(layouts)) {
    const positions = mask.split(",").map(Number);
    if (!positions.every((i) => dimension.test(tokens[i] ?? ""))) continue;
    const key = tokens.filter((_, i) => !positions.includes(i)).join("|");
    const match = rules[key];
    if (match) {
      if (found && match !== found) return;
      found = match;
    }
  }
  return found;
}

function syntheticName(base: string, mask: string, key: string) {
  const positions = new Set(mask.split(",").map(Number));
  const parts = key.split("|");
  const tokens: string[] = [];
  let next = 0;
  for (let i = 0; i < positions.size + parts.length; i++) tokens.push(positions.has(i) ? "48" : parts[next++]!);
  return `${base} (${tokens.join(", ")})`;
}

test("опубликованные костюмы и товары сохраняют свой кадр", () => {
  assert.equal(Object.keys(prepared.suits).length, Object.keys(raw.suits).length);
  for (const [key, expected] of Object.entries(raw.suits)) {
    const [line, variation, color, pattern, fit] = key.split("|");
    const suit = { line: line!, variation: variation!, color: color!, pattern: pattern!, fit: fit!, mixed: false };
    const exact = resolveCatalogPhoto(prepared, { name: "", suit });
    assert.equal(exact?.asset, expected, key);
    assert.equal(exact?.differentAppearance, false);
    const mixed = resolveCatalogPhoto(prepared, { name: "", suit: { ...suit, mixed: true } });
    assert.equal(mixed?.asset, expected, "mixed " + key);
    assert.equal(mixed?.differentAppearance, false);
  }

  let products = 0;
  for (const [base, layouts] of Object.entries(raw.products)) {
    for (const [mask, rules] of Object.entries(layouts)) {
      for (const [key, expected] of Object.entries(rules)) {
        const name = syntheticName(base, mask, key);
        assert.equal(productAsset(prepared, name), expected, name);
        assert.equal(legacyProductAsset(raw, name), expected, name);
        products++;
      }
    }
  }
  const expected = Object.values(raw.products)
    .reduce((sum, layouts) => sum + Object.values(layouts).reduce((inner, rules) => inner + Object.keys(rules).length, 0), 0);
  assert.equal(products, expected);
});

test("ё в цвете костюма попадает в тот же кадр, а не в иконку", () => {
  const black = raw.suits["regular|yi8863|черный|однотонный|oversize"];
  const suit = {
    mixed: true, line: "regular", variation: "yi8863", fit: "oversize",
    color: "чёрный", pattern: "однотонный",
  };
  const photo = resolveCatalogPhoto(prepared, { name: "", suit });
  assert.equal(photo?.asset, black);
  assert.equal(photo?.differentAppearance, false);
  assert.equal(catalogThumbMode(false, photo?.choices?.length ?? 0), "image");
});

test("чужая расцветка остаётся подписанным кадром и не исчезает", () => {
  const family = Object.entries(raw.suits).filter(([key]) => key.startsWith("regular|yi8863|") && key.endsWith("|oversize"));
  const photo = resolveCatalogPhoto(prepared, {
    name: "",
    suit: { mixed: true, line: "regular", variation: "yi8863", fit: "oversize", color: "розовый", pattern: "однотонный" },
  });
  assert.equal(photo?.differentAppearance, true);
  assert.equal(photo?.asset, family[0]?.[1]);
  assert.equal(photo?.choices?.[0]?.name.includes("розовый"), false);
  assert.equal(catalogThumbMode(false, photo?.choices?.length ?? 0), "image");
});

test("два разных кадра на одно написание не выбираются наугад", () => {
  const index: PhotoIndex = {
    products: { пальто: { "1": { "чёрный": asset(1), черный: asset(2) } } },
    suits: {
      "regular|v|чёрный|однотонный|slim fit": asset(3),
      "regular|v|черный|однотонный|slim fit": asset(4),
    },
  };
  const folded = prepareCatalogPhotos(index);
  assert.equal(productAsset(folded, "Пальто (чёрный, 48)"), undefined);
  assert.equal(resolveCatalogPhoto(folded, {
    name: "",
    suit: { mixed: false, line: "regular", variation: "v", color: "черный", pattern: "однотонный", fit: "slim fit" },
  }), null);
});

test("одинаковый кадр с ё и без ё остаётся", () => {
  const index: PhotoIndex = {
    products: { пальто: { "1": { "чёрный": asset(1), черный: asset(1) } } },
    suits: {},
  };
  assert.equal(productAsset(prepareCatalogPhotos(index), "Пальто (Чёрный, 48)"), asset(1));
});

test("разные кадры внутри одной группы не склеиваются", () => {
  const index: PhotoIndex = {
    products: {
      пиджак: { "1": { "черный": asset(1) } },
      брюки: { "1": { "черный": asset(2) } },
    },
    suits: {},
  };
  const folded = prepareCatalogPhotos(index);
  const jacket = "Пиджак (черный, 48)";
  assert.equal(resolveCatalogPhoto(folded, { name: jacket, groupNames: [jacket, "Брюки (черный, 48)"] }), null);
  assert.equal(resolveCatalogPhoto(folded, { name: jacket, groupNames: [jacket, jacket] })?.asset, asset(1));
});

test("иконка только когда оба файла недоступны и есть другие расцветки", () => {
  assert.equal(catalogThumbMode(false, 1), "image");
  assert.equal(catalogThumbMode(false, 4), "image");
  assert.equal(catalogThumbMode(true, 1), "hidden");
  assert.equal(catalogThumbMode(true, 4), "icon");
});
