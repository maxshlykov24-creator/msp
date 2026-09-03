// Реальная фотофиксация: захват файла (камера/галерея) → превью (dataURL) → base64 для API.

export interface PhotoUploadItem {
  filename: string;
  contentBase64: string;
  target?: "shipment" | "order" | "return";
}

/** Локальное представление фото в форме — с превью для UI. */
export interface PhotoAttachment {
  id: string;
  filename: string;
  dataUrl: string; // data:image/...;base64,xxx — используется и для превью, и для отправки
}

function fileToDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

export async function filesToAttachments(files: FileList | File[]): Promise<PhotoAttachment[]> {
  const arr = Array.from(files);
  const out: PhotoAttachment[] = [];
  for (const f of arr) {
    if (!f.type.startsWith("image/")) continue;
    const dataUrl = await fileToDataUrl(f);
    out.push({ id: crypto.randomUUID(), filename: f.name || `photo-${Date.now()}.jpg`, dataUrl });
  }
  return out;
}

export function attachmentsToUpload(
  photos: PhotoAttachment[],
  target?: PhotoUploadItem["target"]
): PhotoUploadItem[] {
  return photos.map((p) => ({
    filename: p.filename,
    contentBase64: p.dataUrl.split(",")[1] ?? "",
    target,
  }));
}
