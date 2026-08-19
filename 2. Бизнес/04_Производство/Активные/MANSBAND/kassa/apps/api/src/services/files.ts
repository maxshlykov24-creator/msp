import { createHash } from "node:crypto";
import * as ms from "../clients/ms.js";
import { withIdempotency } from "../lib/idempotency.js";

// Единая точка загрузки фотофиксаций в МойСклад: паспорт (аренда), брак (возврат/обмен) и т.п.
// Идемпотентность: ключ = entity + хэш содержимого → ретрай не плодит дубли файлов.

export interface PhotoUpload {
  filename: string;
  contentBase64: string;
}

export async function attachPhoto(
  entityType: string,
  entityId: string,
  photo: PhotoUpload
): Promise<{ reused: boolean }> {
  const hash = createHash("sha256").update(photo.contentBase64).digest("hex").slice(0, 16);
  const key = `file:${entityType}:${entityId}:${hash}`;
  const { reused } = await withIdempotency(key, "file", async () => {
    await ms.uploadFile(entityType, entityId, photo.filename, photo.contentBase64);
    return { ok: true, filename: photo.filename };
  });
  return { reused };
}

export async function attachPhotos(
  entityType: string,
  entityId: string,
  photos: PhotoUpload[]
): Promise<void> {
  for (const p of photos) {
    await attachPhoto(entityType, entityId, p);
  }
}
