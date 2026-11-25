import type { PhotoReviewCase, PhotoReviewData, PhotoReviewImage } from "./types.js";

/** Регистр не важен, «/» и «-» один разделитель. Плюс и остальные буквы остаются разными. */
export function photoCodeKey(value: string): string {
  const stem = value.normalize("NFC").trim().replace(/\.arw$/i, "");
  return stem.toLowerCase().split(/\s+/).filter(Boolean).join(" ").replace(/\//g, "-");
}

const NEED_FRAME = "Нужен исходный кадр";
const CHECK_COLOR = "Сверить цвет и узор";
const CHECK_GARMENT = "Уточнить вид изделия и крой";
const CHECK_CARD = "Уточнить карточку и кадр";

type Manifest = Omit<PhotoReviewData, "comments">;

function part(value: string | undefined): string {
  return (value ?? "").normalize("NFC").trim().toLowerCase().split(/\s+/).filter(Boolean).join(" ");
}

function sameList(left: string[], right: string[]): boolean {
  return left.length === right.length && left.every((item, index) => item === right[index]);
}

function appearances(row: PhotoReviewCase): Set<string> {
  return new Set(row.variants.map(fields => fields.slice(1, 5).map(part).join("|")));
}

/** Один кадр, и его имя совпало с артикулом и со всеми вариациями каталога. Цвет и крой тоже одни. */
function frameIsSettled(row: PhotoReviewCase, own: string, photos: Record<string, PhotoReviewImage>): boolean {
  if (row.photoIds.length !== 1 || row.variants.length === 0) return false;
  const photo = photos[row.photoIds[0]!];
  if (!photo || photoCodeKey(photo.name) !== own) return false;
  if (!row.variants.every(fields => photoCodeKey(fields[0] ?? "") === own)) return false;
  return appearances(row).size === 1;
}

function openQuestion(row: PhotoReviewCase): string {
  if (row.variants.length === 0) return CHECK_CARD;
  const colors = new Set(row.variants.map(fields => part(fields[2])));
  const patterns = new Set(row.variants.map(fields => part(fields[3])));
  const models = new Set(row.variants.map(fields => part(fields[1])));
  const fits = new Set(row.variants.map(fields => part(fields[4])));
  if (colors.size > 1 || patterns.size > 1) return CHECK_COLOR;
  if (models.size > 1 || fits.size > 1) return CHECK_GARMENT;
  return CHECK_CARD;
}

/**
 * Кадр остаётся на карточке своего артикула.
 * Файл, чьё имя совпало с другим артикулом той же ячейки, с этой карточки снимается.
 * Если после этого артикул, кадр, цвет и крой одни, карточку не показываем: выбирать нечего.
 * Пустая карточка с тем же артикулом, что уже разобранная соседняя, тоже снимается.
 * Это случай `s23/37b2+` и файла `s23-37b2+.ARW`.
 */
export function settlePhotoReview<T extends Manifest>(data: T): T {
  const byCell = new Map<string, PhotoReviewCase[]>();
  for (const row of data.cases) {
    const list = byCell.get(row.cell) ?? [];
    list.push(row);
    byCell.set(row.cell, list);
  }

  const settled: PhotoReviewCase[] = [];
  const decidedPhotos = new Set<string>();
  for (const row of data.cases) {
    const own = photoCodeKey(row.code);
    const siblingKeys = new Set(
      (byCell.get(row.cell) ?? [])
        .filter(other => other.id !== row.id)
        .map(other => photoCodeKey(other.code)),
    );
    const photoIds = row.photoIds.filter(id => {
      const photo = data.photos[id];
      if (!photo) return false;
      const key = photoCodeKey(photo.name);
      return key === own || !siblingKeys.has(key);
    });
    if (sameList(photoIds, row.photoIds)) {
      settled.push(row);
      continue;
    }
    const next = { ...row, photoIds };
    if (photoIds.length === 0) {
      settled.push({ ...next, reason: NEED_FRAME });
      continue;
    }
    const foreign = photoIds.some(id => photoCodeKey(data.photos[id]!.name) !== own);
    if (foreign) {
      settled.push(next);
      continue;
    }
    if (frameIsSettled(next, own, data.photos)) {
      for (const id of photoIds) decidedPhotos.add(id);
      continue;
    }
    settled.push({ ...next, reason: openQuestion(next) });
  }

  const byCode = new Map<string, PhotoReviewCase[]>();
  for (const row of settled) {
    const key = photoCodeKey(row.code);
    const list = byCode.get(key) ?? [];
    list.push(row);
    byCode.set(key, list);
  }
  const hidden = new Set<string>();
  for (const group of byCode.values()) {
    const owners = group.filter(row =>
      row.photoIds.some(id => photoCodeKey(data.photos[id]?.name ?? "") === photoCodeKey(row.code)));
    for (const blank of group) {
      if (blank.photoIds.length > 0) continue;
      const signature = JSON.stringify(blank.variants);
      if (owners.some(owner => JSON.stringify(owner.variants) === signature)) hidden.add(blank.id);
    }
  }

  const cases = settled.filter(row => !hidden.has(row.id));
  const used = new Set(cases.flatMap(row => row.photoIds));
  const unpublished = data.unpublished.filter(row => !decidedPhotos.has(row.id) || used.has(row.id));
  return { ...data, cases, unpublished };
}
