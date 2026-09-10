import { CHANNELS } from "../data/mock";
import { opts, type SelectGroup, type SelectOption } from "../components/Select";

const CHANNEL_SPEC: Array<[string, readonly string[]]> = [
  ["Рекомендации", ["Совет", "Сарафан"]],
  ["Соцсети и контент", ["Телеграм канал", "Instagram наш", "Instagram не наш", "ВК", "Ютуб", "TikTok", "Pinterest"]],
  ["Реклама", ["Яндекс.Директ.Поиск", "Яндекс.Директ.РСЯ"]],
  ["Карты и площадки", ["Яндекс Карты", "2ГИС", "Гугл Карты", "Авито"]],
  ["Промокод", ["Промокод Чат невест", "Промокод Osoba", "Промокод Онливед"]],
];

function groupsFrom(spec: Array<[string, readonly string[]]>, all: readonly string[]): SelectGroup[] {
  const used = new Set<string>();
  const groups: SelectGroup[] = [];
  for (const [label, items] of spec) {
    const options = items.filter((item) => all.includes(item)).map((item) => ({ value: item, label: item }));
    options.forEach((item) => used.add(item.value));
    if (options.length) groups.push({ label, options });
  }
  const rest = all.filter((item) => !used.has(item));
  if (rest.length) groups.push({ label: "Другое", options: opts(...rest) });
  return groups;
}

/** Источник рекламы: те же значения, что в amoCRM, только разложены по смыслу. */
export const CHANNEL_GROUPS: SelectGroup[] = groupsFrom(CHANNEL_SPEC, CHANNELS);

const LOCATION_BUCKETS: Array<{ label: string; test: (name: string) => boolean }> = [
  { label: "Магазины", test: (name) => /бауманск|новокузнецк|пятницк/i.test(name) && !/полупарк/i.test(name) },
  { label: "Склад", test: (name) => /центральн/i.test(name) },
  { label: "Полупарки", test: (name) => /полупарк/i.test(name) },
  { label: "Прочее", test: () => true },
];

export function groupLocations(names: string[], empty?: SelectOption): SelectGroup[] {
  const used = new Set<string>();
  const groups: SelectGroup[] = [];
  if (empty) groups.push({ label: "", options: [empty] });
  for (const bucket of LOCATION_BUCKETS) {
    const options = names
      .filter((name) => !used.has(name) && bucket.test(name))
      .map((name) => ({ value: name, label: name }));
    options.forEach((item) => used.add(item.value));
    if (options.length) groups.push({ label: bucket.label, options });
  }
  return groups;
}
